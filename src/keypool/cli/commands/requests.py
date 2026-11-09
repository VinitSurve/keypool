"""CLI commands for sanitized request inspection and debugging (Section 23)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.integrations.json_output import emit_json
from keypool.storage.models import UsageEventRecord

requests_app = typer.Typer(
    name="requests",
    help="Sanitized request inspection and debugging history (Section 23).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@requests_app.callback()
def requests_index(
    ctx_typer: typer.Context,
    limit: int = typer.Option(15, "--limit", "-l", help="Number of recent requests to display."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List recent sanitized provider requests."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    with ctx.db.session_scope() as session:
        records = (
            session.execute(
                select(UsageEventRecord).order_by(UsageEventRecord.occurred_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )

        items = [
            {
                "id": str(r.id),
                "timestamp": r.occurred_at,
                "provider": r.provider,
                "model": r.model or "-",
                "status": r.status,
                "http_status": r.http_status,
                "duration_ms": r.duration_ms,
                "tokens": r.observed_tokens,
                "cost_usd": r.cost_usd,
                "summary": r.summary,
            }
            for r in records
        ]

    if json_output:
        emit_json("requests", {"requests": items})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]Recent Sanitized Requests & Activity[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Req ID", style="bold white", width=8)
    table.add_column("Timestamp", style="dim", width=22)
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Model", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost", justify="right")

    for item in items:
        stat_color = "green" if item["status"] == "success" else "red"
        lat_str = f"{item['duration_ms']:.1f}ms" if item["duration_ms"] else "-"
        tok_str = f"{item['tokens']:,}" if item["tokens"] else "-"
        cost_str = f"${item['cost_usd']:.4f}" if item["cost_usd"] else "-"
        table.add_row(
            str(item["id"]),
            str(item["timestamp"]),
            str(item["provider"]),
            str(item["model"]),
            f"[{stat_color}]{item['status']}[/{stat_color}]",
            lat_str,
            tok_str,
            cost_str,
        )

    console.print(table)


@requests_app.command("inspect")
def cmd_requests_inspect(
    request_id: int = typer.Argument(..., help="Request event ID to inspect."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Inspect sanitized event parameters for a specific request."""
    ctx = RuntimeContext()
    with ctx.db.session_scope() as session:
        rec = session.get(UsageEventRecord, request_id)
        if rec is None:
            console = get_console(color_mode=ctx.settings.color)
            console.print(f"[red]Request event #{request_id} not found.[/red]")
            raise typer.Exit(1)

        data = {
            "id": rec.id,
            "occurred_at": rec.occurred_at,
            "provider": rec.provider,
            "credential_id": rec.credential_id,
            "profile_name": rec.profile_name,
            "event_type": rec.event_type,
            "status": rec.status,
            "http_status": rec.http_status,
            "duration_ms": rec.duration_ms,
            "model": rec.model,
            "observed_tokens": rec.observed_tokens,
            "cost_usd": rec.cost_usd,
            "summary": rec.summary,
        }

    if json_output:
        emit_json("requests.inspect", data)
        return

    console = get_console(color_mode=ctx.settings.color)
    grid = Table(show_header=False, box=None)
    grid.add_column("Field", style="bold cyan", width=20)
    grid.add_column("Value")

    for k, v in data.items():
        grid.add_row(k.replace("_", " ").capitalize(), str(v) if v is not None else "-")

    console.print(
        Panel(
            grid,
            title=f"[bold cyan]Sanitized Request #{rec.id}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
