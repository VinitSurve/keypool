"""Comprehensive conformance and contract tests for universal provider adapters."""

from __future__ import annotations

import httpx
import pytest

from keypool.core.exceptions import InvalidArgumentError, SecurityError
from keypool.providers.anthropic import AnthropicProvider
from keypool.providers.capabilities import PROVIDER_CATALOG, ProviderCategory
from keypool.providers.cohere import CohereProvider
from keypool.providers.custom import (
    CustomProviderConfig,
    validate_provider_endpoint_url,
)
from keypool.providers.openai_compatible import OpenAICompatibleProvider
from keypool.providers.registry import ProviderRegistry
from keypool.providers.replicate import ReplicateProvider
from keypool.storage.models import HealthState


def test_provider_registry_contains_all_catalog_providers() -> None:
    """Verify that every provider in the master catalog is registered and loadable."""
    registry = ProviderRegistry()
    registered_names = set(registry.names())

    for catalog_name in PROVIDER_CATALOG:
        assert catalog_name in registered_names, (
            f"Catalog provider '{catalog_name}' missing from registry"
        )
        adapter = registry.get(catalog_name)
        assert adapter.name == catalog_name
        assert adapter.display_name
        assert adapter.base_url.startswith("http://") or adapter.base_url.startswith("https://")
        assert adapter.get_environment_variable()
        assert adapter.docs_url.startswith("http")

        meta = registry.get_metadata(catalog_name)
        assert meta is not None
        assert meta.name == catalog_name
        assert isinstance(meta.category, ProviderCategory)


def test_openai_compatible_adapter_mock_lifecycle() -> None:
    """Verify OpenAI-compatible adapter handles list_models, generation, and error mapping."""

    def custom_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(
                401, json={"error": {"message": "Invalid API key", "type": "invalid_request_error"}}
            )
        if "rate-key" in auth:
            return httpx.Response(
                429,
                headers={"retry-after": "12"},
                json={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
            )
        if url.endswith("/models"):
            return httpx.Response(
                200, json={"data": [{"id": "gpt-4o", "name": "GPT-4o"}, {"id": "gpt-4o-mini"}]}
            )
        if url.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                    "usage": {"total_tokens": 8},
                },
            )
        return httpx.Response(404, json={"error": {"message": "Not found"}})

    client = httpx.Client(transport=httpx.MockTransport(custom_handler))
    adapter = OpenAICompatibleProvider(
        name="test_openai",
        display_name="Test OpenAI",
        base_url="https://api.openai.com/v1",
        http_client=client,
    )

    # 1. Success model discovery
    disc = adapter.list_models("sk-valid-key")
    assert disc.success is True
    assert len(disc.models) == 2
    assert disc.models[0].id == "gpt-4o"

    # 2. Connection test with minimal generation
    conn = adapter.test_connection("sk-valid-key", functional=True, preferred_model="gpt-4o")
    assert conn.success is True
    assert conn.functional_ok is True
    assert conn.observed_tokens == 8

    # 3. 401 Auth error classification
    disc_bad = adapter.list_models("sk-bad-key")
    assert disc_bad.success is False
    assert disc_bad.state == HealthState.AUTH_FAILED
    assert disc_bad.classification is not None
    assert "Invalid API key" in disc_bad.classification.sanitized_message

    # 4. 429 Rate limit error classification
    disc_rate = adapter.list_models("sk-rate-key")
    assert disc_rate.success is False
    assert disc_rate.state == HealthState.RATE_LIMITED
    assert disc_rate.classification is not None
    assert disc_rate.classification.retry_after_seconds == 12


def test_anthropic_adapter_mock_lifecycle() -> None:
    """Verify Anthropic native adapter handles headers, models, messages, and rate limits."""

    def anthropic_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("anthropic-version") == "2023-06-01"
        key = request.headers.get("x-api-key", "")
        if key == "bad-key":
            return httpx.Response(
                401,
                json={
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid x-api-key"},
                },
            )
        if key == "rate-key":
            return httpx.Response(
                429,
                headers={"retry-after": "45"},
                json={
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "Rate limit exceeded"},
                },
            )
        if str(request.url).endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "claude-3-5-sonnet-latest", "display_name": "Claude 3.5 Sonnet"}
                    ]
                },
            )
        if str(request.url).endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "pong"}],
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(anthropic_handler))
    adapter = AnthropicProvider(http_client=client)

    # 1. Success model discovery
    disc = adapter.list_models("sk-ant-valid-key")
    assert disc.success is True
    assert any(m.id == "claude-3-5-sonnet-latest" for m in disc.models)

    # 2. Connection test with functional call
    conn = adapter.test_connection(
        "sk-ant-valid-key", functional=True, preferred_model="claude-3-5-sonnet-latest"
    )
    assert conn.success is True
    assert conn.functional_ok is True
    assert conn.observed_tokens == 6

    # 3. 401 Auth error
    bad_conn = adapter.test_connection("bad-key")
    assert bad_conn.success is False
    assert bad_conn.state == HealthState.AUTH_FAILED


def test_cohere_adapter_mock_lifecycle() -> None:
    """Verify Cohere native adapter handles endpoints, models, and chat."""

    def cohere_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(401, json={"message": "invalid api token"})
        if str(request.url).endswith("/models"):
            return httpx.Response(200, json={"models": [{"name": "command-r-08-2024"}]})
        if str(request.url).endswith("/chat"):
            return httpx.Response(200, json={"message": {"content": [{"text": "hello"}]}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(cohere_handler))
    adapter = CohereProvider(http_client=client)

    disc = adapter.list_models("test-cohere-key")
    assert disc.success is True
    assert disc.models[0].id == "command-r-08-2024"

    bad = adapter.list_models("bad-key")
    assert bad.success is False
    assert bad.state == HealthState.AUTH_FAILED


def test_replicate_adapter_mock_lifecycle() -> None:
    """Verify Replicate native adapter handles Token header and models."""

    def replicate_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(401, json={"detail": "Invalid token"})
        if str(request.url).endswith("/models"):
            return httpx.Response(
                200, json={"results": [{"owner": "meta", "name": "llama-3-8b-instruct"}]}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(replicate_handler))
    adapter = ReplicateProvider(http_client=client)

    disc = adapter.list_models("r8_valid_token")
    assert disc.success is True
    assert any(m.id == "meta/llama-3-8b-instruct" for m in disc.models)


def test_custom_provider_ssrf_and_url_validation() -> None:
    """Verify SSRF protections block malicious, cloud metadata, and invalid endpoints."""
    # 1. Invalid schemes
    with pytest.raises(SecurityError):
        validate_provider_endpoint_url("file:///etc/passwd")

    with pytest.raises(SecurityError):
        validate_provider_endpoint_url("gopher://127.0.0.1:8000")

    # 2. Cloud metadata destinations
    with pytest.raises(SecurityError):
        validate_provider_endpoint_url("http://169.254.169.254/latest/meta-data")

    with pytest.raises(SecurityError):
        validate_provider_endpoint_url("http://metadata.google.internal/computeMetadata/v1")

    with pytest.raises(SecurityError):
        validate_provider_endpoint_url("http://100.100.100.200/latest/meta-data")

    # 3. Malformed / empty
    with pytest.raises(InvalidArgumentError):
        validate_provider_endpoint_url("")

    # 4. Valid external HTTPS URL
    valid = validate_provider_endpoint_url("https://custom-ai.company.org/v1/")
    assert valid == "https://custom-ai.company.org/v1"

    # 5. Custom provider registration
    registry = ProviderRegistry()
    cfg = CustomProviderConfig(
        name="my_corp_ai",
        display_name="Corp AI",
        base_url="https://custom-ai.company.org/v1",
        env_var_name="CORP_AI_KEY",
        custom_headers={"X-Custom-Org": "Engineering"},
    )
    custom_adapter = registry.register_custom(cfg)
    assert custom_adapter.name == "my_corp_ai"
    assert custom_adapter.get_environment_variable() == "CORP_AI_KEY"
    headers = custom_adapter._build_headers("test-secret-key")
    assert headers["X-Custom-Org"] == "Engineering"
    assert headers["Authorization"] == "Bearer test-secret-key"


def test_azure_openai_adapter_conformance() -> None:
    """Verify Azure OpenAI provider adapter headers, models, completions, and error classification."""
    from keypool.providers.azure import AzureOpenAIProvider

    def azure_handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("api-key", "")
        if key == "bad-key":
            return httpx.Response(401, json={"error": {"code": "Unauthorized", "message": "Access denied"}})
        if key == "rate-key":
            return httpx.Response(429, headers={"retry-after": "25"}, json={"error": {"code": "QuotaExceeded", "message": "Rate limit exceeded"}})
        if str(request.url).endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini", "name": "GPT-4o Mini"}]})
        if str(request.url).endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}], "usage": {"total_tokens": 5}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(azure_handler))
    adapter = AzureOpenAIProvider(http_client=client)

    # 1. Success model discovery
    disc = adapter.list_models("valid-azure-key")
    assert disc.success is True
    assert disc.models[0].id == "gpt-4o-mini"

    # 2. Connection test with functional call
    conn = adapter.test_connection("valid-azure-key", functional=True)
    assert conn.success is True
    assert conn.functional_ok is True
    assert conn.observed_tokens == 5

    # 3. 401 Auth error
    bad_conn = adapter.test_connection("bad-key")
    assert bad_conn.success is False
    assert bad_conn.state == HealthState.AUTH_FAILED

    # 4. 429 Rate limit
    rate_conn = adapter.test_connection("rate-key")
    assert rate_conn.success is False
    assert rate_conn.state == HealthState.QUOTA_EXHAUSTED


def test_bedrock_adapter_conformance() -> None:
    """Verify Amazon Bedrock adapter foundation models, converse API, and error handling."""
    from keypool.providers.bedrock import AmazonBedrockProvider

    def bedrock_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(403, json={"__type": "AccessDeniedException", "message": "Invalid credentials"})
        if "rate-key" in auth:
            return httpx.Response(429, headers={"retry-after": "15"}, json={"__type": "ThrottlingException", "message": "Rate exceeded"})
        if str(request.url).endswith("/foundation-models"):
            return httpx.Response(200, json={"modelSummaries": [{"modelId": "anthropic.claude-3-haiku-20240307-v1:0", "modelName": "Claude 3 Haiku"}]})
        if "/converse" in str(request.url):
            return httpx.Response(200, json={"output": {"message": {"content": [{"text": "pong"}]}}, "usage": {"totalTokens": 4}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(bedrock_handler))
    adapter = AmazonBedrockProvider(http_client=client)

    disc = adapter.list_models("valid-bedrock-key")
    assert disc.success is True
    assert disc.models[0].id == "anthropic.claude-3-haiku-20240307-v1:0"

    conn = adapter.test_connection("valid-bedrock-key", functional=True)
    assert conn.success is True
    assert conn.functional_ok is True

    bad = adapter.test_connection("bad-key")
    assert bad.success is False
    assert bad.state in (HealthState.AUTH_FAILED, HealthState.PERMISSION_DENIED)


def test_vertex_adapter_conformance() -> None:
    """Verify Google Vertex AI adapter publisher models, generateContent, and errors."""
    from keypool.providers.vertex import GoogleVertexProvider

    def vertex_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED", "message": "Invalid token"}})
        if str(request.url).endswith("/publishers/google/models"):
            return httpx.Response(200, json={"models": [{"name": "publishers/google/models/gemini-1.5-flash", "displayName": "Gemini 1.5 Flash"}]})
        if ":generateContent" in str(request.url):
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "pong"}]}}]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(vertex_handler))
    adapter = GoogleVertexProvider(http_client=client)

    disc = adapter.list_models("valid-vertex-key")
    assert disc.success is True
    assert disc.models[0].id == "gemini-1.5-flash"

    conn = adapter.test_connection("valid-vertex-key", functional=True)
    assert conn.success is True
    assert conn.functional_ok is True


def test_watsonx_adapter_conformance() -> None:
    """Verify IBM watsonx.ai adapter models, chat endpoint, and errors."""
    from keypool.providers.watsonx import WatsonXProvider

    def watsonx_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if "bad-key" in auth:
            return httpx.Response(401, json={"errors": [{"code": "unauthorized", "message": "Token expired"}]})
        if "/foundation_model_specs" in str(request.url):
            return httpx.Response(200, json={"resources": [{"model_id": "ibm/granite-13b-chat-v2", "label": "Granite 13B"}]})
        if "/ml/v1/text/chat" in str(request.url):
            return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(watsonx_handler))
    adapter = WatsonXProvider(http_client=client)

    disc = adapter.list_models("valid-watsonx-key")
    assert disc.success is True
    assert disc.models[0].id == "ibm/granite-13b-chat-v2"

    conn = adapter.test_connection("valid-watsonx-key", functional=True)
    assert conn.success is True
    assert conn.functional_ok is True


def test_provider_alias_resolution_conformance() -> None:
    """Verify that colloquial provider names and aliases map to canonical adapters."""
    from keypool.providers.capabilities import PROVIDER_ALIASES, normalize_provider_name
    from keypool.security.validation import validate_provider_name

    registry = ProviderRegistry()

    alias_expectations = {
        "azure": "azure_openai",
        "azure-openai": "azure_openai",
        "llama.cpp": "llamacpp",
        "llama-cpp": "llamacpp",
        "01ai": "yi",
        "01.ai": "yi",
        "volcengine": "doubao",
        "volcano": "doubao",
        "togetherai": "together",
        "hf": "huggingface",
        "aws": "bedrock",
        "gcp": "vertex",
        "ibm": "watsonx",
        "lm-studio": "lmstudio",
        "local-ai": "localai",
        "mistralai": "mistral",
        "deepseekai": "deepseek",
    }

    for alias, expected in alias_expectations.items():
        assert alias in PROVIDER_ALIASES
        assert normalize_provider_name(alias) == expected
        assert validate_provider_name(alias) == expected
        adapter = registry.get(alias)
        assert adapter.name == expected
        meta = registry.get_metadata(alias)
        assert meta is not None
        assert meta.name == expected
        assert registry.is_registered(alias) is True


def test_local_provider_adapters_conformance() -> None:
    """Verify local runner providers (Ollama, LM Studio, vLLM, llama.cpp, LocalAI, SGLang) work without API keys."""
    registry = ProviderRegistry()
    local_providers = ["ollama", "lmstudio", "vllm", "llamacpp", "localai", "sglang"]

    for lp in local_providers:
        adapter = registry.get(lp)
        meta = registry.get_metadata(lp)
        assert meta is not None
        assert meta.category == ProviderCategory.LOCAL
        assert adapter.requires_key is False

        # Build headers with dummy/empty key should not crash and contain valid content type
        headers = adapter._build_headers("")
        assert "Content-Type" in headers
        assert "Authorization" not in headers  # No auth header needed for local engines


