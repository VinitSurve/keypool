"""Secure child process execution engine for KEYPOOL (Section 12)."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from keypool.config.settings import KeypoolSettings
from keypool.core.budget_manager import BudgetManager
from keypool.core.credential_manager import CredentialManager
from keypool.core.exceptions import ExecutionError, InvalidArgumentError
from keypool.core.selection_engine import SelectedCredential, SelectionEngine
from keypool.core.usage_tracker import UsageTracker
from keypool.providers.registry import ProviderRegistry
from keypool.security.environment import (
    build_child_environment,
    verify_command_contains_no_secret,
)
from keypool.security.validation import validate_profile_name
from keypool.storage.database import DatabaseManager
from keypool.storage.models import CredentialRecord, utc_now_iso


class ExecutionResult(BaseModel):
    """Summary of a completed child process execution (contains no secrets)."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    credential_id: str
    provider: str
    profile: str
    env_var_injected: str
    executable: str
    duration_ms: float
    timed_out: bool = False
    interrupted: bool = False


class ExecutionManager:
    """Executes child processes with isolated provider credential injection."""

    def __init__(
        self,
        db: DatabaseManager,
        credential_manager: CredentialManager,
        selection_engine: SelectionEngine,
        provider_registry: ProviderRegistry,
        settings: KeypoolSettings | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._db = db
        self._cred_manager = credential_manager
        self._selection = selection_engine
        self._registry = provider_registry
        self._settings = settings or KeypoolSettings()
        self._usage = usage_tracker or UsageTracker(db)

    @staticmethod
    def resolve_executable(command: Sequence[str], search_path: str | None = None) -> str:
        """Validate that the target command is non-empty and its executable exists."""
        if not command:
            raise InvalidArgumentError(
                "No command specified to execute.",
                hint="Pass the child command after '--', e.g.: keypool exec --profile testing --provider groq -- python app.py",
            )
        prog = command[0].strip()
        if not prog:
            raise InvalidArgumentError("Executable name cannot be empty.")

        # Check direct path if it contains a path separator
        if os.sep in prog or (os.altsep and os.altsep in prog):
            prog_path = Path(prog).expanduser()
            if prog_path.is_file() and os.access(prog_path, os.X_OK):
                return str(prog_path)
            raise ExecutionError(
                f"Executable '{prog}' was not found or is not executable.",
                hint="Verify the path and file permissions of the target executable.",
            )

        resolved = shutil.which(prog, path=search_path)
        if resolved is None:
            raise ExecutionError(
                f"Executable '{prog}' not found on PATH.",
                hint="Ensure the command is installed and available in your PATH.",
            )
        return resolved

    def execute(
        self,
        *,
        provider: str,
        profile: str,
        command: Sequence[str],
        env_mode: str | None = None,
        timeout_seconds: float | None = None,
        capture_output: bool = False,
    ) -> tuple[ExecutionResult, str, str]:
        """Run a child command with the selected provider's credential injected into its environment.

        Never mutates ``os.environ`` in the parent process and never uses ``shell=True``.

        Returns:
            Tuple of (ExecutionResult, stdout_str_if_captured, stderr_str_if_captured).
        """
        if not command:
            raise InvalidArgumentError(
                "No command specified after '--'.",
                hint="Usage: keypool exec --profile <profile> --provider <provider> -- <command> [args...]",
            )

        clean_profile = validate_profile_name(profile)
        adapter = self._registry.get(provider)
        effective_mode = env_mode or self._settings.execution.env_mode

        # Check budget hard limits before proceeding
        bm = BudgetManager(self._db)
        blocked = bm.check_hard_limits(provider=adapter.name, profile=clean_profile)
        if blocked:
            b = blocked[0]
            raise ExecutionError(
                f"Execution blocked: budget hard limit exceeded for '{b.name}' (${b.current_spend:.2f} of ${b.amount_limit:.2f}).",
                hint="Increase budget limit or review spend with 'keypool budget list'.",
            )

        # 1. Validate executable exists before touching Keychain or updating usage
        _ = self.resolve_executable(command)
        exec_basename = Path(command[0]).name

        # 2. Select eligible credential strictly within the requested profile
        selected: SelectedCredential = self._selection.select_credential(
            provider=adapter.name,
            profile=clean_profile,
            record_usage=True,
            verify_keychain=True,
        )
        cred_id = selected.metadata.id
        env_var = adapter.get_environment_variable()

        # 3. Retrieve secret from macOS Keychain
        secret: str | None = self._cred_manager.retrieve_secret(cred_id)
        assert secret is not None

        # 4. Verify secret is not present in command-line arguments
        verify_command_contains_no_secret(command, secret)

        # 5. Construct isolated child environment (parent os.environ is untouched)
        child_env: dict[str, str] | None = build_child_environment(
            provider_env_var=env_var,
            secret_value=secret,
            mode=effective_mode,
        )

        # Clear local secret reference immediately after constructing child_env
        secret = None

        effective_timeout: float | None = None
        if timeout_seconds is not None and timeout_seconds > 0:
            effective_timeout = timeout_seconds
        elif self._settings.execution.default_timeout_seconds > 0:
            effective_timeout = float(self._settings.execution.default_timeout_seconds)

        start_time = time.perf_counter()
        exit_code = 1
        timed_out = False
        interrupted = False
        stdout_captured = ""
        stderr_captured = ""

        try:
            proc = subprocess.Popen(
                list(command),
                env=child_env,
                shell=False,
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
                text=bool(capture_output),
            )
            # Drop reference to child_env in parent as soon as child process is spawned
            child_env = None

            try:
                out_data, err_data = proc.communicate(timeout=effective_timeout)
                if capture_output:
                    stdout_captured = out_data or ""
                    stderr_captured = err_data or ""
                raw_rc = proc.returncode
                exit_code = 128 + abs(raw_rc) if raw_rc < 0 else int(raw_rc)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                out_data, err_data = proc.communicate()
                if capture_output:
                    stdout_captured = out_data or ""
                    stderr_captured = err_data or ""
                exit_code = 124
            except KeyboardInterrupt:
                interrupted = True
                try:
                    proc.send_signal(signal.SIGINT)
                    proc.wait(timeout=3.0)
                except Exception:
                    proc.kill()
                    proc.wait()
                exit_code = 130
        except OSError as exc:
            child_env = None
            raise ExecutionError(
                f"Failed to launch child command '{exec_basename}': {exc}"
            ) from exc
        finally:
            child_env = None
            secret = None

        duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        now = utc_now_iso()

        # Update credential execution timestamps in SQLite
        with self._db.session_scope() as session:
            rec = session.get(CredentialRecord, cred_id)
            if rec is not None:
                rec.last_used_at = now
                rec.updated_at = now
                if exit_code == 0:
                    rec.last_successful_at = now
                else:
                    rec.last_failure_at = now

        event_type = "exec_success" if exit_code == 0 else "exec_failure"
        event_status = "success" if exit_code == 0 else "failure"
        summary_msg = f"Executed '{exec_basename}' with {adapter.display_name} ({cred_id}, profile={clean_profile}, exit={exit_code})"
        if timed_out:
            summary_msg += " [TIMED OUT]"
        elif interrupted:
            summary_msg += " [INTERRUPTED]"

        self._usage.record_event(
            provider=adapter.name,
            credential_id=cred_id,
            profile_name=clean_profile,
            event_type=event_type,
            status=event_status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            command_name=exec_basename,
            summary=summary_msg,
        )

        result = ExecutionResult(
            exit_code=exit_code,
            credential_id=cred_id,
            provider=adapter.name,
            profile=clean_profile,
            env_var_injected=env_var,
            executable=exec_basename,
            duration_ms=duration_ms,
            timed_out=timed_out,
            interrupted=interrupted,
        )
        return result, stdout_captured, stderr_captured
