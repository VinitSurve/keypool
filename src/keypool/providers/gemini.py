"""Official Google Gemini API provider adapter."""

from __future__ import annotations

from typing import Any

import httpx

from keypool.config.defaults import DEFAULT_GEMINI_TEST_MODEL
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

_NON_TEXT_GEMINI_MARKERS = (
    "embedding",
    "aqa",
    "imagen",
    "veo",
    "tts",
    "audio",
    "robotics",
    "computer-use",
)


def _normalize_gemini_model_id(raw_name: str) -> str:
    cleaned = raw_name.strip()
    if cleaned.startswith("models/"):
        return cleaned[len("models/") :]
    return cleaned


def _is_gemini_text_gen_model(model_id: str, methods: list[str]) -> bool:
    if methods and "generateContent" not in methods:
        return False
    lower = model_id.lower()
    return not any(marker in lower for marker in _NON_TEXT_GEMINI_MARKERS)


class GeminiProvider(BaseProvider):
    """Adapter for the Google Gemini Developer API (generativelanguage.googleapis.com)."""

    name = "gemini"
    display_name = "Google Gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    docs_url = "https://ai.google.dev/gemini-api/docs"
    rate_limits_docs_url = "https://ai.google.dev/gemini-api/docs/rate-limits"
    models_docs_url = "https://ai.google.dev/gemini-api/docs/models"
    env_var_name = "GEMINI_API_KEY"
    default_test_model = DEFAULT_GEMINI_TEST_MODEL

    def _build_headers(self, credential: str) -> dict[str, str]:
        cleaned = credential.strip()
        register_secret_for_redaction(cleaned)
        return {
            "x-goog-api-key": cleaned,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_details(
        response: httpx.Response,
    ) -> tuple[str, str, list[str], int | None]:
        """Extract (message, rpc_status, reasons, retry_delay_sec) from Gemini error JSON."""
        reasons: list[str] = []
        retry_delay: int | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    msg = str(err.get("message") or "")
                    rpc_status = str(err.get("status") or "")
                    details = err.get("details")
                    if isinstance(details, list):
                        for item in details:
                            if not isinstance(item, dict):
                                continue
                            reason = item.get("reason")
                            if isinstance(reason, str) and reason:
                                reasons.append(reason)
                            delay_raw = item.get("retryDelay")
                            if isinstance(delay_raw, str):
                                parsed = parse_retry_duration_seconds(delay_raw)
                                if parsed is not None:
                                    retry_delay = parsed
                    return redact_text(msg), redact_text(rpc_status), reasons, retry_delay
        except Exception:  # noqa: S110
            pass
        snippet = (response.text or "")[:240].strip()
        return redact_text(snippet or f"HTTP {response.status_code}"), "", reasons, None

    @staticmethod
    def _extract_retry_after(
        response: httpx.Response,
        rpc_retry_delay: int | None = None,
    ) -> int | None:
        header_val = parse_retry_duration_seconds(response.headers.get("retry-after"))
        if header_val is not None:
            return header_val
        return rpc_retry_delay

    def classify_error(
        self,
        response: httpx.Response | None,
        exc: Exception | None = None,
    ) -> ProviderErrorClassification:
        if exc is not None or response is None:
            exc_msg = redact_text(str(exc)) if exc else "No response received from Gemini API"
            if isinstance(exc, httpx.TimeoutException):
                return ProviderErrorClassification(
                    state=HealthState.NETWORK_ERROR,
                    error_category="timeout",
                    http_status=None,
                    sanitized_message=f"Google Gemini API request timed out: {exc_msg}",
                    is_retryable=True,
                )
            return ProviderErrorClassification(
                state=HealthState.NETWORK_ERROR,
                error_category="network_error",
                http_status=None,
                sanitized_message=f"Google Gemini network error: {exc_msg}",
                is_retryable=True,
            )

        status = response.status_code
        msg, rpc_status, reasons, rpc_retry = self._extract_error_details(response)
        retry_after = self._extract_retry_after(response, rpc_retry)
        reasons_upper = {r.upper() for r in reasons}
        combined_lower = f"{msg} {rpc_status} {' '.join(reasons)}".lower()

        # Gemini returns HTTP 400 INVALID_ARGUMENT with API_KEY_INVALID for bad keys, or 401 UNAUTHENTICATED
        if (
            status == 401
            or rpc_status.upper() == "UNAUTHENTICATED"
            or bool(
                reasons_upper
                & {
                    "API_KEY_INVALID",
                    "API_KEY_EXPIRED",
                    "API_KEY_NOT_FOUND",
                }
            )
            or "api key not valid" in combined_lower
            or "api_key_invalid" in combined_lower
            or "api key expired" in combined_lower
        ):
            return ProviderErrorClassification(
                state=HealthState.AUTH_FAILED,
                error_category="invalid_api_key",
                http_status=status,
                sanitized_message=msg or "Google Gemini authentication failed: invalid API key.",
                is_retryable=False,
            )

        if (
            status == 403
            or rpc_status.upper() == "PERMISSION_DENIED"
            or bool(
                reasons_upper
                & {
                    "API_KEY_SERVICE_BLOCKED",
                    "API_KEY_HTTP_REFERRER_BLOCKED",
                    "API_KEY_IP_ADDRESS_BLOCKED",
                    "SERVICE_DISABLED",
                    "CONSUMER_INVALID",
                }
            )
        ):
            return ProviderErrorClassification(
                state=HealthState.PERMISSION_DENIED,
                error_category="permission_denied",
                http_status=status,
                sanitized_message=msg
                or "Google Gemini permission denied (API key restriction or service disabled).",
                is_retryable=False,
            )

        if (
            status == 404
            or rpc_status.upper() == "NOT_FOUND"
            or "is not found for api version" in combined_lower
            or "not supported for generatecontent" in combined_lower
        ):
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="model_unavailable",
                http_status=status,
                sanitized_message=msg or "Requested Google Gemini model is unavailable.",
                is_retryable=False,
            )

        if status == 429 or rpc_status.upper() == "RESOURCE_EXHAUSTED":
            # Distinguish hard quota / billing exhaustion from transient rate limiting
            is_hard_quota = any(
                token in combined_lower
                for token in (
                    "billing",
                    "check your plan",
                    "daily limit",
                    "quota exceeded for quota metric",
                    "free_tier",
                    "limit: 0",
                )
            )
            if is_hard_quota:
                return ProviderErrorClassification(
                    state=HealthState.QUOTA_Exhausted,
                    error_category="quota_exhausted",
                    http_status=status,
                    sanitized_message=msg or "Google Gemini quota exhausted.",
                    retry_after_seconds=retry_after,
                    is_retryable=False,
                )
            return ProviderErrorClassification(
                state=HealthState.RATE_LIMITED,
                error_category="rate_limited",
                http_status=status,
                sanitized_message=msg or "Google Gemini rate limit exceeded (HTTP 429).",
                retry_after_seconds=retry_after,
                is_retryable=True,
            )

        if status in (500, 502, 503, 504) or rpc_status.upper() in (
            "INTERNAL",
            "UNAVAILABLE",
            "DEADLINE_EXCEEDED",
        ):
            return ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="provider_unavailable",
                http_status=status,
                sanitized_message=msg
                or f"Google Gemini service temporarily unavailable (HTTP {status}).",
                retry_after_seconds=retry_after,
                is_retryable=True,
            )

        if status == 400 and "model" in combined_lower:
            return ProviderErrorClassification(
                state=HealthState.MODEL_UNAVAILABLE,
                error_category="model_error",
                http_status=status,
                sanitized_message=msg or "Google Gemini model request error (HTTP 400).",
                is_retryable=False,
            )

        return ProviderErrorClassification(
            state=HealthState.UNKNOWN,
            error_category=f"http_{status}",
            http_status=status,
            sanitized_message=msg or f"Unexpected Google Gemini HTTP {status} response.",
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
                sanitized_message=f"Malformed JSON from Gemini models endpoint: {redact_text(str(parse_exc))}",
                is_retryable=False,
            )
            return ModelDiscoveryResult(
                success=False,
                state=classification.state,
                models=[],
                classification=classification,
                latency_ms=latency_ms,
            )

        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            classification = ProviderErrorClassification(
                state=HealthState.PROVIDER_UNAVAILABLE,
                error_category="unexpected_response_structure",
                http_status=response.status_code,
                sanitized_message="Unexpected Gemini /models response structure: missing 'models' list.",
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
        for item in payload["models"]:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                continue
            model_id = _normalize_gemini_model_id(raw_name)
            display_name = str(item.get("displayName") or model_id).strip()
            methods_raw = item.get("supportedGenerationMethods")
            methods = (
                [str(m) for m in methods_raw if isinstance(m, str)]
                if isinstance(methods_raw, list)
                else []
            )
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=display_name,
                    supports_generation=_is_gemini_text_gen_model(model_id, methods),
                    owned_by="google",
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
    ) -> str:
        gen_models = [m.id for m in discovered if m.supports_generation]
        if not gen_models and discovered:
            gen_models = [m.id for m in discovered]
        if not gen_models:
            return _normalize_gemini_model_id(preferred_model or self.default_test_model)

        target = _normalize_gemini_model_id(preferred_model or self.default_test_model)
        if target in gen_models:
            return target
        # Prefer flash text models dynamically
        for candidate in gen_models:
            lower = candidate.lower()
            if "gemini" in lower and "flash" in lower and "lite" not in lower:
                return candidate
        for candidate in gen_models:
            if "gemini" in candidate.lower():
                return candidate
        return gen_models[0]

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
                else "Google Gemini model discovery failed."
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
                sanitized_message=f"Google Gemini connection healthy ({len(discovery.models)} models discovered).",
            )

        selected_model = self._select_functional_model(discovery.models, preferred_model)
        url = f"{self.base_url}/models/{selected_model}:generateContent"
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1, "temperature": 0},
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
                usage = gen_payload.get("usageMetadata")
                if isinstance(usage, dict) and isinstance(usage.get("totalTokenCount"), int):
                    observed_tokens = usage["totalTokenCount"]
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
            sanitized_message=f"Google Gemini connection and generation verified on '{selected_model}'.",
        )

