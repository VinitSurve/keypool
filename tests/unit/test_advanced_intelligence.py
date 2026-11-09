"""Unit and integration tests for KEYPOOL Advanced Intelligence (v3.5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1
from typer.testing import CliRunner

from keypool.cli.app import app
from keypool.core.alias_manager import ModelAliasManager
from keypool.core.benchmarking import BenchmarkEngine
from keypool.notifications.alerts import AlertManager
from keypool.storage.models import HealthState


def test_model_alias_manager_lifecycle(runtime_ctx: Any) -> None:
    mgr = ModelAliasManager(runtime_ctx.db)

    # Builtin aliases seeded by migration v3
    aliases = mgr.list_aliases()
    alias_names = [a["alias"] for a in aliases]
    assert "fast" in alias_names
    assert "smart" in alias_names

    # Create new alias
    res = mgr.set_alias("custom-fast", "groq/llama-3.3-70b-versatile", "Custom fast alias")
    assert res["alias"] == "custom-fast"
    assert res["target"] == "groq/llama-3.3-70b-versatile"

    # Get alias
    retrieved = mgr.get_alias("custom-fast")
    assert retrieved is not None
    assert retrieved["target"] == "groq/llama-3.3-70b-versatile"

    # Remove alias
    assert mgr.remove_alias("custom-fast") is True
    assert mgr.get_alias("custom-fast") is None
    assert mgr.remove_alias("nonexistent") is False


def test_alert_manager_evaluation_and_rules(runtime_ctx: Any) -> None:
    mgr = AlertManager(runtime_ctx)

    # Rules exist
    rules = mgr.list_rules()
    assert len(rules) >= 4

    # Add a healthy credential -> no alerts
    cred = runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Healthy Key",
        secret=FAKE_GROQ_KEY_1,
    )
    alerts = mgr.evaluate_alerts(notify=False)
    assert len(alerts) == 0

    # Mark credential as auth_failed -> triggers alert
    from keypool.providers.base import ProviderErrorClassification

    runtime_ctx.health_manager.record_runtime_failure(
        cred.id,
        ProviderErrorClassification(
            state=HealthState.AUTH_FAILED,
            error_category="auth_failed",
            http_status=401,
            sanitized_message="Invalid token",
        ),
    )
    alerts = mgr.evaluate_alerts(notify=False)
    assert len(alerts) >= 1
    assert any("Credential Failed" in a["title"] for a in alerts)

    # History contains the alert
    history = mgr.list_alerts(limit=10)
    assert len(history) >= 1

    # Test expired credential alert
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Expired Key",
        secret="gsk_EXPIREDTESTKEY000000000000000000000001",
        expires_at="2020-01-01T00:00:00Z",
    )
    exp_alerts = mgr.evaluate_alerts(notify=False)
    assert any("Credential Expired" in a["title"] for a in exp_alerts)

    # Test disabling alert rule
    with runtime_ctx.db.session_scope() as session:
        from keypool.storage.models import AlertRuleRecord

        rule_rec = session.get(AlertRuleRecord, "rule_expiry_7d")
        if rule_rec:
            rule_rec.enabled = False

    exp_alerts_disabled = mgr.evaluate_alerts(notify=False)
    assert not any(a.get("rule_type") == "credential_expiry" for a in exp_alerts_disabled)


@pytest.mark.anyio
async def test_benchmark_engine_execution(runtime_ctx: Any) -> None:
    engine = BenchmarkEngine(runtime_ctx)

    mock_chat_response = {
        "id": "chatcmpl-bench123",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Benchmark test response text."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
    }

    with patch.object(
        engine.forwarder,
        "forward_chat_completion",
        new=AsyncMock(return_value=mock_chat_response),
    ):
        report = await engine.run_benchmark(
            models=["fast", "groq/llama-3.3-70b-versatile"],
            prompt="Hello benchmark!",
            runs=2,
        )

        assert report.runs_per_model == 2
        assert len(report.results) == 2
        assert report.results[0].avg_output_tokens == 20
        assert report.results[0].success_rate == 1.0

        # Verify rich table rendering without error
        table = report.render_rich_table()
        assert table.row_count == 2


def test_cli_advanced_intelligence_commands(isolated_env: Any) -> None:
    runner = CliRunner()

    # Model aliases CLI
    res = runner.invoke(app, ["models", "alias", "list"])
    assert res.exit_code == 0
    assert "fast" in res.stdout

    res_set = runner.invoke(app, ["models", "alias", "set", "myalias", "groq/llama-3.1-8b-instant"])
    assert res_set.exit_code == 0
    assert "mapped to" in res_set.stdout

    res_rm = runner.invoke(app, ["models", "alias", "remove", "myalias"])
    assert res_rm.exit_code == 0
    assert "removed" in res_rm.stdout

    # Alerts CLI
    res_rules = runner.invoke(app, ["alerts", "rules"])
    assert res_rules.exit_code == 0
    assert "rule_budget_80" in res_rules.stdout

    res_check = runner.invoke(app, ["alerts", "check", "--no-notify"])
    assert res_check.exit_code == 0


def test_backup_and_restore_includes_aliases(runtime_ctx: Any, tmp_path: Any) -> None:
    from keypool.core.backup_manager import BackupManager

    alias_mgr = ModelAliasManager(runtime_ctx.db)
    alias_mgr.set_alias("ultra-fast", "groq/llama-3.1-8b-instant", "Ultra fast alias")

    backup_mgr = BackupManager(
        db=runtime_ctx.db,
        secret_store=runtime_ctx.secret_store,
        credential_manager=runtime_ctx.credential_manager,
    )

    backup_file = tmp_path / "backup_alias_test.kpb"
    backup_mgr.create_backup(backup_file, password="test-backup-password-123")
    assert backup_file.exists()

    # Remove alias from DB
    alias_mgr.remove_alias("ultra-fast")
    assert alias_mgr.get_alias("ultra-fast") is None

    # Restore from backup
    report = backup_mgr.restore_backup(backup_file, password="test-backup-password-123")
    assert len(report.warnings) == 0

    # Verify alias was restored
    restored = alias_mgr.get_alias("ultra-fast")
    assert restored is not None
    assert restored["target"] == "groq/llama-3.1-8b-instant"


def test_model_comparison_and_refresh(runtime_ctx: Any) -> None:
    """Verify model comparison metrics and catalog refresh."""
    from keypool.core.model_catalog import ModelCatalogManager

    mcm = ModelCatalogManager(runtime_ctx.db)

    # Compare gpt-4o vs gpt-4o-mini
    cmp_res = mcm.compare_models(
        model_a="gpt-4o",
        model_b="gpt-4o-mini",
        provider_a="openai",
        provider_b="openai",
    )
    assert cmp_res["model_a"]["model_id"] == "gpt-4o"
    assert cmp_res["model_b"]["model_id"] == "gpt-4o-mini"
    assert cmp_res["pricing_comparison"]["cheaper_input_model"] == "gpt-4o-mini"
    assert cmp_res["pricing_comparison"]["cost_ratio_a_to_b"] > 1.0


def test_budget_forecasting_and_model_scopes(runtime_ctx: Any) -> None:
    """Verify budget spend forecasting and model-scoped budgets."""
    from keypool.core.budget_manager import BudgetManager

    bm = BudgetManager(runtime_ctx.db)
    ut = runtime_ctx.usage_tracker

    # Create model-scoped budget
    b = bm.set_budget(
        name="Llama-3.1-Budget",
        amount_limit=25.0,
        scope_type="model",
        scope_target="llama-3.1-8b-instant",
        period="monthly",
        hard_limit_enabled=True,
    )
    assert b.scope_type == "model"
    assert b.scope_target == "llama-3.1-8b-instant"

    # Record usage for this model
    ut.record_event(
        provider="groq",
        model="llama-3.1-8b-instant",
        event_type="completion",
        cost_usd=5.00,
    )

    # Forecast
    fc = bm.forecast_spend(b.id)
    assert fc["current_spend"] == 5.00
    assert fc["projected_spend"] >= 5.00
    assert fc["name"] == "Llama-3.1-Budget"


def test_execution_manager_blocks_on_exceeded_budget(runtime_ctx: Any) -> None:
    """Verify ExecutionManager blocks execution when hard budget limit is exceeded."""
    from keypool.core.budget_manager import BudgetManager
    from keypool.core.exceptions import ExecutionError
    from keypool.core.execution_manager import ExecutionManager

    cm = runtime_ctx.credential_manager
    sel = runtime_ctx.selection_engine
    pm = runtime_ctx.profile_manager
    bm = BudgetManager(runtime_ctx.db)
    ut = runtime_ctx.usage_tracker

    c = cm.add_credential(provider="groq", name="Exec Key", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", c.id)

    # Set hard budget limit of $1.00 for groq
    bm.set_budget(
        name="Groq Hard Limit",
        amount_limit=1.00,
        scope_type="provider",
        scope_target="groq",
        hard_limit_enabled=True,
    )

    # Exceed limit
    ut.record_event(provider="groq", event_type="exec", cost_usd=2.50)

    em = ExecutionManager(
        db=runtime_ctx.db,
        credential_manager=cm,
        selection_engine=sel,
        provider_registry=runtime_ctx.registry,
    )

    with pytest.raises(ExecutionError) as exc_info:
        em.execute(
            provider="groq",
            profile="testing",
            command=["echo", "hello"],
        )
    assert "budget hard limit exceeded" in str(exc_info.value)

