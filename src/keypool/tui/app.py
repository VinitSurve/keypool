"""Polished interactive Terminal User Interface (TUI) for KEYPOOL (Section 17)."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, build_status_summary
from keypool.storage.models import HealthState
from keypool.version import APP_DISPLAY_NAME, __version__


def create_header() -> Panel:
    """Render top branding banner."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right", ratio=1)
    grid.add_row(
        f"[bold cyan]{APP_DISPLAY_NAME}[/bold cyan] [dim]v{__version__}[/dim]",
        "[cyan]Local AI Credential & Infrastructure Platform[/cyan]",
    )
    return Panel(grid, style="cyan", box=box.ROUNDED)


def render_overview_tab(ctx: RuntimeContext) -> Layout:
    """Render system overview with credentials, health, and usage status."""
    summary = build_status_summary(ctx)
    layout = Layout()
    layout.split_column(
        Layout(name="metrics", size=6),
        Layout(name="body"),
    )

    metrics_table = Table(box=box.ROUNDED, expand=True)
    metrics_table.add_column("Providers", justify="center", style="cyan")
    metrics_table.add_column("Total Keys", justify="center", style="white")
    metrics_table.add_column("Healthy", justify="center", style="green")
    metrics_table.add_column("Cooldown / Rate Limit", justify="center", style="yellow")
    metrics_table.add_column("Failed / Error", justify="center", style="red")

    metrics_table.add_row(
        str(len(ctx.registry.list_providers())),
        str(summary["credentials"]["total"]),
        str(summary["credentials"]["healthy"]),
        str(summary["credentials"]["cooldown"]),
        str(summary["credentials"]["auth_failed"] + summary["credentials"]["keychain_error"]),
    )
    layout["metrics"].update(
        Panel(metrics_table, title="[bold]System Status[/bold]", box=box.ROUNDED)
    )

    # Body with active credentials table
    creds = ctx.credential_manager.list_credentials()
    cred_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    cred_table.add_column("ID", style="bold white", width=14)
    cred_table.add_column("Name", style="white")
    cred_table.add_column("Provider", style="cyan", width=12)
    cred_table.add_column("Status", width=18)
    cred_table.add_column("Profiles", style="dim")
    cred_table.add_column("Last Used", style="dim")

    for c in creds[:10]:
        status_style = (
            "green"
            if c.status == HealthState.HEALTHY.value
            else "yellow"
            if c.status == HealthState.UNTESTED.value
            else "red"
        )
        cred_table.add_row(
            c.id,
            c.name,
            c.provider,
            f"[{status_style}]{c.effective_status}[/{status_style}]",
            ", ".join(c.profiles) or "-",
            c.last_used_at or "never",
        )

    layout["body"].update(
        Panel(cred_table, title="[bold]Active Credentials Snapshot[/bold]", box=box.ROUNDED)
    )
    return layout


def run_tui(ctx: RuntimeContext | None = None) -> None:
    """Launch interactive TUI or render full dashboard."""
    context = ctx or RuntimeContext()
    console = Console()

    header = create_header()
    overview = render_overview_tab(context)

    console.clear()
    console.print(header)
    console.print(overview)

    footer = Table.grid(expand=True)
    footer.add_column(justify="left")
    footer.add_row(
        "[dim]Press Ctrl+C or run specific CLI commands ('keypool list', 'keypool models', 'keypool gateway') for interactive management.[/dim]"
    )
    console.print(Panel(footer, box=box.ROUNDED, style="dim"))
