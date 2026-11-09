"""CLI command for local usage tracking and activity reporting (Section 14)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import (
    RuntimeContext,
    format_timestamp,
    get_console,
)
from keypool.integrations.json_output import emit_json


def cmd_usage(
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter usage report by provider ('groq' or 'gemini').",
    ),
    credential: str | None = typer.Option(
        None,
        "--credential",
        "-c",
        help="Filter usage report by credential ID (e.g. cred_a83f21).",
    ),
    period: str = typer.Option(
        "all",
        "--period",
        help="Time window to inspect (e.g. '24h', '7d', '30d', 'all').",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON usage report.",
    ),
    csv_output: bool = typer.Option(
        False,
        "--csv",
        help="Output comma-separated CSV usage events.",
    ),
) -> None:
    """Display locally observed credential selections, executions, 429 events, and token metrics."""
    ctx = RuntimeContext()
    if provider:
        _ = ctx.registry.get(provider)
    if credential:
        _ = ctx.credential_manager.get_credential(credential)

    if csv_output:
        import csv
        import io
        import sys
        events = ctx.usage_tracker.list_events(provider=provider, credential_id=credential, limit=500)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "occurred_at", "provider", "credential_id", "model", "event_type", "status", "http_status", "duration_ms", "cost_usd"])
        for ev in events:
            writer.writerow([
                ev.id,
                ev.occurred_at,
                ev.provider,
                ev.credential_id or "",
                ev.model or "",
                ev.event_type,
                ev.status,
                ev.http_status or "",
                f"{ev.duration_ms:.2f}" if ev.duration_ms is not None else "",
                f"{ev.cost_usd:.6f}" if ev.cost_usd is not None else "",
            ])
        sys.stdout.write(output.getvalue())
        return

    report = ctx.usage_tracker.get_usage_report(
        provider=provider,
        credential_id=credential,
        period=period,
    )

    if json_output:
        emit_json("usage", report.model_dump(mode="json"))
        return

    console = get_console(color_mode=ctx.settings.color)
    summary_grid = Table(show_header=False, box=None, padding=(0, 3))
    summary_grid.add_column("Metric", style="bold cyan", width=24)
    summary_grid.add_column("Value")

    summary_grid.add_row("Reporting Period", f"[bold white]{report.period}[/bold white]")
    if report.provider_filter:
        summary_grid.add_row("Provider Filter", report.provider_filter.upper())
    if report.credential_filter:
        summary_grid.add_row("Credential Filter", report.credential_filter)
    summary_grid.add_row("Credential Selections", str(report.total_selections))
    summary_grid.add_row("Child Executions", str(report.total_executions))
    summary_grid.add_row("Successful Executions", f"[green]{report.successful_executions}[/green]")
    summary_grid.add_row("Failed Executions", f"[red]{report.failed_executions}[/red]")
    summary_grid.add_row(
        "Observed HTTP 429s",
        f"[yellow]{report.observed_429_events}[/yellow]",
    )
    tokens_display = (
        f"{report.observed_tokens} ({report.token_source})"
        if report.observed_tokens > 0
        else f"0 ({report.token_source})"
    )
    summary_grid.add_row("Observed Probe Tokens", tokens_display)

    console.print(
        Panel(
            summary_grid,
            title="[bold cyan]KEYPOOL Local Usage Summary[/bold cyan]",
            subtitle=f"[dim]{report.billing_disclaimer}[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )

    if report.by_credential:
        console.print()
        cred_table = Table(
            title="[bold cyan]Usage by Credential[/bold cyan]",
            box=box.ROUNDED,
            header_style="bold cyan",
            border_style="cyan",
        )
        cred_table.add_column("ID", style="bold white", no_wrap=True)
        cred_table.add_column("Name", style="white")
        cred_table.add_column("Provider", style="cyan")
        cred_table.add_column("Selections", justify="right")
        cred_table.add_column("Execs", justify="right")
        cred_table.add_column("Success", justify="right", style="green")
        cred_table.add_column("Failed", justify="right", style="red")
        cred_table.add_column("429s", justify="right", style="yellow")
        cred_table.add_column("Last Used", style="dim", no_wrap=True)

        for item in report.by_credential:
            cred_table.add_row(
                item.credential_id,
                item.credential_name,
                item.provider.upper(),
                str(item.selections),
                str(item.executions),
                str(item.successful_executions),
                str(item.failed_executions),
                str(item.observed_429_events),
                format_timestamp(item.last_used_at),
            )
        console.print(cred_table)
