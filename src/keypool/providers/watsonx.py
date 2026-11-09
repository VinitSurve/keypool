"""IBM watsonx.ai provider adapter."""

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


class WatsonXProvider(BaseProvider):
    """Adapter for IBM watsonx.ai foundation models service."""

    name = "watsonx"
    display_name = "IBM watsonx.ai"
    base_url = "https://us-south.ml.cloud.ibm.com"
    docs_url = "https://www.ibm.com/docs/en/watsonx/saas"
    rate_limits_docs_url = "https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-rate-limits.html"
    models_docs_url = "https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-models.html"
    env_var_name = "WATSONX_APIKEY"
    default_test_model = "ibm/granite-13b-chat-v2"

    def __init__(
        self,
        name: str = "watsonx",
        display_name: str = "IBM watsonx.ai",
        base_url: str | None = None,
        env_var_name: str = "WATSONX_APIKEY",
        default_test_model: str = "ibm/granite-13b-chat-v2",
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
        self.display_name = display_name or (meta.display_name if meta else "IBM watsonx.ai")
        self.base_url = (base_url or (meta.base_url if meta else self.base_url)).rstrip("/")
        self.env_var_name = env_var_name or (meta.env_var_name if meta else self.env_var_name)
        self.default_test_model = default_test_model or (
            meta.default_test_model if meta else self.default_test_model
        )

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        auth = cleaned if cleaned.startswith("Bearer ") else f"Bearer {cleaned}"
        return {
            "Authorization": auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> tuple[str, str]:
        with contextlib.suppress(Exception):
            payload = response.json()
            if isinstance(payload, dict):
                errs = payload.get("errors")
                if isinstance(errs, list) and errs:
                    first = errs[0]
                    if isinstance(first, dict):
                        return redact_text(str(first.get("message") or "")), str(
                            first.get("code") or ""
                        )
                msg = str(payload.get("message") or "")
                return redact_text(msg), ""
        return redact_text(response.text[:300]), ""

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
        msg, code = self._extract_error_details(response)
        retry_after = parse_retry_duration_seconds(response.headers.get("retry-after"))

        if status_code in (401, 403):
            is_perm = status_code == 403 or "forbidden" in msg.lower()
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED if is_perm else HealthState.AUTH_FAILED,
                error_category="permission_denied" if is_perm else "auth_failed",
                http_status=status_code,
                sanitized_message=msg or "watsonx.ai IAM token invalid or unauthorized",
                is_retryable=False,
            )

        if status_code == 429:
            return ProviderErrorClassification(
                state=HealthState.RATE_LIMITED,
                error_category="rate_limited",
                http_status=status_code,
                sanitized_message=msg or "watsonx.ai rate limit exceeded",
                retry_after_seconds=retry_after or 30,
                is_retryable=True,
            )

        if status_code == 404:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="model_not_found",
                http_status=status_code,
                sanitized_message=msg or "watsonx.ai model not found",
                is_retryable=False,
            )

        if status_code >= 500:
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status_code,
                sanitized_message=msg or f"watsonx.ai internal error (HTTP {status_code})",
                retry_after_seconds=retry_after or 10,
                is_retryable=True,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category="http_error",
            http_status=status_code,
            sanitized_message=msg or f"HTTP {status_code} error from IBM watsonx.ai",
            is_retryable=False,
        )

    def list_models(self, credential: str) -> ModelDiscoveryResult:
        headers = self._build_headers(credential)
        endpoint = f"{self.base_url}/ml/v1/foundation_model_specs?version=2023-05-29"
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
            resources = payload.get("resources") if isinstance(payload, dict) else []
            if isinstance(resources, list):
                for item in resources:
                    if isinstance(item, dict) and "model_id" in item:
                        mid = str(item["model_id"])
                        dname = str(item.get("label") or mid)
                        provider_name = str(item.get("provider") or "ibm")
                        models.append(
                            ModelInfo(
                                id=mid,
                                display_name=dname,
                                supports_generation=True,
                                owned_by=provider_name,
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
                    sanitized_message=f"Failed to parse watsonx models: {parse_exc}",
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
                sanitized_message="IBM watsonx.ai model discovery verified",
                latency_ms=disc.latency_ms,
                models=disc.models,
                functional_tested=False,
                functional_ok=None,
            )

        model_to_test = preferred_model or self.default_test_model
        headers = self._build_headers(credential)
        endpoint = f"{self.base_url}/ml/v1/text/chat?version=2023-05-29"
        payload = {
            "model_id": model_to_test,
            "messages": [{"role": "user", "content": "ping"}],
            "parameters": {"max_new_tokens": 2, "temperature": 0.0},
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
            sanitized_message=f"Verified watsonx generation via {model_to_test}",
            latency_ms=duration,
            models=disc.models,
            model_tested=model_to_test,
            functional_tested=True,
            functional_ok=True,
            observed_tokens=2,
        )
