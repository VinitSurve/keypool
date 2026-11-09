"""Example provider and integration plugins (Section 21)."""

from __future__ import annotations

from pathlib import Path

from keypool.plugins.base import IntegrationPlugin, ProviderPlugin
from keypool.providers.base import BaseProvider
from keypool.providers.openai_compatible import OpenAICompatibleProvider
from keypool.storage.models import utc_now_iso


class MockCustomProviderPlugin(ProviderPlugin):
    """Example provider plugin providing custom internal model routing."""

    id = "mock_provider_plugin"
    name = "Mock Custom Provider Plugin"
    version = "1.0.0"
    author = "KEYPOOL Examples"
    description = "Example plugin providing an internal custom mock provider adapter."

    def get_provider(self) -> BaseProvider:
        return OpenAICompatibleProvider(
            name="custom_mock",
            display_name="Custom Mock Provider",
            base_url="https://mock.ai.internal/v1",
            env_var_name="MOCK_AI_KEY",
            default_test_model="mock-gpt",
        )


class AuditLoggerPlugin(IntegrationPlugin):
    """Example integration plugin logging execution and selection events to disk."""

    id = "audit_logger_plugin"
    name = "Audit Logger Integration"
    version = "1.0.0"
    author = "KEYPOOL Examples"
    description = "Logs sanitized credential selection and execution events to a local audit file."

    def __init__(self, log_dir: Path | None = None) -> None:
        super().__init__()
        self._log_dir = log_dir

    def on_credential_selected(
        self, credential_id: str, provider: str, profile: str | None
    ) -> None:
        self._write_entry(
            f"CREDENTIAL_SELECTED id={credential_id} provider={provider} profile={profile}"
        )

    def on_execution_completed(self, provider: str, exit_code: int, duration_ms: float) -> None:
        self._write_entry(
            f"EXECUTION_COMPLETED provider={provider} exit={exit_code} duration={duration_ms:.1f}ms"
        )

    def _write_entry(self, line: str) -> None:
        try:
            target_dir = self._log_dir or (
                Path.home() / "Library" / "Application Support" / "keypool"
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            log_file = target_dir / "audit.log"
            with log_file.open("a", encoding="utf-8") as f:
                f.write(f"[{utc_now_iso()}] {line}\n")
        except Exception:  # noqa: S110
            pass
