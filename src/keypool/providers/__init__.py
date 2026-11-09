"""Provider adapters and registry for KEYPOOL."""

from keypool.providers.anthropic import AnthropicProvider
from keypool.providers.base import (
    BaseProvider,
    ConnectionTestResult,
    CredentialValidationResult,
    ModelDiscoveryResult,
    ModelInfo,
    ProviderErrorClassification,
    parse_retry_duration_seconds,
)
from keypool.providers.capabilities import (
    PROVIDER_CATALOG,
    AuthType,
    ProviderAPIType,
    ProviderCapability,
    ProviderCategory,
    ProviderMetadata,
    get_provider_metadata,
)
from keypool.providers.cohere import CohereProvider
from keypool.providers.custom import (
    CustomProvider,
    CustomProviderConfig,
    validate_provider_endpoint_url,
)
from keypool.providers.gemini import GeminiProvider
from keypool.providers.groq import GroqProvider
from keypool.providers.openai_compatible import OpenAICompatibleProvider
from keypool.providers.registry import ProviderRegistry
from keypool.providers.replicate import ReplicateProvider

__all__ = [
    "AuthType",
    "AnthropicProvider",
    "BaseProvider",
    "CohereProvider",
    "ConnectionTestResult",
    "CredentialValidationResult",
    "CustomProvider",
    "CustomProviderConfig",
    "GeminiProvider",
    "GroqProvider",
    "ModelDiscoveryResult",
    "ModelInfo",
    "OpenAICompatibleProvider",
    "PROVIDER_CATALOG",
    "ProviderAPIType",
    "ProviderCapability",
    "ProviderCategory",
    "ProviderErrorClassification",
    "ProviderMetadata",
    "ProviderRegistry",
    "ReplicateProvider",
    "get_provider_metadata",
    "parse_retry_duration_seconds",
    "validate_provider_endpoint_url",
]
