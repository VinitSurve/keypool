"""Comprehensive provider registry for discovering and instantiating provider adapters."""

from __future__ import annotations

import httpx

from keypool.config.settings import KeypoolSettings
from keypool.core.exceptions import InvalidArgumentError
from keypool.providers.anthropic import AnthropicProvider
from keypool.providers.base import BaseProvider
from keypool.providers.capabilities import (
    PROVIDER_CATALOG,
    AuthType,
    ProviderAPIType,
    ProviderCategory,
    ProviderMetadata,
)
from keypool.providers.cohere import CohereProvider
from keypool.providers.custom import CustomProvider, CustomProviderConfig
from keypool.providers.gemini import GeminiProvider
from keypool.providers.groq import GroqProvider
from keypool.providers.openai_compatible import OpenAICompatibleProvider
from keypool.providers.replicate import ReplicateProvider
from keypool.security.validation import validate_provider_name


class ProviderRegistry:
    """Registry managing all supported AI provider adapters."""

    def __init__(
        self,
        settings: KeypoolSettings | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or KeypoolSettings()
        self._http_client = http_client
        self._adapters: dict[str, BaseProvider] = {}
        self._custom_configs: dict[str, CustomProviderConfig] = {}
        self._register_all_providers()

    def _register_all_providers(self) -> None:
        net = self._settings.network
        to = net.http_timeout_seconds
        cto = net.connect_timeout_seconds
        mr = net.max_retries
        bb = net.backoff_base_seconds
        hc = self._http_client

        # 1. Native Specialized Adapters
        self.register(GroqProvider(timeout_seconds=to, connect_timeout_seconds=cto, max_retries=mr, backoff_base_seconds=bb, http_client=hc))
        self.register(GeminiProvider(timeout_seconds=to, connect_timeout_seconds=cto, max_retries=mr, backoff_base_seconds=bb, http_client=hc))
        self.register(AnthropicProvider(timeout_seconds=to, connect_timeout_seconds=cto, max_retries=mr, backoff_base_seconds=bb, http_client=hc))
        self.register(CohereProvider(timeout_seconds=to, connect_timeout_seconds=cto, max_retries=mr, backoff_base_seconds=bb, http_client=hc))
        self.register(ReplicateProvider(timeout_seconds=to, connect_timeout_seconds=cto, max_retries=mr, backoff_base_seconds=bb, http_client=hc))

        # 2. Adapters for Catalog entries
        for name, meta in PROVIDER_CATALOG.items():
            if name in self._adapters:
                continue

            # Special header handling for Azure OpenAI
            auth_header = "Authorization"
            auth_scheme = "Bearer"
            if meta.auth_type == AuthType.API_KEY_HEADER:
                auth_header = "api-key"
                auth_scheme = ""

            requires_key = meta.auth_type != AuthType.NONE_REQUIRED

            adapter = OpenAICompatibleProvider(
                name=meta.name,
                display_name=meta.display_name,
                base_url=meta.base_url,
                env_var_name=meta.env_var_name,
                default_test_model=meta.default_test_model,
                auth_header=auth_header,
                auth_scheme=auth_scheme,
                requires_key=requires_key,
                timeout_seconds=to,
                connect_timeout_seconds=cto,
                max_retries=mr,
                backoff_base_seconds=bb,
                http_client=hc,
            )
            self.register(adapter)

    def register(self, provider: BaseProvider) -> None:
        """Register a provider adapter instance."""
        key = provider.name.strip().lower()
        self._adapters[key] = provider

    def register_custom(self, config: CustomProviderConfig) -> CustomProvider:
        """Register a user-defined custom provider."""
        provider = CustomProvider(config, http_client=self._http_client)
        self.register(provider)
        self._custom_configs[config.name.strip().lower()] = config
        return provider

    def get(self, provider_name: str) -> BaseProvider:
        """Return the registered provider adapter or raise InvalidArgumentError."""
        key = validate_provider_name(provider_name)
        adapter = self._adapters.get(key)
        if adapter is None:
            supported = ", ".join(sorted(self._adapters.keys()))
            raise InvalidArgumentError(
                f"Unsupported provider '{provider_name}'. Supported providers: {supported}.",
                hint="Run 'keypool providers' to view registered adapters.",
            )
        return adapter

    def get_metadata(self, provider_name: str) -> ProviderMetadata | None:
        """Return public capability metadata for a provider."""
        key = provider_name.strip().lower()
        if key in PROVIDER_CATALOG:
            return PROVIDER_CATALOG[key]
        if key in self._custom_configs:
            cfg = self._custom_configs[key]
            return ProviderMetadata(
                name=cfg.name,
                display_name=cfg.display_name,
                category=ProviderCategory.CUSTOM,
                api_type=ProviderAPIType.CUSTOM,
                auth_type=AuthType.BEARER_TOKEN if cfg.auth_scheme == "Bearer" else AuthType.API_KEY_HEADER,
                env_var_name=cfg.env_var_name,
                base_url=cfg.base_url,
                default_test_model=cfg.default_test_model,
                docs_url="https://platform.openai.com/docs/api-reference",
            )
        return None

    def list_providers(self) -> list[BaseProvider]:
        """Return all registered provider adapters in stable order."""
        priority = ["openai", "anthropic", "gemini", "groq", "mistral", "deepseek"]
        ordered: list[BaseProvider] = []
        for p in priority:
            if p in self._adapters:
                ordered.append(self._adapters[p])
        for name in sorted(self._adapters.keys()):
            if name not in priority:
                ordered.append(self._adapters[name])
        return ordered

    def names(self) -> list[str]:
        """Return registered provider identifiers."""
        return [p.name for p in self.list_providers()]

    def is_registered(self, provider_name: str) -> bool:
        return provider_name.strip().lower() in self._adapters
