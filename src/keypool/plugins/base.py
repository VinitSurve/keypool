"""Extensible plugin contracts and lifecycle interfaces (Section 21)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from keypool.cli.formatting import RuntimeContext
from keypool.providers.base import BaseProvider


class PluginMetadata(BaseModel):
    """Metadata describing a KEYPOOL plugin."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    author: str
    description: str
    plugin_type: str  # "provider" or "integration"
    enabled: bool = True


class BasePlugin(ABC):
    """Abstract base class for all KEYPOOL plugins."""

    id: str
    name: str
    version: str
    author: str
    description: str
    plugin_type: str = "generic"

    def __init__(self) -> None:
        self.enabled: bool = True

    @abstractmethod
    def on_load(self, context: RuntimeContext) -> None:
        """Invoked when the plugin is loaded into the runtime."""

    @abstractmethod
    def on_unload(self) -> None:
        """Invoked when the plugin is disabled or unloaded."""

    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id=self.id,
            name=self.name,
            version=self.version,
            author=self.author,
            description=self.description,
            plugin_type=self.plugin_type,
            enabled=self.enabled,
        )


class ProviderPlugin(BasePlugin):
    """Plugin providing a custom or third-party AI provider adapter."""

    plugin_type: str = "provider"

    @abstractmethod
    def get_provider(self) -> BaseProvider:
        """Return the provider adapter instance to register."""

    def on_load(self, context: RuntimeContext) -> None:
        provider = self.get_provider()
        context.registry.register(provider)

    def on_unload(self) -> None:
        pass


class IntegrationPlugin(BasePlugin):
    """Plugin hooking into credential lifecycle, selection, and execution events."""

    plugin_type: str = "integration"

    def on_credential_selected(
        self, credential_id: str, provider: str, profile: str | None
    ) -> None:
        """Lifecycle hook when a credential is selected."""

    def on_execution_completed(self, provider: str, exit_code: int, duration_ms: float) -> None:
        """Lifecycle hook when child execution completes."""

    def on_load(self, context: RuntimeContext) -> None:
        pass

    def on_unload(self) -> None:
        pass
