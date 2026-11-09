"""Main Typer application assembly and exception boundary for KEYPOOL."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from typing import Any, TypeVar

import typer

from keypool.cli.commands.analytics import analytics_app
from keypool.cli.commands.backup import backup_app, cmd_restore
from keypool.cli.commands.budgets import budgets_app
from keypool.cli.commands.credentials import (
    cmd_add,
    cmd_disable,
    cmd_enable,
    cmd_list,
    cmd_remove,
    cmd_rename,
    cmd_show,
    cmd_update,
)
from keypool.cli.commands.diagnostics import cmd_doctor
from keypool.cli.commands.env_cmd import cmd_completion, env_app
from keypool.cli.commands.execution import cmd_exec, cmd_get
from keypool.cli.commands.gateway import gateway_app
from keypool.cli.commands.health import cmd_health, cmd_status, cmd_test
from keypool.cli.commands.mcp import cmd_mcp
from keypool.cli.commands.models import models_app
from keypool.cli.commands.plugins import plugins_app
from keypool.cli.commands.profiles import (
    profile_assign,
    profile_create,
    profile_delete,
    profile_list,
    profile_show,
    profile_unassign,
)
from keypool.cli.commands.projects import projects_app
from keypool.cli.commands.providers import providers_index, providers_inspect, providers_show
from keypool.cli.commands.requests import requests_app
from keypool.cli.commands.usage import cmd_usage
from keypool.cli.formatting import (
    RuntimeContext,
    build_status_summary,
    get_console,
    render_dashboard,
    render_error_panel,
)
from keypool.core.exceptions import ExitCode, KeypoolError
from keypool.integrations.json_output import emit_json, emit_json_error
from keypool.security.redaction import redact_text
from keypool.version import APP_DISPLAY_NAME, __version__

F = TypeVar("F", bound=Callable[..., Any])


def handle_cli_errors(func: F) -> F:
    """Wrap a CLI command handler so KeypoolError maps to its standardized exit code."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        json_requested = bool(kwargs.get("json_output", False)) or ("--json" in sys.argv)
        try:
            return func(*args, **kwargs)
        except (typer.Exit, SystemExit, KeyboardInterrupt):
            raise
        except KeypoolError as exc:
            if json_requested:
                emit_json_error(
                    func.__name__.replace("cmd_", ""),
                    error_code=exc.error_code,
                    message=exc.message,
                    exit_code=exc.exit_code,
                    hint=exc.hint,
                )
            else:
                render_error_panel(exc.message, hint=exc.hint)
            raise typer.Exit(code=exc.exit_code) from exc
        except Exception as exc:
            safe_msg = redact_text(str(exc))
            if json_requested:
                emit_json_error(
                    func.__name__.replace("cmd_", ""),
                    error_code="unexpected_error",
                    message=safe_msg,
                    exit_code=int(ExitCode.GENERAL_ERROR),
                )
            else:
                render_error_panel(
                    f"Unexpected application error: {safe_msg}",
                    title="KEYPOOL Internal Error",
                )
            raise typer.Exit(code=int(ExitCode.GENERAL_ERROR)) from exc

    return wrapper  # type: ignore[return-value]


app = typer.Typer(
    name="keypool",
    help=f"{APP_DISPLAY_NAME}: Production-ready local AI API credential manager for macOS.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)

providers_subapp = typer.Typer(
    name="providers",
    help="Inspect registered AI provider adapters (Groq, Google Gemini).",
    invoke_without_command=True,
    no_args_is_help=False,
)
providers_subapp.callback()(handle_cli_errors(providers_index))
providers_subapp.command("show")(handle_cli_errors(providers_show))
providers_subapp.command("inspect")(handle_cli_errors(providers_inspect))

profile_subapp = typer.Typer(
    name="profile",
    help="Manage isolated execution profiles (testing, development, production, and custom).",
    no_args_is_help=True,
)
profile_subapp.command("list")(handle_cli_errors(profile_list))
profile_subapp.command("show")(handle_cli_errors(profile_show))
profile_subapp.command("create")(handle_cli_errors(profile_create))
profile_subapp.command("assign")(handle_cli_errors(profile_assign))
profile_subapp.command("unassign")(handle_cli_errors(profile_unassign))
profile_subapp.command("delete")(handle_cli_errors(profile_delete))


def _version_callback(value: bool) -> None:
    if value:
        sys.stdout.write(f"keypool {__version__}\n")
        sys.stdout.flush()
        raise typer.Exit(int(ExitCode.SUCCESS))


@app.callback()
@handle_cli_errors
def main_callback(
    ctx_typer: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show KEYPOOL version and exit.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output system overview in machine-readable JSON when no subcommand is given.",
    ),
) -> None:
    """KEYPOOL: Local-first macOS API credential manager for Groq and Google Gemini."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    if json_output:
        emit_json("overview", build_status_summary(ctx))
        return

    console = get_console(color_mode=ctx.settings.color)
    render_dashboard(ctx, console)


# Register top-level commands
app.command("add")(handle_cli_errors(cmd_add))
app.command("list")(handle_cli_errors(cmd_list))
app.command("show")(handle_cli_errors(cmd_show))
app.command("update")(handle_cli_errors(cmd_update))
app.command("rename")(handle_cli_errors(cmd_rename))
app.command("enable")(handle_cli_errors(cmd_enable))
app.command("disable")(handle_cli_errors(cmd_disable))
app.command("remove")(handle_cli_errors(cmd_remove))

app.command("test")(handle_cli_errors(cmd_test))
app.command("health")(handle_cli_errors(cmd_health))
app.command("status")(handle_cli_errors(cmd_status))
app.command("doctor")(handle_cli_errors(cmd_doctor))

app.command(
    "exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)(handle_cli_errors(cmd_exec))
app.command("get")(handle_cli_errors(cmd_get))
app.command("usage")(handle_cli_errors(cmd_usage))

app.add_typer(providers_subapp, name="providers")
app.add_typer(providers_subapp, name="provider")
app.add_typer(profile_subapp, name="profile")
app.add_typer(projects_app, name="project")
app.add_typer(projects_app, name="projects")
app.add_typer(models_app, name="models")
app.add_typer(models_app, name="model")
app.add_typer(budgets_app, name="budget")
app.add_typer(budgets_app, name="budgets")
app.add_typer(backup_app, name="backup")
app.add_typer(analytics_app, name="analytics")
app.add_typer(plugins_app, name="plugin")
app.add_typer(plugins_app, name="plugins")
app.add_typer(env_app, name="env")
app.add_typer(requests_app, name="requests")
app.add_typer(gateway_app, name="gateway")

app.command("restore")(handle_cli_errors(cmd_restore))
app.command("mcp")(handle_cli_errors(cmd_mcp))
app.command("completion")(handle_cli_errors(cmd_completion))


def cmd_tui() -> None:
    """Launch the interactive terminal interface."""
    from keypool.tui.app import run_tui

    run_tui()


app.command("tui")(handle_cli_errors(cmd_tui))
