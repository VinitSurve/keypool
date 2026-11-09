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
