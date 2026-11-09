"""CLI commands for desktop alerts and system monitoring."""

from __future__ import annotations

import typer
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.integrations.json_output import emit_json
from keypool.notifications.alerts import AlertManager

alerts_app = typer.Typer(
    name="alerts",
    help="Manage alerts, notifications, and proactive monitoring rules.",
    no_args_is_help=True,
)


@alerts_app.command("check")
def alerts_check(
    notify: bool = typer.Option(
        True, "--notify/--no-notify", help="Deliver macOS desktop notification."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """Evaluate all health, budget, and expiration alert rules."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = AlertManager(ctx)
    triggered = mgr.evaluate_alerts(notify=notify)

    if json_output:
        emit_json("alerts_check", {"triggered_count": len(triggered), "alerts": triggered})
        return

    if not triggered:
        console.print(
            "[bold green]All systems healthy. No alert thresholds triggered.[/bold green]"
        )
        return

    console.print(f"[bold yellow]Triggered {len(triggered)} alert(s):[/bold yellow]")
    table = Table(box=None)
    table.add_column("Severity", style="bold")
    table.add_column("Title", style="bold cyan")
    table.add_column("Message", style="white")

    for a in triggered:
        sev_style = {
            "critical": "[bold red]CRITICAL[/bold red]",
            "error": "[red]ERROR[/red]",
            "warning": "[yellow]WARNING[/yellow]",
            "info": "[cyan]INFO[/cyan]",
        }.get(a["severity"], a["severity"])
        table.add_row(sev_style, a["title"], a["message"])

    console.print(table)


@alerts_app.command("list")
def alerts_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum alert events to return."),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
) -> None:
    """List historical alert notification events."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = AlertManager(ctx)
    history = mgr.list_alerts(limit=limit)

    if json_output:
        emit_json("alerts_list", {"alerts": history})
        return

    if not history:
        console.print("[dim]No historical alerts found.[/dim]")
        return

    table = Table(title="Recent System Alerts", box=None)
    table.add_column("Time", style="dim")
    table.add_column("Severity", style="bold")
    table.add_column("Title", style="bold cyan")
    table.add_column("Message", style="white")

    for h in history:
        sev_style = {
            "critical": "[bold red]CRITICAL[/bold red]",
            "error": "[red]ERROR[/red]",
            "warning": "[yellow]WARNING[/yellow]",
            "info": "[cyan]INFO[/cyan]",
        }.get(h["severity"], h["severity"])
        table.add_row(h["occurred_at"][:19], sev_style, h["title"], h["message"])

    console.print(table)


@alerts_app.command("rules")
def alerts_rules(
    json_output: bool = typer.Option(False, "--json", help="Output rules in JSON format."),
) -> None:
    """List automated alerting rules."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = AlertManager(ctx)
    rules = mgr.list_rules()

    if json_output:
        emit_json("alerts_rules", {"rules": rules})
        return

    table = Table(title="Automated Alert Rules", box=None)
    table.add_column("Rule ID", style="cyan")
    table.add_column("Type", style="bold white")
    table.add_column("Threshold", style="yellow")
    table.add_column("Status", style="green")

    for r in rules:
        thresh_str = str(r["threshold"]) if r["threshold"] is not None else "Any"
        status_str = "[green]Enabled[/green]" if r["enabled"] else "[dim]Disabled[/dim]"
        table.add_row(r["id"], r["rule_type"], thresh_str, status_str)

    console.print(table)
