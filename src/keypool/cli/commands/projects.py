"""CLI commands for managing workspace project configurations (Section 9)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.core.project_manager import ProjectManager
from keypool.integrations.json_output import emit_json

projects_app = typer.Typer(
    name="project",
    help="Manage project workspaces and local credential bindings (Section 9).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@projects_app.callback()
def project_index(
    ctx_typer: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List all registered projects."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    pm = ProjectManager(ctx.db)
    projects = pm.list_projects()

    if json_output:
        emit_json("projects", {"projects": [p.to_public_dict() for p in projects]})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]Registered Project Workspaces[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Project ID", style="bold white", width=14)
    table.add_column("Name", style="white")
    table.add_column("Root Directory", style="dim")
    table.add_column("Profile", style="cyan")

    for p in projects:
        table.add_row(
            p.id,
            p.name,
            p.root_dir,
            p.profile_name or "[dim]default[/dim]",
        )
    console.print(table)


@projects_app.command("list")
def cmd_project_list(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List registered projects (alias for project callback)."""
    ctx = RuntimeContext()
    pm = ProjectManager(ctx.db)
    projects = pm.list_projects()

    if json_output:
        emit_json("projects.list", {"projects": [p.to_public_dict() for p in projects]})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]Registered Projects[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Project ID", style="bold white", width=14)
    table.add_column("Name", style="white")
    table.add_column("Root Directory", style="dim")
    table.add_column("Profile", style="cyan")

    for p in projects:
        table.add_row(
            p.id,
            p.name,
            p.root_dir,
            p.profile_name or "[dim]default[/dim]",
        )
    console.print(table)


@projects_app.command("register")
def cmd_project_register(
    name: str = typer.Option(..., "--name", "-n", help="Human-friendly project name."),
    path: str = typer.Option(".", "--path", "-p", help="Project root workspace directory."),
    profile: str | None = typer.Option(
        None, "--profile", help="Binding profile name for this project."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Register a workspace directory with KEYPOOL."""
    ctx = RuntimeContext()
    pm = ProjectManager(ctx.db)
    proj = pm.register_project(name=name, root_dir=Path(path), profile_name=profile)

    if json_output:
        emit_json("project.register", proj.to_public_dict())
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(
        Panel(
            f"[bold green]Project '{proj.name}' successfully registered.[/bold green]\n"
            f"[dim]ID:[/dim] {proj.id}\n"
            f"[dim]Root:[/dim] {proj.root_dir}\n"
            f"[dim]Profile:[/dim] {proj.profile_name or 'none'}",
            title="Project Registered",
            border_style="green",
            box=box.ROUNDED,
        )
    )


@projects_app.command("inspect")
def cmd_project_inspect(
    project: str = typer.Argument(..., help="Project ID or name to inspect."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Inspect detailed configuration for a registered project."""
    ctx = RuntimeContext()
    pm = ProjectManager(ctx.db)
    proj = pm.get_project(project)
    if proj is None:
        console = get_console(color_mode=ctx.settings.color)
        console.print(f"[red]Project '{project}' not found.[/red]")
        raise typer.Exit(1)

    if json_output:
        emit_json("project.inspect", proj.to_public_dict())
        return

    console = get_console(color_mode=ctx.settings.color)
    grid = Table(show_header=False, box=None)
    grid.add_column("Field", style="bold cyan", width=20)
    grid.add_column("Value")

    grid.add_row("Project ID", proj.id)
    grid.add_row("Name", proj.name)
    grid.add_row("Root Directory", proj.root_dir)
    grid.add_row("Bound Profile", proj.profile_name or "[dim]None[/dim]")
    grid.add_row("Created At", proj.created_at)

    console.print(
        Panel(
            grid,
            title=f"[bold cyan]Project — {proj.name}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
