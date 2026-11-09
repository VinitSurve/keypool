"""CLI commands for credential lifecycle management (Section 7)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import (
    RuntimeContext,
    format_enabled_badge,
    format_health_badge,
    format_timestamp,
    get_console,
    render_credentials_table,
)
from keypool.cli.prompts import (
    confirm_destructive_action,
    prompt_api_key,
    prompt_credential_name,
)
from keypool.core.exceptions import ExitCode, InvalidArgumentError
from keypool.integrations.json_output import emit_json
from keypool.security.redaction import redact_text
from keypool.storage.models import HealthState


def cmd_add(
    provider: str = typer.Argument(..., help="Target AI provider ('groq' or 'gemini')."),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Optional friendly label for the credential (prompted if omitted).",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Optionally assign the new credential directly to a profile.",
    ),
) -> None:
    """Interactively add a new API credential and store its secret in macOS Keychain."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)

    # 1. Validate provider first
    adapter = ctx.registry.get(provider)

    # 2. Prompt for friendly name if not supplied
    friendly_name = name.strip() if name else prompt_credential_name(adapter.display_name)

    # 3. Request API key using hidden terminal input (never passed as CLI arg)
    secret = prompt_api_key(adapter.display_name)

    # 4-6. Validate input, store in macOS Keychain, register metadata in SQLite
    meta = ctx.credential_manager.add_credential(
        provider=adapter.name,
        name=friendly_name,
        secret=secret,
    )

    if profile:
        ctx.profile_manager.assign_credential(profile, meta.id)
        meta = ctx.credential_manager.get_credential(meta.id)

    try:
        state_label = HealthState(meta.status).label
    except ValueError:
        state_label = meta.status

    lines = [
        "[bold green]✓ Credential added successfully[/bold green]\n",
        f"[bold]Name:[/bold] {meta.name}",
        f"[bold]Provider:[/bold] {adapter.display_name}",
        f"[bold]ID:[/bold] [bold cyan]{meta.id}[/bold cyan]",
        f"[bold]Storage:[/bold] {meta.storage_backend}",
        f"[bold]Preview:[/bold] [dim]{meta.masked_preview or '••••••••'}[/dim]",
        f"[bold]Status:[/bold] [yellow]{state_label}[/yellow]",
    ]
    if meta.profiles:
        lines.append(f"[bold]Profiles:[/bold] {', '.join(meta.profiles)}")
    if meta.warnings:
        for w in meta.warnings:
            lines.append(f"\n[yellow]⚠ {redact_text(w)}[/yellow]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold cyan]KEYPOOL Credential Added[/bold cyan]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


def cmd_list(
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter credentials by provider ('groq' or 'gemini').",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON metadata.",
    ),
) -> None:
    """List managed API credentials and their health states."""
    ctx = RuntimeContext()
    credentials = ctx.credential_manager.list_credentials(provider=provider)

    if json_output:
        emit_json(
            "list",
            {
                "count": len(credentials),
                "provider_filter": provider.strip().lower() if provider else None,
                "credentials": [c.to_public_dict() for c in credentials],
            },
        )
        return

    console = get_console(color_mode=ctx.settings.color)
    title = (
        f"Managed Credentials ({provider.strip().upper()})" if provider else "Managed Credentials"
    )
    render_credentials_table(credentials, console, title=title)


def cmd_show(
    credential_id: str = typer.Argument(..., help="Credential ID (e.g. cred_a83f21)."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON metadata.",
    ),
) -> None:
    """Display non-secret metadata for a specific credential."""
    ctx = RuntimeContext()
    meta = ctx.credential_manager.get_credential(credential_id)
    adapter = ctx.registry.get(meta.provider)

    if json_output:
        emit_json("show", {"credential": meta.to_public_dict()})
        return

    console = get_console(color_mode=ctx.settings.color)
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="bold cyan", width=20)
    grid.add_column("Value")

    grid.add_row("ID", f"[bold white]{meta.id}[/bold white]")
    grid.add_row("Name", meta.name)
    grid.add_row("Provider", f"{adapter.display_name} ({meta.provider})")
    grid.add_row("Keychain Reference", f"[dim]{meta.keychain_reference}[/dim]")
    grid.add_row("Storage Backend", meta.storage_backend)
    grid.add_row("State", format_enabled_badge(meta.enabled))
    grid.add_row("Health", format_health_badge(meta.status, enabled=meta.enabled))
    grid.add_row(
        "Assigned Profiles",
        ", ".join(meta.profiles) if meta.profiles else "[dim]None[/dim]",
    )
    grid.add_row("Created At", format_timestamp(meta.created_at))
    grid.add_row("Updated At", format_timestamp(meta.updated_at))
    grid.add_row("Last Tested", format_timestamp(meta.last_tested_at))
    grid.add_row("Last Successful", format_timestamp(meta.last_successful_at))
    grid.add_row("Last Failure", format_timestamp(meta.last_failure_at))
    grid.add_row("Last Used", format_timestamp(meta.last_used_at))
    if meta.cooldown_until:
        grid.add_row("Cooldown Until", format_timestamp(meta.cooldown_until))
    if meta.last_tested_model:
        grid.add_row("Last Tested Model", meta.last_tested_model)
    if meta.cached_models:
        models_preview = ", ".join(meta.cached_models[:8])
        if len(meta.cached_models) > 8:
            models_preview += f" (+{len(meta.cached_models) - 8} more)"
        grid.add_row("Cached Models", models_preview)
    if meta.last_error_message:
        grid.add_row(
            "Last Observation",
            redact_text(meta.last_error_message),
        )

    console.print(
        Panel(
            grid,
            title=f"[bold cyan]Credential Details — {meta.id}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )


def cmd_update(
    credential_id: str = typer.Argument(..., help="Credential ID to update."),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="New friendly name for the credential.",
    ),
    replace_key: bool | None = typer.Option(
        None,
        "--replace-key/--no-replace-key",
        help="Whether to replace the secret stored in macOS Keychain.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt when replacing the stored secret.",
    ),
) -> None:
    """Update a credential's friendly name and/or replace its stored Keychain secret."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    current = ctx.credential_manager.get_credential(credential_id)
    adapter = ctx.registry.get(current.provider)

    new_name = name.strip() if name else None
    if new_name is None and replace_key is None:
        # Interactive update flow
        prompted_name = prompt_credential_name(adapter.display_name, default=current.name)
        if prompted_name != current.name:
            new_name = prompted_name
        should_replace = confirm_destructive_action(
            f"Replace stored API key in macOS Keychain for '{current.id}' ({current.name})?",
            default=False,
        )
    else:
        should_replace = bool(replace_key)

    new_secret: str | None = None
    if should_replace:
        if not yes and replace_key is True:
            confirmed = confirm_destructive_action(
                f"Confirm replacing the stored secret for '{current.id}' ({current.name}) in macOS Keychain?",
                default=False,
            )
            if not confirmed:
                console.print("[yellow]Credential update cancelled.[/yellow]")
                raise typer.Exit(int(ExitCode.SUCCESS))
        new_secret = prompt_api_key(adapter.display_name)

    if new_name is None and new_secret is None:
        raise InvalidArgumentError(
            "No changes specified for credential update.",
            hint="Provide a new name or confirm replacing the API key.",
        )

    updated = ctx.credential_manager.update_credential(
        current.id,
        new_name=new_name,
        new_secret=new_secret,
    )

    console.print(
        Panel(
            f"[bold green]✓ Credential updated successfully[/bold green]\n\n"
            f"[bold]ID:[/bold] [bold cyan]{updated.id}[/bold cyan]\n"
            f"[bold]Name:[/bold] {updated.name}\n"
            f"[bold]Provider:[/bold] {adapter.display_name}\n"
            f"[bold]Secret Replaced:[/bold] {'Yes (Status reset to Untested)' if new_secret else 'No'}",
            title="[bold cyan]KEYPOOL Credential Updated[/bold cyan]",
            border_style="green",
            box=box.ROUNDED,
            expand=False,
        )
    )


def cmd_rename(
    credential_id: str = typer.Argument(..., help="Credential ID to rename."),
    new_name: str | None = typer.Argument(
        None,
        help="New friendly name (prompted interactively if omitted).",
    ),
) -> None:
    """Rename a credential's friendly label."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    current = ctx.credential_manager.get_credential(credential_id)
    adapter = ctx.registry.get(current.provider)

    resolved_name = (
        new_name.strip()
        if new_name
        else prompt_credential_name(adapter.display_name, default=current.name)
    )
    updated = ctx.credential_manager.rename_credential(current.id, resolved_name)
    console.print(
        f"[bold green]✓[/bold green] Renamed [bold cyan]{updated.id}[/bold cyan] to [bold]{updated.name}[/bold]."
    )


def cmd_enable(
    credential_id: str = typer.Argument(..., help="Credential ID to enable."),
) -> None:
    """Enable a credential so it becomes eligible for selection."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.credential_manager.enable_credential(credential_id)
    console.print(
        f"[bold green]✓[/bold green] Credential [bold cyan]{updated.id}[/bold cyan] ({updated.name}) is now [bold green]Enabled[/bold green]."
    )


def cmd_disable(
    credential_id: str = typer.Argument(..., help="Credential ID to disable."),
) -> None:
    """Disable a credential so it is excluded from all selections."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.credential_manager.disable_credential(credential_id)
    console.print(
        f"[bold yellow]○[/bold yellow] Credential [bold cyan]{updated.id}[/bold cyan] ({updated.name}) is now [dim]Disabled[/dim]."
    )


def cmd_remove(
    credential_id: str = typer.Argument(..., help="Credential ID to permanently remove."),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip interactive confirmation prompt.",
    ),
) -> None:
    """Delete a credential from both macOS Keychain and the SQLite metadata database."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    meta = ctx.credential_manager.get_credential(credential_id)

    if not yes:
        confirmed = confirm_destructive_action(
            f"Permanently delete credential '{meta.id}' ({meta.name}, provider={meta.provider}) from macOS Keychain and SQLite?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Removal cancelled.[/yellow]")
            raise typer.Exit(int(ExitCode.SUCCESS))

    removed = ctx.credential_manager.remove_credential(meta.id)
    console.print(
        f"[bold green]✓[/bold green] Removed credential [bold cyan]{removed.id}[/bold cyan] ({removed.name}) from macOS Keychain and SQLite."
    )
