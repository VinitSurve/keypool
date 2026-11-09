"""CLI commands for usage analytics and cost reporting (Section 12)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import typer
from rich import box
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.core.usage_tracker import UsageTracker
from keypool.integrations.json_output import emit_json, format_json_envelope

analytics_app = typer.Typer(
    name="analytics",
    help="Usage analytics, historical summaries, and CSV/JSON reporting (Section 12).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@analytics_app.callback()
def analytics_index(
    ctx_typer: typer.Context,
    period: str = typer.Option(
        "30d", "--period", "-p", help="Reporting period ('today', '7d', '30d', 'all')."
    ),
    provider: str | None = typer.Option(None, "--provider", help="Filter by provider."),
    export_format: str | None = typer.Option(None, "--export", help="Export to 'json' or 'csv'."),
    output_file: str | None = typer.Option(None, "--output", "-o", help="File to write export to."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Display analytics breakdown across providers, models, and credentials."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    ut = UsageTracker(ctx.db)
    report = ut.get_usage_report(provider=provider, period=period)

    data = {
        "period": period,
        "provider": provider,
        "total_selections": report.total_selections,
        "total_executions": report.total_executions,
        "successful_executions": report.successful_executions,
        "failed_executions": report.failed_executions,
        "observed_429_events": report.observed_429_events,
        "observed_tokens": report.observed_tokens,
        "token_source": report.token_source,
    }

    if json_output or export_format == "json":
        if output_file:
            Path(output_file).write_text(
                format_json_envelope("analytics", data) + "\n", encoding="utf-8"
            )
        else:
            emit_json("analytics", data)
        return

    if export_format == "csv":
        if output_file:
            with Path(output_file).open("w", newline="", encoding="utf-8") as out:
                writer = csv.writer(out)
                writer.writerow(["metric", "value"])
                for k, v in data.items():
                    writer.writerow([k, v])
        else:
            writer = csv.writer(sys.stdout)
            writer.writerow(["metric", "value"])
            for k, v in data.items():
                writer.writerow([k, v])
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title=f"[bold cyan]Usage Analytics ({period})[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Metric", style="bold white", width=24)
    table.add_column("Count / Value", style="white")

    table.add_row("Total Selections", str(report.total_selections))
    table.add_row("Total Executions", str(report.total_executions))
    table.add_row("Successful Executions", f"[green]{report.successful_executions}[/green]")
    table.add_row("Failed Executions", f"[red]{report.failed_executions}[/red]")
    table.add_row("Observed Rate Limits (429)", f"[yellow]{report.observed_429_events}[/yellow]")
    table.add_row("Observed Tokens", f"{report.observed_tokens:,}")
    table.add_row("Token Source", report.token_source)

    console.print(table)
