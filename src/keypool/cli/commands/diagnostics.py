"""System diagnostics (`keypool doctor`) implementing 17 verification checks (Section 18)."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.config.defaults import DEFAULT_PROFILES, SUPPORTED_PROVIDERS
from keypool.core.exceptions import ExitCode
from keypool.integrations.json_output import emit_json
from keypool.security.redaction import RedactingLogFilter, redact_text
from keypool.security.validation import check_secure_permissions
from keypool.storage.models import HealthState
from keypool.version import SCHEMA_VERSION, __version__


def _detect_installation_method() -> str:
    exe = Path(sys.executable).resolve()
    exe_str = str(exe)
    if "pipx" in exe_str:
        return f"pipx isolated environment ({exe})"
    if ".venv" in exe_str or "venv" in exe_str:
        return f"virtual environment ({exe})"
    return f"Python runtime ({exe})"


def run_doctor_checks(ctx: RuntimeContext) -> dict[str, Any]:
    """Execute all 17 diagnostic checks without exposing or modifying user credentials."""
    checks: list[dict[str, str]] = []

    def add_check(name: str, status: str, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": status,  # "pass", "warn", "fail"
                "detail": redact_text(detail),
            }
        )

    # 1. Python compatibility
    py_ver = sys.version_info
    py_ok = (py_ver.major, py_ver.minor) >= (3, 11)
    add_check(
        "Python environment",
        "pass" if py_ok else "fail",
        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro} ({platform.machine()})",
    )

    # 2. macOS platform compatibility
    is_macos = sys.platform == "darwin"
    mac_ver = platform.mac_ver()[0] if is_macos else platform.platform()
    add_check(
        "macOS compatibility",
        "pass" if is_macos else "fail",
        f"macOS {mac_ver} ({platform.machine()})" if is_macos else f"Non-macOS ({sys.platform})",
    )

    # 3. Application version
    add_check(
        "Application version",
        "pass",
        f"KEYPOOL v{__version__}",
    )

    # 4. Configuration directory
    app_dir_ok = ctx.paths.app_dir.is_dir() and os.access(ctx.paths.app_dir, os.R_OK | os.W_OK)
    add_check(
        "Configuration directory",
        "pass" if app_dir_ok else "fail",
        str(ctx.paths.app_dir),
    )

    # 5. Database availability
    db_exists = ctx.paths.db_path.is_file() and os.access(ctx.paths.db_path, os.R_OK | os.W_OK)
    add_check(
        "Database accessible",
        "pass" if db_exists else "fail",
        str(ctx.paths.db_path),
    )

    # 6. Database integrity
    integrity_ok, integrity_msg = ctx.db.check_integrity()
    add_check(
        "Database integrity",
        "pass" if integrity_ok else "fail",
        integrity_msg,
    )

    # 7. Schema version
    try:
        db_schema_ver = ctx.db.get_schema_version()
        schema_ok = db_schema_ver == SCHEMA_VERSION
        add_check(
            "Schema version",
            "pass" if schema_ok else "fail",
            f"v{db_schema_ver} (expected v{SCHEMA_VERSION})",
        )
    except Exception as exc:
        add_check("Schema version", "fail", str(exc))

    # 8. Keychain backend availability
    kc_avail_ok, kc_avail_msg = ctx.secret_store.check_backend(probe_write=False)
    add_check(
        "Keychain backend available",
        "pass" if kc_avail_ok else "fail",
        kc_avail_msg,
    )

    # 9. Keychain read/write capability
    kc_rw_ok, kc_rw_msg = ctx.secret_store.check_backend(probe_write=True)
    add_check(
        "Keychain read/write capability",
        "pass" if kc_rw_ok else "fail",
        kc_rw_msg,
    )

    # 10. Provider adapter registration
    reg_names = ctx.registry.names()
    missing_provs = [p for p in SUPPORTED_PROVIDERS if p not in reg_names]
    add_check(
        "Provider adapters registered",
        "pass" if not missing_provs else "fail",
        f"Registered: {', '.join(reg_names)}"
        if not missing_provs
        else f"Missing adapters: {', '.join(missing_provs)}",
    )

    # 11. Profile consistency
    profiles = ctx.profile_manager.list_profiles()
    prof_names = {p.name for p in profiles}
    missing_profiles = [p for p in DEFAULT_PROFILES if p not in prof_names]
    default_prof_valid = ctx.settings.default_profile in prof_names
    prof_ok = (not missing_profiles) and default_prof_valid
    add_check(
        "Profile configuration valid",
        "pass" if prof_ok else "fail",
        f"{len(profiles)} profiles active (default='{ctx.settings.default_profile}')",
    )

    # 12. Credential metadata consistency
    creds = ctx.credential_manager.list_credentials()
    untested_count = sum(1 for c in creds if c.enabled and c.status == HealthState.UNTESTED.value)
    if untested_count > 0:
        unit_word = "credential has" if untested_count == 1 else "credentials have"
        add_check(
            "Credential metadata consistency",
            "warn",
            f"{untested_count} {unit_word} not been tested yet (run 'keypool test')",
        )
    else:
        add_check(
            "Credential metadata consistency",
            "pass",
            f"{len(creds)} credential(s) registered",
        )

    # 13. Missing Keychain references
    reconcile = ctx.credential_manager.reconcile_storage()
    if reconcile.missing_in_keychain or reconcile.keychain_errors:
        issues = reconcile.missing_in_keychain + reconcile.keychain_errors
        add_check(
            "Keychain reference integrity",
            "fail",
            f"Missing or inaccessible Keychain secrets for: {', '.join(issues)}",
        )
    else:
        add_check(
            "Keychain reference integrity",
            "pass",
            f"{reconcile.healthy_references}/{reconcile.total_checked} Keychain references verified",
        )

    # 14. Logging configuration
    has_redacting_filter = any(
        any(isinstance(f, RedactingLogFilter) for f in h.filters) for h in ctx.logger.handlers
    )
    log_ok = has_redacting_filter or len(ctx.logger.handlers) > 0
    add_check(
        "Logging & redaction filter",
        "pass" if log_ok else "warn",
        f"Log file: {ctx.paths.log_file} (redaction filter active={has_redacting_filter})",
    )

    # 15. Permission problems
    perm_issues: list[str] = []
    for target_path in (ctx.paths.app_dir, ctx.paths.db_path, ctx.paths.config_path):
        ok_perm, msg_perm = check_secure_permissions(target_path)
        if not ok_perm:
            perm_issues.append(msg_perm)
    add_check(
        "Filesystem permissions",
        "pass" if not perm_issues else "warn",
        "Owner-only permissions (0700/0600) verified"
        if not perm_issues
        else "; ".join(perm_issues),
    )

    # 16. Installation method
    install_desc = _detect_installation_method()
    add_check("Installation method", "pass", install_desc)

    # 17. PATH availability
    which_kp = shutil.which("keypool")
    local_bin_kp = Path.home() / ".local" / "bin" / "keypool"
    if which_kp:
        add_check("PATH availability", "pass", f"Resolved at {which_kp}")
    elif local_bin_kp.exists():
        add_check(
            "PATH availability",
            "warn",
            f"Installed at {local_bin_kp} (ensure ~/.local/bin is in your shell PATH)",
        )
    else:
        add_check(
            "PATH availability",
            "warn",
            "Global 'keypool' binary not currently on PATH (install with 'pipx install .')",
        )

    passed = sum(1 for c in checks if c["status"] == "pass")
    warnings = sum(1 for c in checks if c["status"] == "warn")
    failures = sum(1 for c in checks if c["status"] == "fail")

    return {
        "healthy": failures == 0,
        "summary": {
            "total": len(checks),
            "passed": passed,
            "warnings": warnings,
            "failures": failures,
        },
        "checks": checks,
    }


def cmd_doctor(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON diagnostic report.",
    ),
) -> None:
    """Run 17 system, database, Keychain, permission, and profile diagnostic checks."""
    ctx = RuntimeContext()
    report = run_doctor_checks(ctx)

    if json_output:
        emit_json("doctor", report, ok=bool(report["healthy"]))
        if not report["healthy"]:
            raise typer.Exit(int(ExitCode.STORAGE_ERROR))
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Icon", width=3)
    table.add_column("Check", style="bold white", width=32)
    table.add_column("Details", style="dim")

    for chk in report["checks"]:
        st = chk["status"]
        if st == "pass":
            icon = "[bold green]✓[/bold green]"
            detail_style = "white"
        elif st == "warn":
            icon = "[bold yellow]⚠[/bold yellow]"
            detail_style = "yellow"
        else:
            icon = "[bold red]✖[/bold red]"
            detail_style = "bold red"
        table.add_row(icon, chk["name"], f"[{detail_style}]{chk['detail']}[/{detail_style}]")

    counts = report["summary"]
    warns = counts["warnings"]
    fails = counts["failures"]

    if fails > 0:
        footer = f"[bold red]RESULT: {fails} FAILURE(S), {warns} WARNING(S)[/bold red]"
        border = "red"
    elif warns > 0:
        w_label = "WARNING" if warns == 1 else "WARNINGS"
        footer = f"[bold yellow]RESULT: {warns} {w_label}[/bold yellow]"
        border = "yellow"
    else:
        footer = "[bold green]RESULT: ALL CHECKS PASSED[/bold green]"
        border = "green"

    console.print()
    console.print(
        Panel(
            table,
            title="[bold cyan]KEYPOOL DOCTOR[/bold cyan]",
            subtitle=footer,
            border_style=border,
            box=box.ROUNDED,
            expand=False,
        )
    )
    console.print()

    if fails > 0:
        raise typer.Exit(int(ExitCode.STORAGE_ERROR))
