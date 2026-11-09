"""Native Anthropic API provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from keypool.providers.base import (
    BaseProvider,
    ConnectionTestResult,
    ModelDiscoveryResult,
    ModelInfo,
    ProviderErrorClassification,
    parse_retry_duration_seconds,
)
from keypool.security.redaction import redact_text, register_secret_for_redaction
from keypool.storage.models import HealthState

_FALLBACK_ANTHROPIC_MODELS = [
    ModelInfo(id="claude-3-5-haiku-latest", display_name="Claude 3.5 Haiku", supports_generation=True),
    ModelInfo(id="claude-3-5-sonnet-latest", display_name="Claude 3.5 Sonnet", supports_generation=True),
    ModelInfo(id="claude-3-opus-latest", display_name="Claude 3 Opus", supports_generation=True),
]


class AnthropicProvider(BaseProvider):
    """Native adapter for Anthropic Claude API."""

    name = "anthropic"
    display_name = "Anthropic"
    base_url = "https://api.anthropic.com/v1"
    docs_url = "https://docs.anthropic.com/en/docs/welcome"
    models_docs_url = "https://docs.anthropic.com/en/docs/about-claude/models"
    rate_limits_docs_url = "https://docs.anthropic.com/en/api/rate-limits"
    env_var_name = "ANTHROPIC_API_KEY"
    default_test_model = "claude-3-5-haiku-latest"

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        return {
            "x-api-key": cleaned,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> tuple[str, str]:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    msg = str(err.get("message") or "")
                    err_type = str(err.get("type") or "")
                    return redact_text(msg), redact_text(err_type)
                if isinstance(err, str):
                    return redact_text(err), ""
        except Exception:  # noqa: S110
            pass
        snippet = (response.text or "")[:240].strip()
        return redact_text(snippet or f"HTTP {response.status_code}"), ""

    @staticmethod
    def _extract_retry_after(response: httpx.Response) -> int | None:
        for header_name in (
            "retry-after",
            "anthropic-ratelimit-requests-reset",
            "anthropic-ratelimit-tokens-reset",
        ):
            raw = response.headers.get(header_name)
            parsed = parse_retry_duration_seconds(raw)
            if parsed is not None:
                return parsed
        return None

    def classify_error(
        self,
        response: httpx.Response | None,
        exc: Exception | None = None,
    ) -> ProviderErrorClassification:
        if exc is not None or response is None:
            exc_msg = redact_text(str(exc)) if exc else "No response from Anthropic API"
            if isinstance(exc, httpx.TimeoutException):
                return ProviderErrorClassification(
                    state=HealthState.NETWORK_ERROR,
                    error_category="timeout",
                    http_status=None,
                    sanitized_message=f"Anthropic API request timed out: {exc_msg}",
                    is_retryable=True,
                )
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=f"Anthropic network error: {exc_msg}",
                is_retryable=True,
            )

        status = response.status_code
        msg, err_type = self._extract_error_details(response)
        retry_after = self._extract_retry_after(response)
        combined = f"{msg} {err_type}".lower()

        if status == 401 or "authentication_error" in combined or "invalid x-api-key" in combined:
            return ProviderErrorClassification(
                state=HealthState.AUTH_FAILED,
                error_category="authentication_error",
                http_status=status,
                sanitized_message=f"Anthropic authentication failed: {msg}",
                is_retryable=False,
            )

        if status == 403 or "permission_error" in combined:
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED,
                error_category="permission_error",
                http_status=status,
                sanitized_message=f"Anthropic permission denied: {msg}",
                is_retryable=False,
            )

        if status == 404 or "not_found_error" in combined:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="not_found_error",
                http_status=status,
                sanitized_message=f"Anthropic resource not found: {msg}",
                is_retryable=False,
            )

        if status == 429 or "rate_limit_error" in combined:
            is_quota = "quota" in combined or "credit" in combined
            return ProviderErrorClassification(
                state=HealthState.QUOTA_EXHAUSTED if is_quota else HealthState.RATE_LIMITED,
                error_category="quota_exhausted" if is_quota else "rate_limit_error",
                http_status=429,
                sanitized_message=f"Anthropic {'quota exhausted' if is_quota else 'rate limit exceeded'}: {msg}",
                retry_after_seconds=retry_after,
                is_retryable=not is_quota,
            )

        if status in (500, 502, 503, 504, 529) or "overloaded_error" in combined:
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="overloaded_error" if "overloaded" in combined else "provider_unavailable",
                http_status=status,
                sanitized_message=f"Anthropic service unavailable (HTTP {status}): {msg}",
                retry_after_seconds=retry_after or 30,
                is_retryable=True,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category=f"http_{status}",
            http_status=status,
            sanitized_message=f"Anthropic returned HTTP {status}: {msg}",
            is_retryable=False,
        )

    def list_models(self, credential: str) -> ModelDiscoveryResult:
        url = f"{self.base_url}/models"
        headers = self._build_headers(credential)
        resp, exc, latency_ms = self._request_with_retry("GET", url, headers=headers)

        if exc is not None or resp is None or resp.status_code != 200:
            classification = self.classify_error(resp, exc)
            # If models endpoint is not yet enabled for this key (404/400), fall back to known models if auth is verified
            if resp is not None and resp.status_code == 404:
                return ModelDiscoveryResult(
                    success=True,
                    state=HealthState.HEALTHY,
                    models=_FALLBACK_ANTHROPIC_MODELS,
                    latency_ms=latency_ms,
                )
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                classification=classification,
                latency_ms=latency_ms,
            )

        models: list[ModelInfo] = []
        try:
            payload = resp.json()
            data = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item:
                        mid = str(item["id"])
                        models.append(
                            ModelInfo(
                                id=mid,
                                display_name=str(item.get("display_name") or mid),
                                supports_generation=True,
                            )
                        )
        except Exception:  # noqa: S110
            models = _FALLBACK_ANTHROPIC_MODELS

        return ModelDiscoveryResult(
            success=True,
            state=HealthState.HEALTHY,
            models=models or _FALLBACK_ANTHROPIC_MODELS,
            latency_ms=latency_ms,
        )

    def test_connection(
        self,
        credential: str,
        *,
        functional: bool = False,
        preferred_model: str | None = None,
    ) -> ConnectionTestResult:
        discovery = self.list_models(credential)
        if not discovery.success:
            err = (
                discovery.classification.sanitized_message
                if discovery.classification
                else "Anthropic model discovery failed."
            )
            return ConnectionTestResult(
                success=False,
                state=discovery.state,
                auth_ok=False,
                models_discovered=0,
                models=[],
                latency_ms=discovery.latency_ms,
                classification=discovery.classification,
                sanitized_message=err,
            )

        model_to_test = preferred_model or self.default_test_model

        if not functional:
            return ConnectionTestResult(
                success=True,
                state=HealthState.HEALTHY,
                auth_ok=True,
                models_discovered=len(discovery.models),
                models=discovery.models,
                functional_tested=False,
                latency_ms=discovery.latency_ms,
                sanitized_message=f"Anthropic authentication succeeded ({len(discovery.models)} models available).",
            )

        # Minimal generation test via /v1/messages
        gen_url = f"{self.base_url}/messages"
        headers = self._build_headers(credential)
        payload: dict[str, Any] = {
            "model": model_to_test,
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "ping"}],
        }

        resp, exc, gen_latency_ms = self._request_with_retry("POST", gen_url, headers=headers, json_body=payload)
        total_latency = round(discovery.latency_ms + gen_latency_ms, 2)

        if exc is not None or resp is None or resp.status_code != 200:
            classification = self.classify_error(resp, exc)
            return ConnectionTestResult(
                success=False,
                state=classification.state,
                auth_ok=True,
                models_discovered=len(discovery.models),
                models=discovery.models,
                functional_tested=True,
                functional_ok=False,
                model_tested=model_to_test,
                latency_ms=total_latency,
                classification=classification,
                sanitized_message=f"Auth succeeded, but generation test failed: {classification.sanitized_message}",
            )

        observed_tokens: int | None = None
        try:
            body = resp.json()
            usage = body.get("usage")
            if isinstance(usage, dict):
                in_tok = int(usage.get("input_tokens") or 0)
                out_tok = int(usage.get("output_tokens") or 0)
                observed_tokens = in_tok + out_tok
        except Exception:  # noqa: S110
            pass

        return ConnectionTestResult(
            success=True,
            state=HealthState.HEALTHY,
            auth_ok=True,
            models_discovered=len(discovery.models),
            models=discovery.models,
            functional_tested=True,
            functional_ok=True,
            model_tested=model_to_test,
            observed_tokens=observed_tokens,
            latency_ms=total_latency,
            sanitized_message=f"Anthropic generation test succeeded using {model_to_test}.",
        )
