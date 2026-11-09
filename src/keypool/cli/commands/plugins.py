"""CLI commands for managing plugins (Section 21)."""

from __future__ import annotations

import typer
from rich import box
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.integrations.json_output import emit_json
from keypool.plugins.manager import PluginManager

plugins_app = typer.Typer(
    name="plugin",
    help="Manage KEYPOOL provider and integration plugins (Section 21).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@plugins_app.callback()
def plugin_index(
    ctx_typer: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List installed plugins and their activation status."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    pm = PluginManager(ctx)
    plugins = pm.list_plugins()

    if json_output:
        emit_json("plugins", {"plugins": [p.model_dump() for p in plugins]})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]KEYPOOL Plugin Registry[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Plugin ID", style="bold white")
    table.add_column("Name", style="white")
    table.add_column("Type", style="magenta")
    table.add_column("Version", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Description", style="dim")

    for p in plugins:
        stat = "[green]Enabled[/green]" if p.enabled else "[dim]Disabled[/dim]"
        table.add_row(p.id, p.name, p.plugin_type, p.version, stat, p.description)

    console.print(table)


@plugins_app.command("enable")
def cmd_plugin_enable(
    plugin_id: str = typer.Argument(..., help="Plugin ID to enable."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Enable an installed plugin."""
    ctx = RuntimeContext()
    pm = PluginManager(ctx)
    meta = pm.enable_plugin(plugin_id)

    if json_output:
        emit_json("plugin.enable", meta.model_dump())
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(f"[bold green]Plugin '{meta.name}' ({meta.id}) enabled.[/bold green]")


@plugins_app.command("disable")
def cmd_plugin_disable(
    plugin_id: str = typer.Argument(..., help="Plugin ID to disable."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Disable an active plugin."""
    ctx = RuntimeContext()
    pm = PluginManager(ctx)
    meta = pm.disable_plugin(plugin_id)

    if json_output:
        emit_json("plugin.disable", meta.model_dump())
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(f"[yellow]Plugin '{meta.name}' ({meta.id}) disabled.[/yellow]")
