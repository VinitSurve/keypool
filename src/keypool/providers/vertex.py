"""Google Cloud Vertex AI provider adapter."""

from __future__ import annotations

import contextlib

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


class GoogleVertexProvider(BaseProvider):
    """Adapter for Google Cloud Vertex AI foundation models."""

    name = "vertex"
    display_name = "Google Vertex AI"
    base_url = "https://us-central1-aiplatform.googleapis.com/v1"
    docs_url = "https://cloud.google.com/vertex-ai/docs"
    rate_limits_docs_url = "https://cloud.google.com/vertex-ai/docs/quotas"
    models_docs_url = "https://cloud.google.com/vertex-ai/docs/generative-ai/model-garden"
    env_var_name = "VERTEX_API_KEY"
    default_test_model = "gemini-1.5-flash"

    def __init__(
        self,
        name: str = "vertex",
        display_name: str = "Google Vertex AI",
        base_url: str | None = None,
        env_var_name: str = "VERTEX_API_KEY",
        default_test_model: str = "gemini-1.5-flash",
        timeout_seconds: float = 15.0,
        connect_timeout_seconds: float = 5.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        http_client: httpx.Client | None = None,
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
        self.display_name = display_name or (meta.display_name if meta else "Google Vertex AI")
        self.base_url = (base_url or (meta.base_url if meta else self.base_url)).rstrip("/")
        self.env_var_name = env_var_name or (meta.env_var_name if meta else self.env_var_name)
        self.default_test_model = default_test_model or (
            meta.default_test_model if meta else self.default_test_model
        )

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Support both Google OAuth Bearer token and direct API keys
        if cleaned.startswith("ya29.") or cleaned.startswith("Bearer "):
            token = cleaned if cleaned.startswith("Bearer ") else f"Bearer {cleaned}"
            headers["Authorization"] = token
        else:
            headers["x-goog-api-key"] = cleaned
            headers["Authorization"] = f"Bearer {cleaned}"
        return headers

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> tuple[str, str, int | None]:
        with contextlib.suppress(Exception):
            payload = response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    msg = str(err.get("message") or "")
                    status_str = str(err.get("status") or "")
                    code = err.get("code")
                    return redact_text(msg), status_str, int(code) if code else None
        return redact_text(response.text[:300]), "", None

    def classify_error(
        self,
        response: httpx.Response | None,
        exception: Exception | None = None,
    ) -> ProviderErrorClassification:
        if response is None:
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=redact_text(str(exception) or "Network connection failed"),
                retry_after_seconds=5,
                is_retryable=True,
            )

        status_code = response.status_code
        msg, rpc_status, _ = self._extract_error_details(response)
        retry_after = parse_retry_duration_seconds(response.headers.get("retry-after"))

        if status_code in (401, 403) or rpc_status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
            is_perm = status_code == 403 or rpc_status == "PERMISSION_DENIED"
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED if is_perm else HealthState.AUTH_FAILED,
                error_category="permission_denied" if is_perm else "auth_failed",
                http_status=status_code,
                sanitized_message=msg or "Vertex AI authentication or IAM permission denied",
                is_retryable=False,
            )

        if status_code == 429 or rpc_status == "RESOURCE_EXHAUSTED":
            is_quota = "quota" in msg.lower() or "limit" in msg.lower()
            return ProviderErrorClassification(
                state=HealthState.QUOTA_EXHAUSTED if is_quota else HealthState.RATE_LIMITED,
                error_category="quota_exhausted" if is_quota else "rate_limited",
                http_status=status_code,
                sanitized_message=msg or "Vertex AI rate limit or quota exhausted",
                retry_after_seconds=retry_after or 60,
                is_retryable=True,
            )

        if status_code == 404 or rpc_status == "NOT_FOUND":
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="model_not_found",
                http_status=status_code,
                sanitized_message=msg or "Vertex AI publisher model not found",
                is_retryable=False,
            )

        if status_code >= 500:
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status_code,
                sanitized_message=msg or f"Vertex AI internal server error (HTTP {status_code})",
                retry_after_seconds=retry_after or 10,
                is_retryable=True,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category="http_error",
            http_status=status_code,
            sanitized_message=msg or f"HTTP {status_code} error from Vertex AI",
            is_retryable=False,
        )

    def list_models(self, credential: str) -> ModelDiscoveryResult:
        headers = self._build_headers(credential)
        endpoint = f"{self.base_url}/publishers/google/models"
        response, exc, duration = self._request_with_retry("GET", endpoint, headers=headers)

        if exc is not None or response is None or response.status_code != 200:
            classification = self.classify_error(response, exc)
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                latency_ms=duration,
                classification=classification,
            )

        models: list[ModelInfo] = []
        try:
            payload = response.json()
            raw_models = payload.get("models") if isinstance(payload, dict) else []
            if isinstance(raw_models, list):
                for item in raw_models:
                    if isinstance(item, dict) and "name" in item:
                        mid = str(item["name"]).split("/")[-1]
                        dname = str(item.get("displayName") or mid)
                        models.append(
                            ModelInfo(
                                id=mid,
                                display_name=dname,
                                supports_generation=True,
                                owned_by="google",
                            )
                        )
        except Exception as parse_exc:
            return ModelDiscoveryResult(
                success=False,
                state=HealthState.PROVIDER_UNAVAILABLE,
                models=[],
                latency_ms=duration,
                classification=ProviderErrorClassification(
                    state=HealthState.PROVIDER_UNAVAILABLE,
                    error_category="malformed_response",
                    http_status=response.status_code,
                    sanitized_message=f"Failed to parse Vertex models: {parse_exc}",
                ),
            )

        return ModelDiscoveryResult(
            success=True,
            state=HealthState.HEALTHY,
            models=models,
            latency_ms=duration,
        )

    def test_connection(
        self,
        credential: str,
        *,
        functional: bool = True,
        preferred_model: str | None = None,
    ) -> ConnectionTestResult:
        disc = self.list_models(credential)
        if not disc.success:
            return ConnectionTestResult(
                success=False,
                state=disc.state,
                auth_ok=False,
                models_discovered=0,
                sanitized_message=disc.classification.sanitized_message
                if disc.classification
                else "Model discovery failed",
                latency_ms=disc.latency_ms,
                models=disc.models,
                classification=disc.classification,
            )

        if not functional:
            return ConnectionTestResult(
                success=True,
                state=HealthState.HEALTHY,
                auth_ok=True,
                models_discovered=len(disc.models),
                sanitized_message="Vertex AI model discovery verified",
                latency_ms=disc.latency_ms,
                models=disc.models,
                functional_tested=False,
                functional_ok=None,
            )

        model_to_test = preferred_model or self.default_test_model
        headers = self._build_headers(credential)
        endpoint = f"{self.base_url}/publishers/google/models/{model_to_test}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 2, "temperature": 0.0},
        }

        resp, exc, duration = self._request_with_retry(
            "POST", endpoint, headers=headers, json_body=payload
        )

        if exc is not None or resp is None or resp.status_code != 200:
            classification = self.classify_error(resp, exc)
            return ConnectionTestResult(
                success=False,
                state=classification.state,
                auth_ok=True,
                models_discovered=len(disc.models),
                sanitized_message=classification.sanitized_message,
                latency_ms=duration,
                models=disc.models,
                model_tested=model_to_test,
                functional_tested=True,
                functional_ok=False,
                classification=classification,
            )

        return ConnectionTestResult(
            success=True,
            state=HealthState.HEALTHY,
            auth_ok=True,
            models_discovered=len(disc.models),
            sanitized_message=f"Verified Vertex AI generation via {model_to_test}",
            latency_ms=duration,
            models=disc.models,
            model_tested=model_to_test,
            functional_tested=True,
            functional_ok=True,
            observed_tokens=2,
        )
