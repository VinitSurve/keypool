"""Native Replicate API provider adapter."""

from __future__ import annotations

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

_FALLBACK_REPLICATE_MODELS = [
    ModelInfo(
        id="meta/meta-llama-3-8b-instruct",
        display_name="Llama 3 8B Instruct",
        supports_generation=True,
    ),
    ModelInfo(
        id="meta/meta-llama-3-70b-instruct",
        display_name="Llama 3 70B Instruct",
        supports_generation=True,
    ),
]


class ReplicateProvider(BaseProvider):
    """Native adapter for Replicate inference platform."""

    name = "replicate"
    display_name = "Replicate"
    base_url = "https://api.replicate.com/v1"
    docs_url = "https://replicate.com/docs"
    models_docs_url = "https://replicate.com/models"
    rate_limits_docs_url = "https://replicate.com/docs/reference/http#rate-limits"
    env_var_name = "REPLICATE_API_TOKEN"
    default_test_model = "meta/meta-llama-3-8b-instruct"

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        return {
            "Authorization": f"Token {cleaned}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("title") or payload.get("error")
                if detail:
                    return redact_text(str(detail))
        except Exception:  # noqa: S110
            pass
        snippet = (response.text or "")[:240].strip()
        return redact_text(snippet or f"HTTP {response.status_code}")

    @staticmethod
    def _extract_retry_after(response: httpx.Response) -> int | None:
        raw = response.headers.get("retry-after")
        return parse_retry_duration_seconds(raw)

    def classify_error(
        self,
        response: httpx.Response | None,
        exc: Exception | None = None,
    ) -> ProviderErrorClassification:
        if exc is not None or response is None:
            exc_msg = redact_text(str(exc)) if exc else "No response from Replicate API"
            if isinstance(exc, httpx.TimeoutException):
                return ProviderErrorClassification(
                    state=HealthState.NETWORK_ERROR,
                    error_category="timeout",
                    http_status=None,
                    sanitized_message=f"Replicate API request timed out: {exc_msg}",
                    is_retryable=True,
                )
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=f"Replicate network error: {exc_msg}",
                is_retryable=True,
            )

        status = response.status_code
        msg = self._extract_error_details(response)
        retry_after = self._extract_retry_after(response)
        lower = msg.lower()

        if status in (401, 403) or "unauthenticated" in lower or "invalid token" in lower:
            return ProviderErrorClassification(
                state=HealthState.AUTH_FAILED,
                error_category="authentication_error",
                http_status=status,
                sanitized_message=f"Replicate authentication failed: {msg}",
                is_retryable=False,
            )

        if status == 404:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="not_found",
                http_status=status,
                sanitized_message=f"Replicate resource not found: {msg}",
                is_retryable=False,
            )

        if status == 429:
            return ProviderErrorClassification(
                state=HealthState.RATE_LIMITED,
                error_category="rate_limit_error",
                http_status=429,
                sanitized_message=f"Replicate rate limit exceeded: {msg}",
                retry_after_seconds=retry_after,
                is_retryable=True,
            )

        if status in (500, 502, 503, 504):
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status,
                sanitized_message=f"Replicate service unavailable (HTTP {status}): {msg}",
                retry_after_seconds=retry_after or 30,
                is_retryable=True,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category=f"http_{status}",
            http_status=status,
            sanitized_message=f"Replicate returned HTTP {status}: {msg}",
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
            results = payload.get("results") if isinstance(payload, dict) else payload
            if isinstance(results, list):
                for item in results:
                    if isinstance(item, dict):
                        owner = item.get("owner")
                        name = item.get("name")
                        if owner and name:
                            mid = f"{owner}/{name}"
                            models.append(
                                ModelInfo(id=mid, display_name=mid, supports_generation=True)
                            )
        except Exception:  # noqa: S110
            models = _FALLBACK_REPLICATE_MODELS

        return ModelDiscoveryResult(
            success=True,
            state=HealthState.HEALTHY,
            models=models or _FALLBACK_REPLICATE_MODELS,
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
                else "Replicate model discovery failed."
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

        return ConnectionTestResult(
            success=True,
            state=HealthState.HEALTHY,
            auth_ok=True,
            models_discovered=len(discovery.models),
            models=discovery.models,
            functional_tested=False,
            latency_ms=discovery.latency_ms,
            sanitized_message=f"Replicate authentication succeeded ({len(discovery.models)} models accessible).",
        )
