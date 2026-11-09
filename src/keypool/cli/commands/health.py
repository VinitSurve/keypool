"""CLI commands for credential testing, health monitoring, and system status (Section 9, 17, 22)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import (
    RuntimeContext,
    build_status_summary,
    format_health_badge,
    format_timestamp,
    get_console,
    render_dashboard,
)
from keypool.core.exceptions import ExitCode, InvalidArgumentError
from keypool.core.health_manager import CredentialHealthSnapshot
from keypool.integrations.json_output import emit_json
from keypool.security.redaction import redact_text
from keypool.storage.models import HealthState


def _render_health_snapshots(
    snapshots: list[CredentialHealthSnapshot],
    console: typer.Context | object,
    *,
    title: str = "Credential Health Report",
) -> None:
    from rich.console import Console

    assert isinstance(console, Console)
    if not snapshots:
        console.print(
            Panel(
                "[yellow]LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED[/yellow]\n"
                "Add credentials with [bold cyan]keypool add groq[/bold cyan] or [bold cyan]keypool add gemini[/bold cyan].",
                title=title,
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        return

    table = Table(
        title=f"[bold cyan]{title}[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("ID", style="bold white", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Provider", style="cyan")
    table.add_column("Health State", no_wrap=True)
    table.add_column("Keychain", justify="center")
    table.add_column("Auth", justify="center")
    table.add_column("Models", justify="right")
    table.add_column("Last Tested", style="dim", no_wrap=True)
    table.add_column("Observation / Details")

    for snap in snapshots:
        kc_mark = "[green]✓[/green]" if snap.keychain_retrievable else "[red]✖[/red]"
        auth_mark = (
            "[green]✓[/green]"
            if snap.authentication_succeeded is True
            else "[red]✖[/red]"
            if snap.authentication_succeeded is False
            else "[dim]—[/dim]"
        )
        models_str = str(snap.models_discovered) if snap.models_discovered > 0 else "—"
        msg = redact_text(snap.sanitized_message) if snap.sanitized_message else "—"
        if len(msg) > 68:
            msg = msg[:65] + "..."

        table.add_row(
            snap.credential_id,
            snap.name,
            snap.provider.upper(),
            format_health_badge(snap.state, enabled=snap.enabled),
            kc_mark,
            auth_mark,
            models_str,
            format_timestamp(snap.last_tested_at),
            msg,
        )

    console.print(table)


def cmd_test(
    credential_id: str | None = typer.Argument(
        None,
        help="Optional specific credential ID to test (e.g. cred_a83f21).",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Test all configured credentials for a provider ('groq' or 'gemini').",
    ),
    functional: bool = typer.Option(
        True,
        "--functional/--no-functional",
        help="Perform a minimal generation request in addition to model discovery.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON test report.",
    ),
) -> None:
    """Test live connectivity, authentication, model discovery, and generation for credentials."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)

    if credential_id and provider:
        raise InvalidArgumentError("Specify either a credential ID or --provider, not both.")

    snapshots: list[CredentialHealthSnapshot] = []

    if credential_id:
        snap = ctx.health_manager.test_credential(
            credential_id,
            functional=functional,
            force=True,
        )
        snapshots.append(snap)
    else:
        creds = ctx.credential_manager.list_credentials(provider=provider)
        if not creds:
            if json_output:
                emit_json(
                    "test",
                    {
                        "status": "not_run",
                        "message": "LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED",
                        "results": [],
                    },
                )
                return
            console.print(
                "[yellow]LIVE PROVIDER TESTS NOT RUN — NO CREDENTIALS CONFIGURED[/yellow]"
            )
            return

        for cred in creds:
            snap = ctx.health_manager.test_credential(
                cred.id,
                functional=functional,
                force=True,
            )
            snapshots.append(snap)

    any_failed = any(s.state != HealthState.HEALTHY.value and s.enabled for s in snapshots)

    if json_output:
        emit_json(
            "test",
            {
                "tested_count": len(snapshots),
                "all_healthy": not any_failed,
                "results": [s.to_public_dict() for s in snapshots],
            },
            ok=not any_failed,
        )
        if any_failed:
            raise typer.Exit(int(ExitCode.PROVIDER_ERROR))
        return

    _render_health_snapshots(
        snapshots,
        console,
        title="Live Credential Test Results",
    )
    if any_failed:
        raise typer.Exit(int(ExitCode.PROVIDER_ERROR))


def cmd_health(
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter health report by provider ('groq' or 'gemini').",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        "-r",
        help="Force live network health checks instead of using cached observations.",
    ),
    functional: bool = typer.Option(
        False,
        "--functional",
        help="Include minimal generation test when --refresh is enabled.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON health report.",
    ),
) -> None:
    """Inspect credential health states (cached by default; use --refresh for live checks)."""
    ctx = RuntimeContext()
    snapshots = ctx.health_manager.get_health_report(
        provider=provider,
        refresh=refresh,
        functional=functional,
    )

    if json_output:
        emit_json(
            "health",
            {
                "refreshed": refresh,
                "provider_filter": provider.strip().lower() if provider else None,
                "count": len(snapshots),
                "credentials": [s.to_public_dict() for s in snapshots],
            },
        )
        return

    console = get_console(color_mode=ctx.settings.color)
    title = (
        "Credential Health (Live Refreshed)"
        if refresh
        else "Credential Health (Cached Observations)"
    )
    if not snapshots:
        console.print(
            Panel(
                "[yellow]No credentials configured.[/yellow]\n"
                "Run [bold cyan]keypool add groq[/bold cyan] or [bold cyan]keypool add gemini[/bold cyan] to add credentials.",
                title=title,
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        return

    _render_health_snapshots(snapshots, console, title=title)


def cmd_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON system status.",
    ),
) -> None:
    """Display KEYPOOL system overview, provider counts, active profile, and recent activity."""
    ctx = RuntimeContext()
    if json_output:
        summary = build_status_summary(ctx)
        emit_json("status", summary)
        return

    console = get_console(color_mode=ctx.settings.color)
    render_dashboard(ctx, console)
