"""Provider registry for discovering and instantiating provider adapters."""

from __future__ import annotations

import httpx

from keypool.config.settings import KeypoolSettings
from keypool.core.exceptions import InvalidArgumentError
from keypool.providers.base import BaseProvider
from keypool.providers.gemini import GeminiProvider
from keypool.providers.groq import GroqProvider
from keypool.security.validation import validate_provider_name


class ProviderRegistry:
    """Registry managing supported AI provider adapters."""

    def __init__(
        self,
        settings: KeypoolSettings | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or KeypoolSettings()
        self._http_client = http_client
        self._adapters: dict[str, BaseProvider] = {}
        self._register_builtin_providers()

    def _register_builtin_providers(self) -> None:
        net = self._settings.network
        self.register(
            GroqProvider(
                timeout_seconds=net.http_timeout_seconds,
                connect_timeout_seconds=net.connect_timeout_seconds,
                max_retries=net.max_retries,
                backoff_base_seconds=net.backoff_base_seconds,
                http_client=self._http_client,
            )
        )
        self.register(
            GeminiProvider(
                timeout_seconds=net.http_timeout_seconds,
                connect_timeout_seconds=net.connect_timeout_seconds,
                max_retries=net.max_retries,
                backoff_base_seconds=net.backoff_base_seconds,
                http_client=self._http_client,
            )
        )

    def register(self, provider: BaseProvider) -> None:
        """Register a provider adapter instance."""
        key = provider.name.strip().lower()
        self._adapters[key] = provider

    def get(self, provider_name: str) -> BaseProvider:
        """Return the registered provider adapter or raise InvalidArgumentError."""
        key = validate_provider_name(provider_name)
        adapter = self._adapters.get(key)
        if adapter is None:
            supported = ", ".join(sorted(self._adapters.keys()))
            raise InvalidArgumentError(
                f"Unsupported provider '{provider_name}'. Supported providers: {supported}.",
                hint=f"Run 'keypool providers' to view registered adapters ({supported}).",
            )
        return adapter

    def list_providers(self) -> list[BaseProvider]:
        """Return all registered provider adapters in deterministic order."""
        order = ["groq", "gemini"]
        ordered: list[BaseProvider] = []
        for name in order:
            if name in self._adapters:
                ordered.append(self._adapters[name])
        for name in sorted(self._adapters.keys()):
            if name not in order:
                ordered.append(self._adapters[name])
        return ordered

    def names(self) -> list[str]:
        """Return registered provider identifiers."""
        return [p.name for p in self.list_providers()]

    def is_registered(self, provider_name: str) -> bool:
        return provider_name.strip().lower() in self._adapters
