"""Unit tests for local usage tracking and JSON serialization."""

from __future__ import annotations

import json

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1

from keypool.cli.formatting import RuntimeContext
from keypool.core.exceptions import InvalidArgumentError
from keypool.core.usage_tracker import parse_period_cutoff
from keypool.integrations.json_output import format_json_envelope
from keypool.storage.models import TokenSource


def test_usage_tracking_and_period_filtering(runtime_ctx: RuntimeContext) -> None:
    """Verify UsageTracker aggregates selections, executions, 429s, and tokens accurately."""
    cm = runtime_ctx.credential_manager
    ut = runtime_ctx.usage_tracker

    cred = cm.add_credential(provider="groq", name="Usage Test Key", secret=FAKE_GROQ_KEY_1)

    ut.record_event(
        provider="groq",
        credential_id=cred.id,
        profile_name="testing",
        event_type="selection",
        status="info",
    )
    ut.record_event(
        provider="groq",
        credential_id=cred.id,
        profile_name="testing",
        event_type="exec_success",
        status="success",
        exit_code=0,
        duration_ms=42.5,
        command_name="python",
    )
    ut.record_event(
        provider="groq",
        credential_id=cred.id,
        profile_name="testing",
        event_type="exec_failure",
        status="failure",
        exit_code=1,
        http_status=429,
        is_rate_limit=True,
        observed_tokens=15,
        token_source=TokenSource.PROVIDER_REPORTED,
    )

    report = ut.get_usage_report(provider="groq", credential_id=cred.id, period="7d")
    assert report.total_selections == 1
    assert report.total_executions == 2
    assert report.successful_executions == 1
    assert report.failed_executions == 1
    assert report.observed_429_events == 1
    assert report.observed_tokens == 15
    assert report.token_source == TokenSource.PROVIDER_REPORTED.value

    with pytest.raises(InvalidArgumentError):
        parse_period_cutoff("invalid_period")


def test_json_envelope_redacts_secrets() -> None:
    """Verify format_json_envelope strips any accidental secret values."""
    rendered = format_json_envelope(
        "test_cmd",
        {
            "safe": "hello",
            "api_key": FAKE_GROQ_KEY_1,
            "nested": f"Error with key {FAKE_GROQ_KEY_1}",
        },
    )
    assert FAKE_GROQ_KEY_1 not in rendered
    parsed = json.loads(rendered)
    assert parsed["ok"] is True
    assert parsed["data"]["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in parsed["data"]["nested"]
