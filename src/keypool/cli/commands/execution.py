"""CLI commands for child process execution (`exec`) and controlled retrieval (`get`) (Sections 12, 13)."""

from __future__ import annotations

import sys

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import (
    RuntimeContext,
    format_enabled_badge,
    format_health_badge,
    get_console,
)
from keypool.core.exceptions import InvalidArgumentError, SecurityError
from keypool.integrations.json_output import emit_json


def cmd_exec(
    command: list[str] | None = typer.Argument(
        None,
        help="Child command and arguments after '--' (e.g. -- python app.py).",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-f",
        help="Execution profile to select credentials from (defaults to configured default_profile).",
    ),
    provider: str = typer.Option(
        ...,
        "--provider",
        "-p",
        help="AI provider whose credential will be injected ('groq' or 'gemini').",
    ),
    env_mode: str | None = typer.Option(
        None,
        "--env-mode",
        help="Child environment inheritance mode ('inherit' or 'controlled').",
    ),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        "-t",
        help="Optional timeout in seconds for the child process.",
    ),
) -> None:
    """Execute a child command with the selected provider credential injected into its environment."""
    ctx = RuntimeContext()
    effective_profile = profile.strip().lower() if profile else ctx.settings.default_profile

    cleaned_cmd = list(command or [])
    if cleaned_cmd and cleaned_cmd[0] == "--":
        cleaned_cmd = cleaned_cmd[1:]

    if not cleaned_cmd:
        raise InvalidArgumentError(
            "No child command specified for 'keypool exec'.",
            hint="Example: keypool exec --profile testing --provider groq -- python app.py",
        )

    result, _out, _err = ctx.execution_manager.execute(
        provider=provider,
        profile=effective_profile,
        command=cleaned_cmd,
        env_mode=env_mode,
        timeout_seconds=timeout,
        capture_output=False,
    )
    raise typer.Exit(result.exit_code)


def cmd_get(
    provider: str = typer.Option(
        ...,
        "--provider",
        "-p",
        help="Target AI provider ('groq' or 'gemini').",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-f",
        help="Optional profile filter for credential selection.",
    ),
    show: bool = typer.Option(
        False,
        "--show",
        help="Explicitly output the raw secret to stdout (writes a security warning to stderr).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output non-secret selection metadata as JSON (cannot be combined with --show).",
    ),
) -> None:
    """Select an eligible credential for a provider (outputs metadata unless --show is passed)."""
    if show and json_output:
        raise SecurityError(
            "Cannot combine '--show' and '--json'. Raw secrets are never included in JSON responses.",
            hint="Use 'keypool get --provider <p> --json' for metadata or 'keypool exec' to inject secrets.",
        )

    ctx = RuntimeContext()
    selected = ctx.selection_engine.select_credential(
        provider=provider,
        profile=profile,
        record_usage=True,
        verify_keychain=True,
    )
    meta = selected.metadata

    if show:
        secret = ctx.credential_manager.retrieve_secret(meta.id)
        # Emit explicit security warning to stderr only, never touching stdout with formatting
        sys.stderr.write(
            "WARNING: Writing raw API credential to stdout (--show). "
            "Ensure stdout is not logged by shells, terminal recordings, or CI pipelines. "
            "Prefer 'keypool exec' whenever possible.\n"
        )
        sys.stderr.flush()
        # Write ONLY the raw credential to stdout without Rich formatting
        sys.stdout.write(secret)
        if sys.stdout.isatty():
            sys.stdout.write("\n")
        sys.stdout.flush()
        return

    if json_output:
        emit_json(
            "get",
            {
                "selected_credential": meta.to_public_dict(),
                "env_var_name": selected.env_var_name,
                "profile_used": selected.profile_used,
                "selection_reason": selected.selection_reason,
            },
        )
        return

    console = get_console(color_mode=ctx.settings.color)
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="bold cyan", width=20)
    grid.add_column("Value")

    grid.add_row("Selected ID", f"[bold white]{meta.id}[/bold white]")
    grid.add_row("Name", meta.name)
    grid.add_row("Provider", meta.provider.upper())
    grid.add_row("Environment Variable", f"[bold green]{selected.env_var_name}[/bold green]")
    grid.add_row("Profile Filter", selected.profile_used or "All enabled")
    grid.add_row("Selection Policy", selected.selection_reason)
    grid.add_row("State", format_enabled_badge(meta.enabled))
    grid.add_row("Health", format_health_badge(meta.status, enabled=meta.enabled))
    grid.add_row("Secret Value", "[dim][Hidden — use 'keypool exec' or pass --show][/dim]")

    console.print(
        Panel(
            grid,
            title="[bold cyan]Selected Credential (Metadata Only)[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )

