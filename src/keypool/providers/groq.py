"""Official Groq API provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from keypool.config.defaults import DEFAULT_GROQ_TEST_MODEL
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

_NON_CHAT_MODEL_MARKERS = (
    "whisper",
    "tts",
    "playai",
    "orpheus",
    "embed",
    "guard",
    "safeguard",
)


def _is_groq_chat_model(model_id: str) -> bool:
    lower = model_id.lower()
    return not any(marker in lower for marker in _NON_CHAT_MODEL_MARKERS)


class GroqProvider(BaseProvider):
    """Adapter for the Groq Cloud OpenAI-compatible API."""

    name = "groq"
    display_name = "Groq"
    base_url = "https://api.groq.com/openai/v1"
    docs_url = "https://console.groq.com/docs/overview"
    rate_limits_docs_url = "https://console.groq.com/docs/rate-limits"
    models_docs_url = "https://console.groq.com/docs/models"
    env_var_name = "GROQ_API_KEY"
    default_test_model = DEFAULT_GROQ_TEST_MODEL

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        return {
            "Authorization": f"Bearer {cleaned}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_details(response: httpx.Response) -> tuple[str, str, str]:
        """Extract (message, error_type, error_code) safely from a Groq error payload."""
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
            exc_msg = redact_text(str(exc)) if exc else "No response received from Groq API"
            if isinstance(exc, httpx.TimeoutException):
                return ProviderErrorClassification(
                    state=HealthState.NETWORK_ERROR,
                    error_category="timeout",
                    http_status=None,
                    sanitized_message=f"Groq API request timed out: {exc_msg}",
                    is_retryable=True,
                )
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=f"Groq network error: {exc_msg}",
                is_retryable=True,
            )

        status = response.status_code
        msg, err_type, err_code = self._extract_error_details(response)
        retry_after = self._extract_retry_after(response)
        combined_lower = f"{msg} {err_type} {err_code}".lower()

        if status == 401 or "invalid_api_key" in combined_lower:
            return ProviderErrorClassification(
                state=HealthState.AUTH_FAILED,
                error_category="invalid_api_key",
                http_status=status,
                sanitized_message=msg or "Groq authentication failed: invalid API key.",
                is_retryable=False,
            )

        if status == 403 or "permission" in combined_lower or "restricted" in combined_lower:
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED,
                error_category="permission_denied",
                http_status=status,
                sanitized_message=msg or "Groq access forbidden or organization restricted.",
                is_retryable=False,
            )

        if status == 404 or any(
            token in combined_lower
            for token in ("model_not_found", "model_decommissioned", "does not exist")
        ):
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="model_unavailable",
                http_status=status,
                sanitized_message=msg or "Requested Groq model is unavailable or decommissioned.",
                is_retryable=False,
            )

        if status == 429:
            if any(
                token in combined_lower
                for token in ("insufficient_quota", "quota_exceeded", "spend_limit", "billing")
            ):
                return ProviderErrorClassification(
                    state=HealthState.QUOTA_Exhausted,
                    error_category="quota_exhausted",
                    http_status=status,
                    sanitized_message=msg or "Groq account quota or spend limit exhausted.",
                    retry_after_seconds=retry_after,
                    is_retryable=False,
                )
            return ProviderErrorClassification(
                state=HealthState.RATE_LIMITED,
                error_category="rate_limited",
                http_status=status,
                sanitized_message=msg or "Groq rate limit exceeded (HTTP 429).",
                retry_after_seconds=retry_after,
                is_retryable=True,
            )

        if status in (500, 502, 503, 504, 529):
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status,
                sanitized_message=msg or f"Groq service temporarily unavailable (HTTP {status}).",
                retry_after_seconds=retry_after,
                is_retryable=True,
            )

        if status == 400 and "model" in combined_lower:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="model_error",
                http_status=status,
                sanitized_message=msg or "Groq model error (HTTP 400).",
                is_retryable=False,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category=f"http_{status}",
            http_status=status,
            sanitized_message=msg or f"Unexpected Groq HTTP {status} response.",
            retry_after_seconds=retry_after,
            is_retryable=False,
        )

    def list_models(self, credential: str) -> ModelDiscoveryResult:
        register_secret_for_redaction(credential)
        url = f"{self.base_url}/models"
        response, exc, latency_ms = self._request_with_retry(
            "GET",
            url,
            headers=self._build_headers(credential),
        )

        if exc is not None or response is None:
            classification = self.classify_error(response, exc)
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                classification=classification,
                latency_ms=latency_ms,
            )

        if response.status_code != 200:
            classification = self.classify_error(response)
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                classification=classification,
                latency_ms=latency_ms,
            )

        try:
            payload = response.json()
        except Exception as parse_exc:
            classification = ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="malformed_json",
                http_status=response.status_code,
                sanitized_message=f"Malformed JSON from Groq models endpoint: {redact_text(str(parse_exc))}",
                is_retryable=False,
            )
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                classification=classification,
                latency_ms=latency_ms,
            )

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            classification = ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="unexpected_response_structure",
                http_status=response.status_code,
                sanitized_message="Unexpected Groq /models response structure: missing 'data' list.",
                is_retryable=False,
            )
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                classification=classification,
                latency_ms=latency_ms,
            )

        models: list[ModelInfo] = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            active = item.get("active", True)
            if active is False:
                continue
            owned_by = str(item.get("owned_by")) if item.get("owned_by") else None
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    supports_generation=_is_groq_chat_model(model_id),
                    owned_by=owned_by,
                )
            )

        models.sort(key=lambda m: m.id)
        return ModelDiscoveryResult(
            success=True,
            state=HealthState.HEALTHY,
            models=models,
            classification=None,
            latency_ms=latency_ms,
        )

    def _select_functional_model(
        self,
        discovered: list[ModelInfo],
        preferred_model: str | None = None,
    ) -> str | None:
        chat_models = [m.id for m in discovered if m.supports_generation]
        if not chat_models and discovered:
            chat_models = [m.id for m in discovered]
        if not chat_models:
            return preferred_model or self.default_test_model

        target = preferred_model or self.default_test_model
        if target in chat_models:
            return target
        # Prefer versatile / instant llama models if present
        for candidate in chat_models:
            if "llama" in candidate.lower() and "instant" in candidate.lower():
                return candidate
        return chat_models[0]

    def test_connection(
        self,
        credential: str,
        *,
        functional: bool = False,
        preferred_model: str | None = None,
    ) -> ConnectionTestResult:
        register_secret_for_redaction(credential)
        discovery = self.list_models(credential)
        if not discovery.success:
            msg = (
                discovery.classification.sanitized_message
                if discovery.classification
                else "Groq model discovery failed."
            )
            return ConnectionTestResult(
                success=False,
                state=discovery.state,
                auth_ok=False,
                models_discovered=0,
                models=[],
                functional_tested=False,
                functional_ok=None,
                model_tested=None,
                observed_tokens=None,
                latency_ms=discovery.latency_ms,
                classification=discovery.classification,
                sanitized_message=redact_text(msg),
            )

        if not functional:
            return ConnectionTestResult(
                success=True,
                state=HealthState.HEALTHY,
                auth_ok=True,
                models_discovered=len(discovery.models),
                models=discovery.models,
                functional_tested=False,
                functional_ok=None,
                model_tested=None,
                observed_tokens=None,
                latency_ms=discovery.latency_ms,
                classification=None,
                sanitized_message=f"Groq connection healthy ({len(discovery.models)} models discovered).",
            )

        selected_model = self._select_functional_model(discovery.models, preferred_model)
        url = f"{self.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": selected_model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        response, exc, gen_latency = self._request_with_retry(
            "POST",
            url,
            headers=self._build_headers(credential),
            json_body=body,
        )
        total_latency = round(discovery.latency_ms + gen_latency, 2)

        if exc is not None or response is None or response.status_code != 200:
            classification = self.classify_error(response, exc)
            return ConnectionTestResult(
                success=False,
                state=classification.state,
                auth_ok=True,
                models_discovered=len(discovery.models),
                models=discovery.models,
                functional_tested=True,
                functional_ok=False,
                model_tested=selected_model,
                observed_tokens=None,
                latency_ms=total_latency,
                classification=classification,
                sanitized_message=classification.sanitized_message,
            )

        observed_tokens: int | None = None
        try:
            gen_payload = response.json()
            if isinstance(gen_payload, dict):
                usage = gen_payload.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
                    observed_tokens = usage["total_tokens"]
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
            model_tested=selected_model,
            observed_tokens=observed_tokens,
            latency_ms=total_latency,
            classification=None,
            sanitized_message=f"Groq connection and generation verified on '{selected_model}'.",
        )
