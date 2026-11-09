"""Integration tests for the child process execution engine (`keypool exec`) (Section 12, 21)."""

from __future__ import annotations

import hashlib
import os
import sys

import pytest
from tests.fixtures.provider_payloads import FAKE_GEMINI_KEY_1, FAKE_GROQ_KEY_1

from keypool.cli.formatting import RuntimeContext
from keypool.core.exceptions import ExecutionError, SecurityError


def test_exec_injects_only_target_provider_variable_and_preserves_parent_env(
    runtime_ctx: RuntimeContext,
) -> None:
    """Verify child receives only the target provider key and parent os.environ is untouched."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    g_cred = cm.add_credential(provider="groq", name="Groq Exec Key", secret=FAKE_GROQ_KEY_1)
    m_cred = cm.add_credential(provider="gemini", name="Gemini Exec Key", secret=FAKE_GEMINI_KEY_1)
    pm.assign_credential("testing", g_cred.id)
    pm.assign_credential("testing", m_cred.id)

    parent_snapshot = dict(os.environ)

    # Execute a Python child that checks GROQ_API_KEY (via sha256 digest) and GEMINI_API_KEY
    expected_digest = hashlib.sha256(FAKE_GROQ_KEY_1.encode("utf-8")).hexdigest()
    script = (
        "import hashlib, os, sys; "
        "g = os.environ.get('GROQ_API_KEY', ''); "
        "m = os.environ.get('GEMINI_API_KEY', ''); "
        "d = hashlib.sha256(g.encode('utf-8')).hexdigest() if g else ''; "
        "sys.stdout.write(f'GROQ_SET={bool(g)},DIGEST={d},GEMINI_SET={bool(m)}')"
    )
    res, out, _err = em.execute(
        provider="groq",
        profile="testing",
        command=[sys.executable, "-c", script],
        capture_output=True,
    )
    assert res.exit_code == 0
    assert res.credential_id == g_cred.id
    assert res.env_var_injected == "GROQ_API_KEY"
    assert f"GROQ_SET=True,DIGEST={expected_digest},GEMINI_SET=False" in out

    # Verify parent environment was never mutated
    assert dict(os.environ) == parent_snapshot
    assert "GROQ_API_KEY" not in os.environ


def test_exec_propagates_nonzero_exit_code_and_handles_missing_executable(
    runtime_ctx: RuntimeContext,
) -> None:
    """Verify child exit codes propagate accurately and missing executables raise ExecutionError."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    cred = cm.add_credential(provider="gemini", name="Gemini Key", secret=FAKE_GEMINI_KEY_1)
    pm.assign_credential("testing", cred.id)

    res, _out, _err = em.execute(
        provider="gemini",
        profile="testing",
        command=[sys.executable, "-c", "import sys; sys.exit(42)"],
        capture_output=True,
    )
    assert res.exit_code == 42

    with pytest.raises(ExecutionError):
        em.execute(
            provider="gemini",
            profile="testing",
            command=["nonexistent_binary_xyz_987654"],
        )


def test_exec_controlled_env_mode_and_timeout(
    runtime_ctx: RuntimeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify controlled environment mode strips arbitrary parent vars and enforces timeouts."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    cred = cm.add_credential(provider="groq", name="Groq Key", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", cred.id)

    monkeypatch.setenv("CUSTOM_UNTRUSTED_PARENT_VAR", "should_not_pass_in_controlled_mode")

    script = (
        "import os, sys; "
        "has_custom = 'CUSTOM_UNTRUSTED_PARENT_VAR' in os.environ; "
        "has_groq = 'GROQ_API_KEY' in os.environ; "
        "sys.stdout.write(f'CUSTOM={has_custom},GROQ={has_groq}')"
    )
    res, out, _err = em.execute(
        provider="groq",
        profile="testing",
        command=[sys.executable, "-c", script],
        env_mode="controlled",
        capture_output=True,
    )
    assert res.exit_code == 0
    assert "CUSTOM=False,GROQ=True" in out

    # Test timeout handling
    res_to, _out2, _err2 = em.execute(
        provider="groq",
        profile="testing",
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=0.2,
        capture_output=True,
    )
    assert res_to.timed_out is True
    assert res_to.exit_code == 124


def test_exec_rejects_secret_in_command_arguments(runtime_ctx: RuntimeContext) -> None:
    """Verify ExecutionManager refuses to run if the secret appears in argv."""
    cm = runtime_ctx.credential_manager
    pm = runtime_ctx.profile_manager
    em = runtime_ctx.execution_manager

    cred = cm.add_credential(provider="groq", name="Groq Key", secret=FAKE_GROQ_KEY_1)
    pm.assign_credential("testing", cred.id)

    with pytest.raises(SecurityError):
        em.execute(
            provider="groq",
            profile="testing",
            command=[sys.executable, "-c", f"print('{FAKE_GROQ_KEY_1}')"],
        )
