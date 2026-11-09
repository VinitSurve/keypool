"""CLI commands for cost controls and usage budgets (Section 13)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.core.budget_manager import BudgetManager
from keypool.integrations.json_output import emit_json

budgets_app = typer.Typer(
    name="budget",
    help="Manage usage budgets and cost controls (Section 13).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@budgets_app.callback()
def budget_index(
    ctx_typer: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List configured budgets and current period spend."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    bm = BudgetManager(ctx.db)
    budgets = bm.list_budgets()

    if json_output:
        emit_json("budgets", {"budgets": [b.to_public_dict() for b in budgets]})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]Configured Budgets & Cost Controls[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("ID", style="bold white", width=12)
    table.add_column("Name", style="white")
    table.add_column("Scope", style="magenta")
    table.add_column("Period", style="cyan")
    table.add_column("Limit", justify="right", style="white")
    table.add_column("Current Spend", justify="right")
    table.add_column("Used %", justify="right")
    table.add_column("Status", justify="center")

    for b in budgets:
        scope_str = f"{b.scope_type}:{b.scope_target}" if b.scope_target else b.scope_type
        stat_color = "red" if b.is_exceeded else "yellow" if b.is_warning else "green"
        stat_label = "EXCEEDED" if b.is_exceeded else "WARNING" if b.is_warning else "OK"
        table.add_row(
            b.id,
            b.name,
            scope_str,
            b.period,
            f"${b.amount_limit:.2f}",
            f"${b.current_spend:.4f}",
            f"{b.percent_used:.1f}%",
            f"[{stat_color}]{stat_label}[/{stat_color}]",
        )
    console.print(table)


@budgets_app.command("set")
def cmd_budget_set(
    name: str = typer.Argument(..., help="Descriptive budget label."),
    amount: float = typer.Option(..., "--amount", "-a", help="Budget limit amount (e.g. 50.0)."),
    scope: str = typer.Option(
        "global", "--scope", "-s", help="Budget scope ('global', 'provider', 'project', 'profile')."
    ),
    target: str | None = typer.Option(
        None, "--target", "-t", help="Scope target (e.g. provider name or project id)."
    ),
    period: str = typer.Option(
        "monthly", "--period", "-p", help="Budget period ('daily', 'weekly', 'monthly')."
    ),
    hard_limit: bool = typer.Option(
        False, "--hard-limit", help="Block requests when budget is exceeded."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Create or update a budget limit."""
    ctx = RuntimeContext()
    bm = BudgetManager(ctx.db)
    b = bm.set_budget(
        name=name,
        amount_limit=amount,
        scope_type=scope,
        scope_target=target,
        period=period,
        hard_limit_enabled=hard_limit,
    )

    if json_output:
        emit_json("budget.set", b.to_public_dict())
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(
        Panel(
            f"[bold green]Budget '{b.name}' configured successfully.[/bold green]\n"
            f"[dim]ID:[/dim] {b.id}\n"
            f"[dim]Scope:[/dim] {b.scope_type}{f':{b.scope_target}' if b.scope_target else ''}\n"
            f"[dim]Limit:[/dim] ${b.amount_limit:.2f} / {b.period}\n"
            f"[dim]Hard limit enforcement:[/dim] {'Enabled' if b.hard_limit_enabled else 'Disabled (alerts only)'}",
            title="Budget Configured",
            border_style="green",
            box=box.ROUNDED,
        )
    )


@budgets_app.command("check")
def cmd_budget_check(
    provider: str | None = typer.Option(None, "--provider", "-p", help="Provider to check."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Evaluate all budgets and check for threshold warnings or hard blocks."""
    ctx = RuntimeContext()
    bm = BudgetManager(ctx.db)
    exceeded = bm.check_hard_limits(provider=provider)

    if json_output:
        emit_json("budget.check", {"blocked": [b.to_public_dict() for b in exceeded]})
        return

    console = get_console(color_mode=ctx.settings.color)
    if exceeded:
        console.print(
            f"[bold red]WARNING: {len(exceeded)} hard-limited budget(s) exceeded![/bold red]"
        )
        for b in exceeded:
            console.print(
                f"  • {b.name} ({b.scope_type}): ${b.current_spend:.2f} >= ${b.amount_limit:.2f}"
            )
    else:
        console.print(
            "[bold green]All configured budgets are within acceptable limits.[/bold green]"
        )
