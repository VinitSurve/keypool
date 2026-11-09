"""Base provider adapter abstraction and typed result models."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from keypool.security.redaction import redact_text, register_secret_for_redaction
from keypool.security.validation import validate_api_key_input
from keypool.storage.models import HealthState

_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)")


def parse_retry_duration_seconds(raw_value: str | None) -> int | None:
    """Parse Retry-After or provider reset duration strings into integer seconds.

    Handles standard integer/float seconds (e.g., '30', '12.5') and Go-style
    compound duration strings used by Groq/Google (e.g., '2m59.56s', '45s', '600ms').
    """
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    if not cleaned:
        return None

    # Direct numeric seconds
    try:
        numeric = float(cleaned)
        if numeric >= 0:
            return max(1, int(round(numeric)))
    except ValueError:
        pass

    matches = _DURATION_PART_RE.findall(cleaned.lower())
    if not matches:
        return None

    total_seconds = 0.0
    for amount_str, unit in matches:
        amount = float(amount_str)
        if unit == "ms":
            total_seconds += amount / 1000.0
        elif unit == "s":
            total_seconds += amount
        elif unit == "m":
            total_seconds += amount * 60.0
        elif unit == "h":
            total_seconds += amount * 3600.0

    return max(1, int(round(total_seconds))) if total_seconds > 0 else None


class ModelInfo(BaseModel):
    """Metadata describing an available provider model."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    supports_generation: bool = True
    owned_by: str | None = None


class ProviderErrorClassification(BaseModel):
    """Structured error classification returned by a provider adapter."""

    model_config = ConfigDict(extra="forbid")

    state: HealthState
    error_category: str
    http_status: int | None = None
    sanitized_message: str
    retry_after_seconds: int | None = None
    is_retryable: bool = False


class CredentialValidationResult(BaseModel):
    """Result of local format and remote authentication validation."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    state: HealthState
    sanitized_message: str
    warnings: list[str] = Field(default_factory=list)


class ModelDiscoveryResult(BaseModel):
    """Result of listing models from a provider."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    state: HealthState
    models: list[ModelInfo] = Field(default_factory=list)
    classification: ProviderErrorClassification | None = None
    latency_ms: float = 0.0


class ConnectionTestResult(BaseModel):
    """Comprehensive result of testing a credential against its provider."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    state: HealthState
    auth_ok: bool
    models_discovered: int = 0
    models: list[ModelInfo] = Field(default_factory=list)
    functional_tested: bool = False
    functional_ok: bool | None = None
    model_tested: str | None = None
    observed_tokens: int | None = None
    latency_ms: float = 0.0
    classification: ProviderErrorClassification | None = None
    sanitized_message: str


class BaseProvider(ABC):
    """Abstract base class for KEYPOOL AI provider adapters."""

    name: str
    display_name: str
    base_url: str
    docs_url: str
    rate_limits_docs_url: str
    models_docs_url: str
    env_var_name: str
    default_test_model: str

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        connect_timeout_seconds: float = 5.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self._http_client = http_client

    def get_environment_variable(self) -> str:
        """Return the official environment variable name expected by SDKs for this provider."""
        return self.env_var_name

    def _create_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_seconds, connect=self.connect_timeout_seconds)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response | None, Exception | None, float]:
        """Execute an HTTP request with bounded retries for transient network/5xx errors."""
        start_time = time.perf_counter()
        last_exc: Exception | None = None
        response: httpx.Response | None = None

        attempts = 1 + max(0, self.max_retries) if method.upper() == "GET" else 1

        for attempt in range(attempts):
            try:
                if self._http_client is not None:
                    response = self._http_client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        timeout=self._create_timeout(),
                    )
                else:
                    with httpx.Client(
                        timeout=self._create_timeout(), follow_redirects=False
                    ) as client:
                        response = client.request(
                            method,
                            url,
                            headers=headers,
                            json=json_body,
                        )
                last_exc = None
                if response.status_code in (502, 503, 504) and attempt + 1 < attempts:
                    time.sleep(self.backoff_base_seconds * (2**attempt))
                    continue
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(self.backoff_base_seconds * (2**attempt))
                    continue
                break

        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        return response, last_exc, latency_ms

    def validate_credential(self, credential: str) -> CredentialValidationResult:
        """Validate credential format locally and test authentication against the provider."""
        register_secret_for_redaction(credential)
        try:
            cleaned, warnings = validate_api_key_input(credential, provider=self.name)
        except Exception as exc:
            return CredentialValidationResult(
                is_valid=False,
                state=HealthState.AUTH_FAILED,
                sanitized_message=redact_text(str(exc)),
                warnings=[],
            )

        discovery = self.list_models(cleaned)
        if discovery.success:
            return CredentialValidationResult(
                is_valid=True,
                state=HealthState.HEALTHY,
                sanitized_message=f"{self.display_name} authentication succeeded ({len(discovery.models)} models accessible).",
                warnings=warnings,
            )

        msg = (
            discovery.classification.sanitized_message
            if discovery.classification
            else f"{self.display_name} credential validation failed."
        )
        return CredentialValidationResult(
            is_valid=False,
            state=discovery.state,
            sanitized_message=redact_text(msg),
            warnings=warnings,
        )

    @abstractmethod
    def list_models(self, credential: str) -> ModelDiscoveryResult:
        """Discover accessible models using the given credential."""

    @abstractmethod
    def test_connection(
        self,
        credential: str,
        *,
        functional: bool = False,
        preferred_model: str | None = None,
    ) -> ConnectionTestResult:
        """Perform model discovery and optional minimal generation test."""

    @abstractmethod
    def classify_error(
        self,
        response: httpx.Response | None,
        exc: Exception | None = None,
    ) -> ProviderErrorClassification:
        """Classify an HTTP error response or transport exception into a HealthState."""
