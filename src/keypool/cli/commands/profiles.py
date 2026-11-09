"""CLI commands for profile management and explicit credential assignments (Section 11)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import (
    RuntimeContext,
    format_timestamp,
    get_console,
    render_credentials_table,
)
from keypool.cli.prompts import confirm_destructive_action
from keypool.core.exceptions import ExitCode
from keypool.integrations.json_output import emit_json

profile_app = typer.Typer(
    name="profile",
    help="Manage isolated execution profiles (testing, development, production, and custom).",
    no_args_is_help=True,
)


@profile_app.command("list")
def profile_list(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON profile metadata.",
    ),
) -> None:
    """List all execution profiles and their assigned credential counts."""
    ctx = RuntimeContext()
    profiles = ctx.profile_manager.list_profiles()
    active_profile = ctx.settings.default_profile

    if json_output:
        emit_json(
            "profile.list",
            {
                "active_profile": active_profile,
                "count": len(profiles),
                "profiles": [p.to_public_dict() for p in profiles],
            },
        )
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]Execution Profiles[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Profile", style="bold white")
    table.add_column("Type", style="cyan")
    table.add_column("Default", justify="center")
    table.add_column("Credentials", justify="right")
    table.add_column("Assigned IDs", style="blue")
    table.add_column("Description", style="dim")

    for prof in profiles:
        is_default = "[bold green]●[/bold green]" if prof.name == active_profile else ""
        kind = "Built-in" if prof.is_builtin else "Custom"
        cids = ", ".join(prof.assigned_credential_ids) if prof.assigned_credential_ids else "—"
        table.add_row(
            prof.name,
            kind,
            is_default,
            str(len(prof.assigned_credential_ids)),
            cids,
            prof.description,
        )

    console.print(table)


@profile_app.command("show")
def profile_show(
    profile_name: str = typer.Argument(
        ..., help="Profile name (e.g. testing, development, production)."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON profile metadata.",
    ),
) -> None:
    """Inspect a profile and its explicitly assigned credentials."""
    ctx = RuntimeContext()
    details = ctx.profile_manager.get_profile(profile_name)

    if json_output:
        emit_json("profile.show", {"profile": details.to_public_dict()})
        return

    console = get_console(color_mode=ctx.settings.color)
    info_grid = Table(show_header=False, box=None, padding=(0, 2))
    info_grid.add_column("Field", style="bold cyan", width=20)
    info_grid.add_column("Value")

    info_grid.add_row("Profile Name", f"[bold white]{details.name}[/bold white]")
    info_grid.add_row("Type", "Built-in" if details.is_builtin else "Custom")
    info_grid.add_row("Description", details.description or "—")
    info_grid.add_row("Preferred Provider", details.preferred_provider or "None")
    info_grid.add_row("Assigned Credentials", str(len(details.assigned_credential_ids)))
    info_grid.add_row("Created At", format_timestamp(details.created_at))
    info_grid.add_row("Updated At", format_timestamp(details.updated_at))

    console.print(
        Panel(
            info_grid,
            title=f"[bold cyan]Profile — {details.name}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )
    console.print()
    render_credentials_table(
        details.credentials,
        console,
        title=f"Credentials Assigned to '{details.name}'",
    )


@profile_app.command("create")
def profile_create(
    profile_name: str = typer.Argument(..., help="Name for the new profile (e.g. staging)."),
    description: str = typer.Option(
        "",
        "--description",
        "-d",
        help="Optional description for the profile.",
    ),
    preferred_provider: str | None = typer.Option(
        None,
        "--preferred-provider",
        help="Optional preferred provider within this profile.",
    ),
) -> None:
    """Create a new custom execution profile (starts with zero assigned credentials)."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    if preferred_provider:
        _ = ctx.registry.get(preferred_provider)

    created = ctx.profile_manager.create_profile(
        profile_name,
        description=description,
        preferred_provider=preferred_provider,
    )
    console.print(
        f"[bold green]✓[/bold green] Created profile [bold cyan]{created.name}[/bold cyan] "
        "(0 credentials assigned; use [bold]keypool profile assign[/bold] to grant access)."
    )


@profile_app.command("assign")
def profile_assign(
    profile_name: str = typer.Argument(..., help="Target profile name (e.g. testing)."),
    credential_id: str = typer.Argument(..., help="Credential ID to assign (e.g. cred_a83f21)."),
    priority: int = typer.Option(
        100,
        "--priority",
        help="Selection priority within the profile (lower numbers are preferred first).",
    ),
) -> None:
    """Explicitly assign a credential to a profile."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.profile_manager.assign_credential(
        profile_name,
        credential_id,
        priority=priority,
    )
    console.print(
        f"[bold green]✓[/bold green] Assigned credential [bold cyan]{credential_id}[/bold cyan] "
        f"to profile [bold cyan]{updated.name}[/bold cyan]."
    )


@profile_app.command("unassign")
def profile_unassign(
    profile_name: str = typer.Argument(..., help="Profile name (e.g. testing)."),
    credential_id: str = typer.Argument(..., help="Credential ID to remove from the profile."),
) -> None:
    """Remove a credential assignment from a profile."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.profile_manager.unassign_credential(profile_name, credential_id)
    console.print(
        f"[bold green]✓[/bold green] Unassigned credential [bold cyan]{credential_id}[/bold cyan] "
        f"from profile [bold cyan]{updated.name}[/bold cyan]."
    )


@profile_app.command("delete")
def profile_delete(
    profile_name: str = typer.Argument(..., help="Custom profile name to delete (e.g. staging)."),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip interactive confirmation prompt.",
    ),
) -> None:
    """Delete a custom profile (built-in profiles cannot be deleted)."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    details = ctx.profile_manager.get_profile(profile_name)

    if not details.is_builtin and not yes:
        confirmed = confirm_destructive_action(
            f"Delete custom profile '{details.name}'? (Assigned credentials themselves will not be deleted)",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Profile deletion cancelled.[/yellow]")
            raise typer.Exit(int(ExitCode.SUCCESS))

    deleted = ctx.profile_manager.delete_profile(profile_name)
    console.print(
        f"[bold green]✓[/bold green] Deleted custom profile [bold cyan]{deleted.name}[/bold cyan]."
    )
