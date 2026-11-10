"""Comprehensive CLI command tests exercising every implemented command (Section 17, 21)."""

from __future__ import annotations

import json
import sys

import httpx
from tests.fixtures.provider_payloads import (
    FAKE_GEMINI_KEY_1,
    FAKE_GROQ_KEY_1,
    FAKE_GROQ_KEY_2,
    GEMINI_GENERATE_SUCCESS_PAYLOAD,
    GEMINI_MODELS_SUCCESS_PAYLOAD,
    GROQ_CHAT_COMPLETION_PAYLOAD,
    GROQ_MODELS_SUCCESS_PAYLOAD,
)
from typer.testing import CliRunner

from keypool.cli.app import app
from keypool.cli.formatting import configure_test_overrides
from keypool.config.settings import AppPaths
from keypool.core.exceptions import ExitCode
from keypool.storage.keychain import InMemorySecretStore

runner = CliRunner()


def test_cli_root_help_version_and_empty_dashboard(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify `keypool`, `keypool --help`, `keypool --version`, and empty states."""
    res_ver = runner.invoke(app, ["--version"])
    assert res_ver.exit_code == 0
    assert "keypool 5.0.0" in res_ver.stdout

    res_help = runner.invoke(app, ["--help"])
    assert res_help.exit_code == 0
    assert "LOCAL AI API CREDENTIAL MANAGER" in res_help.stdout.upper()

    res_dash = runner.invoke(app, [])
    assert res_dash.exit_code == 0
    assert "SYSTEM OVERVIEW" in res_dash.stdout
    assert "PROVIDERS" in res_dash.stdout

    res_empty_list = runner.invoke(app, ["list"])
    assert res_empty_list.exit_code == 0
    assert "No credentials configured" in res_empty_list.stdout

    res_empty_test = runner.invoke(app, ["test", "--provider", "groq"])
    assert res_empty_test.exit_code == 0
    assert "LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED" in res_empty_test.stdout


def test_cli_full_credential_profile_and_exec_lifecycle(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Test complete CLI workflow across add, list, show, rename, update, profile, test, exec, get, disable, enable, remove."""
    paths, secret_store = isolated_env

    def handler(req: httpx.Request) -> httpx.Response:
        if "groq.com" in str(req.url):
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json=GROQ_MODELS_SUCCESS_PAYLOAD)
            return httpx.Response(200, json=GROQ_CHAT_COMPLETION_PAYLOAD)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=GEMINI_MODELS_SUCCESS_PAYLOAD)
        return httpx.Response(200, json=GEMINI_GENERATE_SUCCESS_PAYLOAD)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    configure_test_overrides(secret_store=secret_store, http_client=mock_client, paths=paths)

    # 1. Add Groq credential interactively
    res_add = runner.invoke(
        app,
        ["add", "groq"],
        input=f"Groq Testing 01\n{FAKE_GROQ_KEY_1}\n",
    )
    assert res_add.exit_code == 0, res_add.output
    assert "Credential added successfully" in res_add.stdout
    assert FAKE_GROQ_KEY_1 not in res_add.stdout

    # 2. Add Gemini credential interactively
    res_add_gem = runner.invoke(
        app,
        ["add", "gemini", "--name", "Gemini Testing 01"],
        input=f"{FAKE_GEMINI_KEY_1}\n",
    )
    assert res_add_gem.exit_code == 0, res_add_gem.output

    # 3. List JSON to retrieve generated IDs
    res_list_json = runner.invoke(app, ["list", "--json"])
    assert res_list_json.exit_code == 0
    list_payload = json.loads(res_list_json.stdout)
    assert list_payload["ok"] is True
    assert list_payload["data"]["count"] == 2
    groq_id = next(c["id"] for c in list_payload["data"]["credentials"] if c["provider"] == "groq")
    gemini_id = next(
        c["id"] for c in list_payload["data"]["credentials"] if c["provider"] == "gemini"
    )

    # 4. Show credential metadata
    res_show = runner.invoke(app, ["show", groq_id])
    assert res_show.exit_code == 0
    assert "Groq Testing 01" in res_show.stdout
    assert FAKE_GROQ_KEY_1 not in res_show.stdout

    # 5. Rename credential
    res_rename = runner.invoke(app, ["rename", groq_id, "Groq Primary 01"])
    assert res_rename.exit_code == 0

    # 6. Update credential secret with confirmation
    res_update = runner.invoke(
        app,
        ["update", groq_id, "--replace-key", "--yes"],
        input=f"{FAKE_GROQ_KEY_2}\n",
    )
    assert res_update.exit_code == 0
    assert FAKE_GROQ_KEY_2 not in res_update.stdout

    # 7. Test credential and provider
    res_test_id = runner.invoke(app, ["test", groq_id])
    assert res_test_id.exit_code == 0
    res_test_gem = runner.invoke(app, ["test", "--provider", "gemini"])
    assert res_test_gem.exit_code == 0

    # 8. Health & Status & Providers
    assert runner.invoke(app, ["health"]).exit_code == 0
    assert runner.invoke(app, ["health", "--refresh"]).exit_code == 0
    assert runner.invoke(app, ["status"]).exit_code == 0
    assert runner.invoke(app, ["providers"]).exit_code == 0
    assert runner.invoke(app, ["providers", "show", "groq"]).exit_code == 0
    assert runner.invoke(app, ["providers", "show", "gemini"]).exit_code == 0

    # 9. Profiles: create, list, assign, show, unassign, delete
    assert runner.invoke(app, ["profile", "create", "staging"]).exit_code == 0
    assert runner.invoke(app, ["profile", "assign", "testing", groq_id]).exit_code == 0
    assert runner.invoke(app, ["profile", "assign", "testing", gemini_id]).exit_code == 0
    assert runner.invoke(app, ["profile", "assign", "staging", groq_id]).exit_code == 0
    assert runner.invoke(app, ["profile", "show", "testing"]).exit_code == 0
    assert runner.invoke(app, ["profile", "unassign", "staging", groq_id]).exit_code == 0
    assert runner.invoke(app, ["profile", "delete", "staging", "--yes"]).exit_code == 0

    # 10. Exec command
    res_exec = runner.invoke(
        app,
        [
            "exec",
            "--profile",
            "testing",
            "--provider",
            "groq",
            "--",
            sys.executable,
            "-c",
            "import os, sys; sys.exit(0 if os.environ.get('GROQ_API_KEY') else 9)",
        ],
    )
    assert res_exec.exit_code == 0

    # 11. Get command (metadata vs --show)
    res_get_meta = runner.invoke(app, ["get", "--provider", "groq"])
    assert res_get_meta.exit_code == 0
    assert FAKE_GROQ_KEY_2 not in res_get_meta.stdout

    res_get_show = runner.invoke(app, ["get", "--provider", "groq", "--show"])
    assert res_get_show.exit_code == 0
    assert FAKE_GROQ_KEY_2 in res_get_show.stdout

    # 12. Disable -> cannot be selected -> Enable -> can be selected
    assert runner.invoke(app, ["disable", groq_id]).exit_code == 0
    res_get_disabled = runner.invoke(app, ["get", "--provider", "groq"])
    assert res_get_disabled.exit_code == int(ExitCode.CREDENTIAL_UNAVAILABLE)

    assert runner.invoke(app, ["enable", groq_id]).exit_code == 0
    assert runner.invoke(app, ["get", "--provider", "groq"]).exit_code == 0

    # 13. Usage & Doctor
    assert runner.invoke(app, ["usage", "--period", "7d", "--provider", "groq"]).exit_code == 0
    assert runner.invoke(app, ["doctor"]).exit_code == 0

    # 14. All required --json endpoints produce valid JSON free of secrets
    for json_args in [
        ["status", "--json"],
        ["list", "--json"],
        ["health", "--json"],
        ["providers", "--json"],
        ["profile", "list", "--json"],
        ["doctor", "--json"],
        ["usage", "--json"],
    ]:
        res_j = runner.invoke(app, json_args)
        assert res_j.exit_code == 0, f"Failed for {json_args}: {res_j.output}"
        parsed = json.loads(res_j.stdout)
        assert parsed["ok"] is True
        assert FAKE_GROQ_KEY_2 not in res_j.stdout
        assert FAKE_GEMINI_KEY_1 not in res_j.stdout

    # 15. Remove credential
    res_rm = runner.invoke(app, ["remove", groq_id, "--yes"])
    assert res_rm.exit_code == 0
    assert not secret_store.secret_exists(f"keypool:{groq_id}")


def test_cli_error_exit_codes(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify standardized CLI exit codes for invalid arguments and missing credentials."""
    res_bad_prov = runner.invoke(app, ["list", "--provider", "invalid_provider"])
    assert res_bad_prov.exit_code == int(ExitCode.INVALID_ARGUMENTS)

    res_bad_id = runner.invoke(app, ["show", "bad_id"])
    assert res_bad_id.exit_code == int(ExitCode.INVALID_ARGUMENTS)

    res_missing_id = runner.invoke(app, ["show", "cred_deadbeef"])
    assert res_missing_id.exit_code == int(ExitCode.CREDENTIAL_UNAVAILABLE)

    res_missing_prof = runner.invoke(app, ["profile", "show", "nonexistent_prof"])
    assert res_missing_prof.exit_code == int(ExitCode.CREDENTIAL_UNAVAILABLE)


def test_cli_expanded_commands(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify reminders, model comparison, budget forecasting, and TUI view flags."""
    # 1. Reminders
    res_rem = runner.invoke(app, ["reminders", "--json"])
    assert res_rem.exit_code == 0
    assert '"reminders"' in res_rem.stdout

    # 2. Models Compare
    res_cmp = runner.invoke(app, ["models", "compare", "gpt-4o", "gpt-4o-mini", "--json"])
    assert res_cmp.exit_code == 0
    assert '"context_comparison"' in res_cmp.stdout

    # 3. Usage CSV
    res_csv = runner.invoke(app, ["usage", "--csv"])
    assert res_csv.exit_code == 0
    assert "occurred_at" in res_csv.stdout

    # 4. TUI view rendering
    res_tui = runner.invoke(app, ["tui", "--view", "overview"])
    assert res_tui.exit_code == 0
    assert "KEYPOOL" in res_tui.stdout

    res_tui_prov = runner.invoke(app, ["tui", "--view", "providers"])
    assert res_tui_prov.exit_code == 0
    assert "Registered Provider" in res_tui_prov.stdout


def test_cli_budgets_and_models_lifecycle(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify budget CRUD, forecasting, and models catalog/alias commands."""
    # 1. Budget set
    res_set = runner.invoke(
        app,
        ["budget", "set", "Monthly Cap", "-a", "100.0", "-s", "global", "-p", "monthly", "--json"],
    )
    assert res_set.exit_code == 0
    b_data = json.loads(res_set.stdout)
    b_id = b_data["data"]["id"]

    # 2. Budget list
    res_list = runner.invoke(app, ["budget", "list", "--json"])
    assert res_list.exit_code == 0
    assert any(b["id"] == b_id for b in json.loads(res_list.stdout)["data"]["budgets"])

    # 3. Budget check
    res_chk = runner.invoke(app, ["budget", "check", "--json"])
    assert res_chk.exit_code == 0

    # 4. Budget forecast
    res_fc = runner.invoke(app, ["budget", "forecast", b_id, "--json"])
    assert res_fc.exit_code == 0
    assert "projected_spend" in res_fc.stdout

    # 5. Budget delete
    res_del = runner.invoke(app, ["budget", "delete", b_id, "--json"])
    assert res_del.exit_code == 0

    # 6. Models list and search
    res_mlist = runner.invoke(app, ["models", "list", "--json"])
    assert res_mlist.exit_code == 0

    res_msearch = runner.invoke(app, ["models", "search", "gpt", "--json"])
    assert res_msearch.exit_code == 0

    res_mshow = runner.invoke(app, ["models", "show", "gpt-4o", "-p", "openai", "--json"])
    assert res_mshow.exit_code == 0

    # 7. Model aliases
    res_alias_set = runner.invoke(
        app, ["models", "alias", "set", "my_fast", "openai/gpt-4o-mini", "--json"]
    )
    assert res_alias_set.exit_code == 0
    res_alias_list = runner.invoke(app, ["models", "alias", "list", "--json"])
    assert res_alias_list.exit_code == 0
    res_alias_rm = runner.invoke(app, ["models", "alias", "remove", "my_fast", "--json"])
    assert res_alias_rm.exit_code == 0


def test_cli_bulk_and_export_lifecycle(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify bulk operations, history audit timeline, and encrypted bundle export/import."""
    paths, secret_store = isolated_env
    # Add a credential
    res_add = runner.invoke(
        app,
        ["add", "groq", "--name", "Bulk Test Key"],
        input=f"{FAKE_GROQ_KEY_1}\n",
    )
    assert res_add.exit_code == 0
    res_list = runner.invoke(app, ["list", "--json"])
    groq_id = json.loads(res_list.stdout)["data"]["credentials"][0]["id"]

    # Bulk tag
    res_tag = runner.invoke(app, ["bulk-tag", groq_id, "--tag", "production", "--json"])
    assert res_tag.exit_code == 0

    # Bulk disable
    res_dis = runner.invoke(app, ["bulk-disable", groq_id, "--json"])
    assert res_dis.exit_code == 0

    # Bulk enable
    res_en = runner.invoke(app, ["bulk-enable", groq_id, "--json"])
    assert res_en.exit_code == 0

    # History
    res_hist = runner.invoke(app, ["history", groq_id, "--json"])
    assert res_hist.exit_code == 0

    # Export keys
    bundle_path = str(paths.app_dir / "test_export.kpbundle")
    res_exp = runner.invoke(
        app,
        ["export-keys", "--output", bundle_path, "--passphrase", "SecretPassphrase123"],
    )
    assert res_exp.exit_code == 0

    # Import keys
    res_imp = runner.invoke(
        app,
        ["import-keys", bundle_path, "--passphrase", "SecretPassphrase123", "--allow-duplicate"],
    )
    assert res_imp.exit_code == 0


def test_cli_tui_all_tabs(
    isolated_env: tuple[AppPaths, InMemorySecretStore],
) -> None:
    """Verify that all 12 TUI views render without crashing."""
    tabs = [
        "overview",
        "credentials",
        "providers",
        "health",
        "models",
        "profiles",
        "projects",
        "usage",
        "budgets",
        "gateway",
        "settings",
        "diagnostics",
    ]
    for tab in tabs:
        res = runner.invoke(app, ["tui", "--view", tab])
        assert res.exit_code == 0, f"TUI failed on tab {tab}: {res.output}"


