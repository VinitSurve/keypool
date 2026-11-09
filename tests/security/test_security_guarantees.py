"""Mandatory security tests verifying all 12 security guarantees in Section 21."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from tests.fixtures.provider_payloads import (
    FAKE_GEMINI_KEY_1,
    FAKE_GROQ_KEY_1,
    FAKE_GROQ_KEY_2,
)
from typer.testing import CliRunner

from keypool.cli.app import app
from keypool.cli.formatting import RuntimeContext
from keypool.config.settings import AppPaths, load_settings
from keypool.core.exceptions import (
    CredentialUnavailableError,
    KeychainError,
    KeypoolError,
    SecurityError,
)
from keypool.providers.base import ProviderErrorClassification
from keypool.security.redaction import redact_text, register_secret_for_redaction
from keypool.storage.keychain import InMemorySecretStore
from keypool.storage.models import HealthState

runner = CliRunner()


def test_1_no_api_secret_in_sqlite(runtime_ctx: RuntimeContext) -> None:
    """Assertion 1: Actual API secrets are never stored in SQLite database files."""
    cm = runtime_ctx.credential_manager
    cm.add_credential(provider="groq", name="Secret Check Groq", secret=FAKE_GROQ_KEY_1)
    cm.add_credential(provider="gemini", name="Secret Check Gemini", secret=FAKE_GEMINI_KEY_1)

    for suffix in ("", "-wal", "-shm"):
        db_file = Path(f"{runtime_ctx.paths.db_path}{suffix}")
        if db_file.exists():
            raw_bytes = db_file.read_bytes()
            assert FAKE_GROQ_KEY_1.encode("utf-8") not in raw_bytes
            assert FAKE_GEMINI_KEY_1.encode("utf-8") not in raw_bytes


def test_2_no_api_secret_in_logs(runtime_ctx: RuntimeContext) -> None:
    """Assertion 2: API secrets never enter application log files even when logged directly."""
    register_secret_for_redaction(FAKE_GROQ_KEY_1)
    runtime_ctx.logger.info(
        "Simulated log containing secret %s and %s", FAKE_GROQ_KEY_1, FAKE_GEMINI_KEY_1
    )
    runtime_ctx.logger.error(f"Bearer {FAKE_GROQ_KEY_1} failed with key={FAKE_GEMINI_KEY_1}")

    for handler in runtime_ctx.logger.handlers:
        handler.flush()

    log_content = runtime_ctx.paths.log_file.read_text(encoding="utf-8")
    assert FAKE_GROQ_KEY_1 not in log_content
    assert FAKE_GEMINI_KEY_1 not in log_content
    assert "[REDACTED]" in log_content


def test_3_no_api_secret_in_normal_terminal_output(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Assertion 3: Normal terminal output never exposes API secrets."""
    runner.invoke(
        app,
        ["add", "groq", "--name", "Groq Terminal Test"],
        input=f"{FAKE_GROQ_KEY_1}\n",
    )
    for cmd_args in (
        [],
        ["list"],
        ["status"],
        ["health"],
        ["providers"],
        ["doctor"],
        ["usage"],
        ["get", "--provider", "groq"],
    ):
        res = runner.invoke(app, cmd_args)
        assert FAKE_GROQ_KEY_1 not in res.output


def test_4_no_api_secret_in_exception_messages() -> None:
    """Assertion 4: Exception messages automatically scrub any secret values."""
    register_secret_for_redaction(FAKE_GROQ_KEY_1)
    err = KeypoolError(
        f"Failed request using {FAKE_GROQ_KEY_1} and {FAKE_GEMINI_KEY_1}",
        hint=f"Check key {FAKE_GROQ_KEY_1}",
    )
    assert FAKE_GROQ_KEY_1 not in str(err)
    assert FAKE_GEMINI_KEY_1 not in str(err)
    assert FAKE_GROQ_KEY_1 not in (err.hint or "")
    assert "[REDACTED]" in str(err)


def test_5_no_api_secret_in_json_metadata(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Assertion 5: JSON status and metadata responses never contain secrets."""
    runner.invoke(
        app,
        ["add", "groq", "--name", "Groq JSON Test"],
        input=f"{FAKE_GROQ_KEY_1}\n",
    )
    for cmd_args in (
        ["status", "--json"],
        ["list", "--json"],
        ["health", "--json"],
        ["providers", "--json"],
        ["profile", "list", "--json"],
        ["doctor", "--json"],
        ["usage", "--json"],
        ["get", "--provider", "groq", "--json"],
    ):
        res = runner.invoke(app, cmd_args)
        assert res.exit_code == 0
        assert FAKE_GROQ_KEY_1 not in res.stdout

    # Combining --show and --json is explicitly forbidden
    res_forbidden = runner.invoke(app, ["get", "--provider", "groq", "--show", "--json"])
    assert res_forbidden.exit_code != 0
    assert FAKE_GROQ_KEY_1 not in res_forbidden.output


def test_6_no_api_secret_in_temporary_or_config_files(runtime_ctx: RuntimeContext) -> None:
    """Assertion 6: No plaintext .env or config files contain secrets; config.toml rejects secrets."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    cred = cm.add_credential(provider="groq", name="Temp File Test", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", cred.id)

    em.execute(
        provider="groq",
        profile="testing",
        command=[sys.executable, "-c", "pass"],
        capture_output=True,
    )

    for file_path in runtime_ctx.paths.app_dir.rglob("*"):
        if file_path.is_file():
            data = file_path.read_bytes()
            assert FAKE_GROQ_KEY_1.encode("utf-8") not in data

    # Verify config.toml rejects secret keys
    runtime_ctx.paths.config_path.write_text(
        f'default_profile = "testing"\napi_key = "{FAKE_GROQ_KEY_1}"\n',
        encoding="utf-8",
    )
    with pytest.raises(SecurityError):
        load_settings(runtime_ctx.paths)


def test_7_no_api_secret_in_process_arguments(runtime_ctx: RuntimeContext) -> None:
    """Assertion 7: Child process receives credential via env var, never via process argv."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    cred = cm.add_credential(provider="groq", name="Argv Test", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", cred.id)

    res, out, _err = em.execute(
        provider="groq",
        profile="testing",
        command=[sys.executable, "-c", "import sys; print('ARGV:', ' '.join(sys.argv))"],
        capture_output=True,
    )
    assert res.exit_code == 0
    assert FAKE_GROQ_KEY_1 not in out


def test_8_disabled_credentials_are_never_selected(runtime_ctx: RuntimeContext) -> None:
    """Assertion 8: Disabled credentials are never selected."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    sel = runtime_ctx.selection_engine

    cred = cm.add_credential(provider="groq", name="Disabled Key", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", cred.id)
    cm.disable_credential(cred.id)

    with pytest.raises(CredentialUnavailableError):
        sel.select_credential(provider="groq", profile="testing")


def test_9_testing_cannot_silently_use_production_credentials(runtime_ctx: RuntimeContext) -> None:
    """Assertion 9: Testing profile cannot silently fall back to production credentials."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    sel = runtime_ctx.selection_engine

    prod_cred = cm.add_credential(provider="groq", name="Prod Key", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("production", prod_cred.id)

    # Requesting 'testing' profile must fail rather than crossing into 'production'
    with pytest.raises(CredentialUnavailableError):
        sel.select_credential(provider="groq", profile="testing")


def test_10_keychain_errors_never_trigger_plaintext_fallback(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Assertion 10: Keychain errors raise KeychainError and never write secrets to disk."""
    paths, secret_store = isolated_env
    ctx = RuntimeContext(paths=paths)
    secret_store.fail_on_set = KeychainError("Keychain locked")

    with pytest.raises(KeychainError):
        ctx.credential_manager.add_credential(
            provider="groq", name="No Fallback Key", secret=FAKE_GROQ_KEY_1
        )

    for file_path in paths.app_dir.rglob("*"):
        if file_path.is_file():
            assert FAKE_GROQ_KEY_1.encode("utf-8") not in file_path.read_bytes()


def test_11_failed_requests_cannot_trigger_infinite_retries(runtime_ctx: RuntimeContext) -> None:
    """Assertion 11: Fallback execution strictly bounds retries and avoids retrying auth failures."""
    cm = runtime_ctx.credential_manager
    sel = runtime_ctx.selection_engine

    cm.add_credential(provider="groq", name="Fail 1", secret=FAKE_GROQ_KEY_1)
    cm.add_credential(provider="groq", name="Fail 2", secret=FAKE_GROQ_KEY_2)

    calls = 0

    def failing_op(_sel: object, _secret: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("401 unauthorized")

    def classify(_exc: Exception) -> ProviderErrorClassification:
        return ProviderErrorClassification(
            state=HealthState.AUTH_FAILED,
            error_category="invalid_api_key",
            http_status=401,
            sanitized_message=redact_text("Invalid key"),
            is_retryable=False,
        )

    with pytest.raises(CredentialUnavailableError):
        sel.execute_with_fallback(
            provider="groq",
            operation=failing_op,
            classify_failure=classify,
            max_attempts=10,
        )
    # Only 2 credentials exist, each tried once -> bounded at 2 calls
    assert calls == 2


def test_12_parent_shell_environment_remains_unchanged(runtime_ctx: RuntimeContext) -> None:
    """Assertion 12: Parent process os.environ is identical before and after child execution."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    cred = cm.add_credential(provider="groq", name="Env Check", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", cred.id)

    before = dict(os.environ)
    em.execute(
        provider="groq",
        profile="testing",
        command=[sys.executable, "-c", "import os; assert 'GROQ_API_KEY' in os.environ"],
        capture_output=True,
    )
    after = dict(os.environ)
    assert before == after
    assert "GROQ_API_KEY" not in os.environ
