"""Provider adapter tests with mocked HTTP responses (Section 21)."""

from __future__ import annotations

import httpx
from tests.fixtures.provider_payloads import (
    FAKE_GEMINI_KEY_1,
    FAKE_GROQ_KEY_1,
    GEMINI_GENERATE_SUCCESS_PAYLOAD,
    GEMINI_MODELS_SUCCESS_PAYLOAD,
    GROQ_CHAT_COMPLETION_PAYLOAD,
    GROQ_MODELS_SUCCESS_PAYLOAD,
)

from keypool.providers.base import parse_retry_duration_seconds
from keypool.providers.gemini import GeminiProvider
from keypool.providers.groq import GroqProvider
from keypool.storage.models import HealthState


def test_parse_retry_duration_seconds() -> None:
    """Verify duration parsing for numeric seconds and Go-style duration strings."""
    assert parse_retry_duration_seconds("30") == 30
    assert parse_retry_duration_seconds("2m30s") == 150
    assert parse_retry_duration_seconds("500ms") == 1
    assert parse_retry_duration_seconds("1h") == 3600
    assert parse_retry_duration_seconds(None) is None
    assert parse_retry_duration_seconds("") is None


def test_groq_successful_auth_and_functional_generation() -> None:
    """Verify Groq adapter model discovery and minimal generation request."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Authorization") == f"Bearer {FAKE_GROQ_KEY_1}"
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=GROQ_MODELS_SUCCESS_PAYLOAD)
        if req.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=GROQ_CHAT_COMPLETION_PAYLOAD)
        return httpx.Response(404)

    provider = GroqProvider(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    val = provider.validate_credential(FAKE_GROQ_KEY_1)
    assert val.is_valid is True
    assert val.state == HealthState.HEALTHY

    conn = provider.test_connection(FAKE_GROQ_KEY_1, functional=True)
    assert conn.success is True
    assert conn.state == HealthState.HEALTHY
    assert conn.models_discovered == 3
    assert conn.functional_ok is True
    assert conn.observed_tokens == 5


def test_groq_invalid_credentials_rate_limit_quota_and_errors() -> None:
    """Verify Groq error classification for 401, 403, 404, 429, and 503."""
    status_to_return = 401
    payload_to_return: dict[str, object] = {
        "error": {
            "message": "Invalid API Key",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }
    headers_to_return: dict[str, str] = {}

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status_to_return, json=payload_to_return, headers=headers_to_return)

    provider = GroqProvider(
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    # 1. Invalid key (401)
    res_401 = provider.list_models(FAKE_GROQ_KEY_1)
    assert res_401.success is False
    assert res_401.state == HealthState.AUTH_FAILED

    # 2. Permission denied (403)
    status_to_return = 403
    payload_to_return = {"error": {"message": "Organization restricted"}}
    res_403 = provider.list_models(FAKE_GROQ_KEY_1)
    assert res_403.state == HealthState.PERMISSION_DENIED

    # 3. Rate limit (429) with Groq x-ratelimit-reset-requests header
    status_to_return = 429
    payload_to_return = {"error": {"message": "Rate limit reached for requests"}}
    headers_to_return = {"x-ratelimit-reset-requests": "1m15s"}
    res_429 = provider.list_models(FAKE_GROQ_KEY_1)
    assert res_429.state == HealthState.RATE_LIMITED
    assert res_429.classification is not None
    assert res_429.classification.retry_after_seconds == 75

    # 4. Quota exhausted (429 insufficient_quota)
    payload_to_return = {
        "error": {"message": "You exceeded your current spend_limit", "code": "insufficient_quota"}
    }
    res_quota = provider.list_models(FAKE_GROQ_KEY_1)
    assert res_quota.state == HealthState.QUOTA_Exhausted

    # 5. Model unavailable (404)
    status_to_return = 404
    payload_to_return = {
        "error": {"message": "The model does not exist", "code": "model_not_found"}
    }
    res_404 = provider.list_models(FAKE_GROQ_KEY_1)
    assert res_404.state == HealthState.MODEL_UNAVAILABLE

    # 6. Provider outage (503)
    status_to_return = 503
    payload_to_return = {"error": {"message": "Service temporarily unavailable"}}
    res_503 = provider.list_models(FAKE_GROQ_KEY_1)
    assert res_503.state == HealthState.PROVIDER_UNAVAILABLE


def test_gemini_successful_auth_and_functional_generation() -> None:
    """Verify Google Gemini adapter authentication via x-goog-api-key, model listing, and generation."""

    def handler(req: httpx.Request) -> httpx.Response:
        # Ensure API key is sent in x-goog-api-key header and NOT leaked in URL query string
        assert req.headers.get("x-goog-api-key") == FAKE_GEMINI_KEY_1
        assert FAKE_GEMINI_KEY_1 not in str(req.url)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=GEMINI_MODELS_SUCCESS_PAYLOAD)
        if ":generateContent" in req.url.path:
            return httpx.Response(200, json=GEMINI_GENERATE_SUCCESS_PAYLOAD)
        return httpx.Response(404)

    provider = GeminiProvider(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    conn = provider.test_connection(FAKE_GEMINI_KEY_1, functional=True)
    assert conn.success is True
    assert conn.state == HealthState.HEALTHY
    assert conn.models_discovered == 3
    assert conn.functional_ok is True
    assert conn.model_tested == "gemini-2.5-flash"
    assert conn.observed_tokens == 3


def test_gemini_error_classifications() -> None:
    """Verify Gemini classification for 400 API_KEY_INVALID, 403, 404, 429 quota/rate-limit, and 503."""
    status_to_return = 400
    payload_to_return: dict[str, object] = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
            "details": [{"reason": "API_KEY_INVALID"}],
        }
    }

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status_to_return, json=payload_to_return)

    provider = GeminiProvider(
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    # 1. Invalid Gemini API key (HTTP 400 API_KEY_INVALID)
    res_bad_key = provider.list_models(FAKE_GEMINI_KEY_1)
    assert res_bad_key.state == HealthState.AUTH_FAILED

    # 2. Permission denied (HTTP 403 API_KEY_SERVICE_BLOCKED)
    status_to_return = 403
    payload_to_return = {
        "error": {
            "code": 403,
            "message": "Requests to this API are blocked.",
            "status": "PERMISSION_DENIED",
            "details": [{"reason": "API_KEY_SERVICE_BLOCKED"}],
        }
    }
    res_403 = provider.list_models(FAKE_GEMINI_KEY_1)
    assert res_403.state == HealthState.PERMISSION_DENIED

    # 3. Rate limit (HTTP 429 with Google RPC RetryInfo retryDelay)
    status_to_return = 429
    payload_to_return = {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted (e.g. check per-minute rate limit).",
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"retryDelay": "28s"}],
        }
    }
    res_429 = provider.list_models(FAKE_GEMINI_KEY_1)
    assert res_429.state == HealthState.RATE_LIMITED
    assert res_429.classification is not None
    assert res_429.classification.retry_after_seconds == 28

    # 4. Quota exhausted (HTTP 429 billing/daily limit)
    payload_to_return = {
        "error": {
            "code": 429,
            "message": "Quota exceeded for quota metric and limit: 0 of service. Check your plan and billing details.",
            "status": "RESOURCE_EXHAUSTED",
        }
    }
    res_quota = provider.list_models(FAKE_GEMINI_KEY_1)
    assert res_quota.state == HealthState.QUOTA_Exhausted

    # 5. Model unavailable (HTTP 404)
    status_to_return = 404
    payload_to_return = {
        "error": {
            "code": 404,
            "message": "models/gemini-old is not found for API version v1beta",
            "status": "NOT_FOUND",
        }
    }
    res_404 = provider.list_models(FAKE_GEMINI_KEY_1)
    assert res_404.state == HealthState.MODEL_UNAVAILABLE


def test_provider_timeout_malformed_json_and_unexpected_structures() -> None:
    """Verify resilient handling of network timeouts, malformed JSON, and missing fields."""

    def timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out", request=req)

    groq_timeout = GroqProvider(
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(timeout_handler)),
    )
    res_to = groq_timeout.list_models(FAKE_GROQ_KEY_1)
    assert res_to.success is False
    assert res_to.state == HealthState.NETWORK_ERROR

    def malformed_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not-valid-json")

    gemini_malformed = GeminiProvider(
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(malformed_handler)),
    )
    res_mal = gemini_malformed.list_models(FAKE_GEMINI_KEY_1)
    assert res_mal.success is False
    assert res_mal.state == HealthState.PROVIDER_UNAVAILABLE
    assert res_mal.classification is not None
    assert res_mal.classification.error_category == "malformed_json"

    def missing_fields_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected_key": 123})

    groq_unexpected = GroqProvider(
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(missing_fields_handler)),
    )
    res_unexp = groq_unexpected.list_models(FAKE_GROQ_KEY_1)
    assert res_unexp.success is False
    assert res_unexp.classification is not None
    assert res_unexp.classification.error_category == "unexpected_response_structure"
