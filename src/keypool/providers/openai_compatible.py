"""Generic and specialized OpenAI-compatible REST API provider adapter."""

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
from keypool.providers.capabilities import PROVIDER_CATALOG
from keypool.security.redaction import redact_text, register_secret_for_redaction
from keypool.storage.models import HealthState


class OpenAICompatibleProvider(BaseProvider):
    """Adapter for endpoints complying with the OpenAI REST API specification."""

    def __init__(
        self,
        name: str = "openai_compatible",
        display_name: str | None = None,
        base_url: str | None = None,
        env_var_name: str | None = None,
        default_test_model: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        timeout_seconds: float = 15.0,
        connect_timeout_seconds: float = 5.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        http_client: httpx.Client | None = None,
        requires_key: bool = True,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            http_client=http_client,
        )
        self.name = name.strip().lower()
        meta = PROVIDER_CATALOG.get(self.name)

        self.display_name = display_name or (meta.display_name if meta else name.capitalize())
        self.base_url = (
            base_url or (meta.base_url if meta else "http://127.0.0.1:8000/v1")
        ).rstrip("/")
        self.env_var_name = env_var_name or (
            meta.env_var_name if meta else f"{self.name.upper()}_API_KEY"
        )
        self.default_test_model = default_test_model or (
            meta.default_test_model if meta else "default"
        )
        self.docs_url = meta.docs_url if meta else "https://platform.openai.com/docs/api-reference"
        self.models_docs_url = meta.models_docs_url or self.docs_url if meta else self.docs_url
        self.rate_limits_docs_url = (
            meta.rate_limits_docs_url or self.docs_url if meta else self.docs_url
        )

        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.requires_key = requires_key

    def _build_headers(self, credential: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        cleaned = credential.strip()
        if cleaned:
            register_secret_for_redaction(cleaned)
            if self.auth_scheme:
                headers[self.auth_header] = f"{self.auth_scheme} {cleaned}".strip()
            else:
                headers[self.auth_header] = cleaned
        elif self.requires_key:
            headers[self.auth_header] = f"{self.auth_scheme} placeholder".strip()
        return headers

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> tuple[str, str, str]:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    msg = str(err.get("message") or "")
                    err_type = str(err.get("type") or "")
                    err_code = str(err.get("code") or "")
                    return redact_text(msg), redact_text(err_type), redact_text(err_code)
                if isinstance(err, str):
                    return redact_text(err), "", ""
                if "message" in payload:
                    return redact_text(str(payload.get("message") or "")), "", ""
        except Exception:  # noqa: S110
            pass
        snippet = (response.text or "")[:240].strip()
        return redact_text(snippet or f"HTTP {response.status_code}"), "", ""

    @staticmethod
    def _extract_retry_after(response: httpx.Response) -> int | None:
        for header_name in (
            "retry-after",
            "x-ratelimit-reset-requests",
            "x-ratelimit-reset-tokens",
            "x-ratelimit-reset",
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
            exc_msg = redact_text(str(exc)) if exc else f"No response from {self.display_name}"
            if isinstance(exc, httpx.TimeoutException):
                return ProviderErrorClassification(
                    state=HealthState.NETWORK_ERROR,
                    error_category="timeout",
                    http_status=None,
                    sanitized_message=f"{self.display_name} API request timed out: {exc_msg}",
                    is_retryable=True,
                )
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=f"{self.display_name} network error: {exc_msg}",
                is_retryable=True,
            )

        status = response.status_code
        msg, err_type, err_code = self._extract_error_details(response)
        retry_after = self._extract_retry_after(response)
        combined = f"{msg} {err_type} {err_code}".lower()

        if status in (401,):
            return ProviderErrorClassification(
                state=HealthState.AUTH_FAILED,
                error_category="authentication_failed",
                http_status=status,
                sanitized_message=f"Authentication failed for {self.display_name}: {msg}",
                is_retryable=False,
            )

        if status == 403:
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED,
                error_category="permission_denied",
                http_status=status,
                sanitized_message=f"Permission denied on {self.display_name}: {msg}",
                is_retryable=False,
            )

        if status == 404:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="not_found",
                http_status=status,
                sanitized_message=f"Resource or model not found on {self.display_name}: {msg}",
                is_retryable=False,
            )

        if status == 429:
            is_quota = any(
                term in combined
                for term in (
                    "quota",
                    "insufficient_quota",
                    "credit",
                    "balance",
                    "exceeded your current quota",
                )
            )
            return ProviderErrorClassification(
                state=HealthState.QUOTA_EXHAUSTED if is_quota else HealthState.RATE_LIMITED,
                error_category="quota_exhausted" if is_quota else "rate_limit_exceeded",
                http_status=429,
                sanitized_message=f"{self.display_name} {'quota exhausted' if is_quota else 'rate limit exceeded'}: {msg}",
                retry_after_seconds=retry_after,
                is_retryable=not is_quota,
            )

        if status in (500, 502, 503, 504):
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status,
                sanitized_message=f"{self.display_name} service unavailable (HTTP {status}): {msg}",
                retry_after_seconds=retry_after or 30,
                is_retryable=True,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category=f"http_{status}",
            http_status=status,
            sanitized_message=f"{self.display_name} returned HTTP {status}: {msg}",
            is_retryable=False,
        )

    def list_models(self, credential: str) -> ModelDiscoveryResult:
        url = f"{self.base_url}/models"
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
            data = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item:
                        mid = str(item["id"])
                        models.append(
                            ModelInfo(
                                id=mid,
                                display_name=str(item.get("name") or mid),
                                supports_generation=True,
                                owned_by=str(item.get("owned_by") or "") or None,
                            )
                        )
        except Exception as json_err:
            return ModelDiscoveryResult(
                success=False,
                state=HealthState.UNKNOWN,
                models=[],
                classification=ProviderErrorClassification(
                    state=HealthState.UNKNOWN,
                    error_category="parse_error",
                    http_status=resp.status_code,
                    sanitized_message=f"Failed to parse model list from {self.display_name}: {json_err}",
                ),
                latency_ms=latency_ms,
            )

        return ModelDiscoveryResult(
            success=True,
            state=HealthState.HEALTHY,
            models=models,
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
                else f"Model discovery failed for {self.display_name}"
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
        # If preferred model is not in discovered list and models are available, pick first
        if discovery.models and not any(m.id == model_to_test for m in discovery.models):
            model_to_test = discovery.models[0].id

        if not functional:
            return ConnectionTestResult(
                success=True,
                state=HealthState.HEALTHY,
                auth_ok=True,
                models_discovered=len(discovery.models),
                models=discovery.models,
                functional_tested=False,
                latency_ms=discovery.latency_ms,
                sanitized_message=f"Connected to {self.display_name} successfully ({len(discovery.models)} models available).",
            )

        # Minimal generation test
        gen_url = f"{self.base_url}/chat/completions"
        headers = self._build_headers(credential)
        payload: dict[str, Any] = {
            "model": model_to_test,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
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
                sanitized_message=f"Auth succeeded, but generation test failed: {classification.sanitized_message}",
            )

        observed_tokens: int | None = None
        try:
            body = resp.json()
            usage = body.get("usage")
            if isinstance(usage, dict) and "total_tokens" in usage:
                observed_tokens = int(usage["total_tokens"])
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
            sanitized_message=f"Full test succeeded on {self.display_name} using {model_to_test}.",
        )
