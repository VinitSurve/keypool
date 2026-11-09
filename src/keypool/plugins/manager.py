"""Plugin manager discovering, validating, and activating extensions (Section 21)."""

from __future__ import annotations

import contextlib

from keypool.cli.formatting import RuntimeContext
from keypool.core.exceptions import InvalidArgumentError
from keypool.plugins.base import BasePlugin, PluginMetadata
from keypool.plugins.examples import AuditLoggerPlugin, MockCustomProviderPlugin


class PluginManager:
    """Manages plugin discovery, lifecycle, and safety boundaries."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self._ctx = ctx
        self._plugins: dict[str, BasePlugin] = {}
        self._register_default_plugins()

    def _register_default_plugins(self) -> None:
        """Register built-in example plugins in disabled state by default."""
        mock_prov = MockCustomProviderPlugin()
        mock_prov.enabled = False
        self.register_plugin(mock_prov)

        audit_log = AuditLoggerPlugin()
        audit_log.enabled = False
        self.register_plugin(audit_log)

    def register_plugin(self, plugin: BasePlugin) -> None:
        """Register a plugin instance."""
        self._plugins[plugin.id] = plugin
        if plugin.enabled:
            plugin.on_load(self._ctx)

    def list_plugins(self) -> list[PluginMetadata]:
        """List metadata for all known plugins."""
        return [p.get_metadata() for p in self._plugins.values()]

    def enable_plugin(self, plugin_id: str) -> PluginMetadata:
        """Enable an installed plugin."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise InvalidArgumentError(f"Plugin '{plugin_id}' not found.")
        plugin.enabled = True
        plugin.on_load(self._ctx)
        return plugin.get_metadata()

    def disable_plugin(self, plugin_id: str) -> PluginMetadata:
        """Disable an active plugin."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise InvalidArgumentError(f"Plugin '{plugin_id}' not found.")
        plugin.enabled = False
        plugin.on_unload()
        return plugin.get_metadata()

    def notify_credential_selected(
        self, credential_id: str, provider: str, profile: str | None
    ) -> None:
        """Dispatch lifecycle event to active integration plugins."""
        for plugin in self._plugins.values():
            if plugin.enabled and hasattr(plugin, "on_credential_selected"):
                with contextlib.suppress(Exception):
                    plugin.on_credential_selected(credential_id, provider, profile)
