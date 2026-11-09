"""Native Cohere API provider adapter."""

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

_FALLBACK_COHERE_MODELS = [
    ModelInfo(id="command-r-08-2024", display_name="Command R", supports_generation=True),
    ModelInfo(id="command-r-plus-08-2024", display_name="Command R+", supports_generation=True),
    ModelInfo(id="command-light", display_name="Command Light", supports_generation=True),
]


class CohereProvider(BaseProvider):
    """Native adapter for Cohere API."""

    name = "cohere"
    display_name = "Cohere"
    base_url = "https://api.cohere.com/v2"
    docs_url = "https://docs.cohere.com/docs"
    models_docs_url = "https://docs.cohere.com/docs/models"
    rate_limits_docs_url = "https://docs.cohere.com/docs/rate-limits"
    env_var_name = "CO_API_KEY"
    default_test_model = "command-r-08-2024"

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        return {
            "Authorization": f"Bearer {cleaned}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> tuple[str, str]:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                msg = str(payload.get("message") or "")
                err_type = ""
                return redact_text(msg), redact_text(err_type)
        except Exception:  # noqa: S110
            pass
        snippet = (response.text or "")[:240].strip()
        return redact_text(snippet or f"HTTP {response.status_code}"), ""

    @staticmethod
    def _extract_retry_after(response: httpx.Response) -> int | None:
        for header_name in ("retry-after", "x-ratelimit-reset"):
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
            exc_msg = redact_text(str(exc)) if exc else "No response from Cohere API"
            if isinstance(exc, httpx.TimeoutException):
                return ProviderErrorClassification(
                    state=HealthState.NETWORK_ERROR,
                    error_category="timeout",
                    http_status=None,
                    sanitized_message=f"Cohere API request timed out: {exc_msg}",
                    is_retryable=True,
                )
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=f"Cohere network error: {exc_msg}",
                is_retryable=True,
            )

        status = response.status_code
        msg, err_type = self._extract_error_details(response)
        retry_after = self._extract_retry_after(response)
        combined = f"{msg} {err_type}".lower()

        if status == 401 or "unauthorized" in combined or "invalid api token" in combined:
            return ProviderErrorClassification(
                state=HealthState.AUTH_FAILED,
                error_category="authentication_error",
                http_status=status,
                sanitized_message=f"Cohere authentication failed: {msg}",
                is_retryable=False,
            )

        if status == 403:
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED,
                error_category="permission_denied",
                http_status=status,
                sanitized_message=f"Cohere permission denied: {msg}",
                is_retryable=False,
            )

        if status == 404:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="not_found",
                http_status=status,
                sanitized_message=f"Cohere model or endpoint not found: {msg}",
                is_retryable=False,
            )

        if status == 429:
            is_quota = "quota" in combined or "trial" in combined
            return ProviderErrorClassification(
                state=HealthState.QUOTA_EXHAUSTED if is_quota else HealthState.RATE_LIMITED,
                error_category="quota_exhausted" if is_quota else "rate_limit_error",
                http_status=429,
                sanitized_message=f"Cohere {'quota exhausted' if is_quota else 'rate limit exceeded'}: {msg}",
                retry_after_seconds=retry_after,
                is_retryable=not is_quota,
            )

        if status in (500, 502, 503, 504):
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status,
                sanitized_message=f"Cohere service unavailable (HTTP {status}): {msg}",
                retry_after_seconds=retry_after or 30,
                is_retryable=True,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category=f"http_{status}",
            http_status=status,
            sanitized_message=f"Cohere returned HTTP {status}: {msg}",
            is_retryable=False,
        )

    def list_models(self, credential: str) -> ModelDiscoveryResult:
        url = "https://api.cohere.com/v1/models"
        headers = self._build_headers(credential)
        resp, exc, latency_ms = self._request_with_retry("GET", url, headers=headers)

        if exc is not None or resp is None or resp.status_code != 200:
            classification = self.classify_error(resp, exc)
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
            models_data = payload.get("models")
            if isinstance(models_data, list):
                for item in models_data:
                    if isinstance(item, dict) and "name" in item:
                        mid = str(item["name"])
                        models.append(
                            ModelInfo(
                                id=mid,
                                display_name=mid,
                                supports_generation=True,
                            )
                        )
        except Exception:  # noqa: S110
            models = _FALLBACK_COHERE_MODELS

        return ModelDiscoveryResult(
            success=True,
            state=HealthState.HEALTHY,
            models=models or _FALLBACK_COHERE_MODELS,
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
                else "Cohere model discovery failed."
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
                sanitized_message=f"Cohere authentication succeeded ({len(discovery.models)} models available).",
            )

        # Minimal generation test via /v2/chat
        gen_url = f"{self.base_url}/chat"
        headers = self._build_headers(credential)
        payload: dict[str, Any] = {
            "model": model_to_test,
            "messages": [{"role": "user", "content": "ping"}],
        }

        resp, exc, gen_latency_ms = self._request_with_retry(
            "POST", gen_url, headers=headers, json_body=payload
        )
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
                sanitized_message=f"Auth succeeded, but Cohere test failed: {classification.sanitized_message}",
            )

        return ConnectionTestResult(
            success=True,
            state=HealthState.HEALTHY,
            auth_ok=True,
            models_discovered=len(discovery.models),
            models=discovery.models,
            functional_tested=True,
            functional_ok=True,
            model_tested=model_to_test,
            latency_ms=total_latency,
            sanitized_message=f"Cohere generation test succeeded using {model_to_test}.",
        )
