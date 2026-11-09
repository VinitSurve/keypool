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


def cmd_rotate(
    credential_id: str = typer.Argument(..., help="Credential ID whose API key will be rotated."),
    new_key: str | None = typer.Option(
        None,
        "--key",
        "-k",
        help="Optional new API key (prompted securely if omitted).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip interactive confirmation.",
    ),
) -> None:
    """Rotate the underlying secret in macOS Keychain for a credential."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    meta = ctx.credential_manager.get_credential(credential_id)
    adapter = ctx.registry.get(meta.provider)

    if not yes:
        confirmed = confirm_destructive_action(
            f"Rotate API key for '{meta.id}' ({meta.name}, provider={meta.provider})?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Rotation cancelled.[/yellow]")
            raise typer.Exit(int(ExitCode.SUCCESS))

    secret = new_key.strip() if new_key else prompt_api_key(adapter.display_name)
    updated = ctx.credential_manager.rotate_credential(meta.id, secret)
    console.print(
        f"[bold green]✓[/bold green] Credential [bold cyan]{updated.id}[/bold cyan] ({updated.name}) rotated successfully in macOS Keychain."
    )


def cmd_reminders(
    days: int = typer.Option(7, "--days", "-d", help="Threshold in days to check for nearing expirations."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Inspect credential rotation reminders and nearing expirations."""
    ctx = RuntimeContext()
    reminders = ctx.credential_manager.check_rotation_reminders(threshold_days=days)

    if json_output:
        emit_json("reminders", {"count": len(reminders), "reminders": reminders})
        return

    console = get_console(color_mode=ctx.settings.color)
    if not reminders:
        console.print("[bold green]✓ All credentials are valid and within rotation policies.[/bold green]")
        return

    table = Table(title="[bold yellow]Credential Rotation & Expiry Reminders[/bold yellow]", box=box.ROUNDED)
    table.add_column("Severity", style="bold")
    table.add_column("ID", style="bold cyan")
    table.add_column("Name", style="white")
    table.add_column("Provider", style="cyan")
    table.add_column("State", style="magenta")
    table.add_column("Message")

    for r in reminders:
        sev_style = "red" if r["severity"] == "critical" else "yellow"
        table.add_row(
            f"[{sev_style}]{r['severity'].upper()}[/{sev_style}]",
            r["credential_id"],
            r["name"],
            r["provider"],
            r["state"],
            r["message"],
        )

    console.print(table)


def cmd_history(
    credential_id: str = typer.Argument(..., help="Credential ID to inspect history for."),
    limit: int = typer.Option(25, "--limit", "-l", help="Maximum historical entries to return."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """View complete audit history and event timeline for a credential."""
    ctx = RuntimeContext()
    meta = ctx.credential_manager.get_credential(credential_id)
    history = ctx.credential_manager.get_credential_history(meta.id, limit=limit)

    if json_output:
        emit_json("history", {"credential_id": meta.id, "name": meta.name, "history": history})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title=f"[bold cyan]Audit Timeline — {meta.name} ({meta.id})[/bold cyan]",
        box=box.ROUNDED,
    )
    table.add_column("Timestamp", style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Status / State", style="bold")
    table.add_column("Model", style="white")
    table.add_column("Details")

    for h in history:
        t_type = h.get("type", "")
        status_val = str(h.get("status") or h.get("state") or "")
        model_val = str(h.get("model") or h.get("model_tested") or "-")
        details = str(h.get("summary") or h.get("message") or "")
        table.add_row(
            format_timestamp(h.get("timestamp")),
            t_type,
            status_val,
            model_val,
            redact_text(details),
        )

    console.print(table)


def cmd_bulk_enable(
    credential_ids: list[str] = typer.Argument(..., help="List of credential IDs to enable."),
) -> None:
    """Enable multiple credentials at once."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.credential_manager.bulk_set_enabled(credential_ids, enabled=True)
    console.print(f"[bold green]✓ Enabled {len(updated)} of {len(credential_ids)} credentials.[/bold green]")


def cmd_bulk_disable(
    credential_ids: list[str] = typer.Argument(..., help="List of credential IDs to disable."),
) -> None:
    """Disable multiple credentials at once."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.credential_manager.bulk_set_enabled(credential_ids, enabled=False)
    console.print(f"[bold yellow]○ Disabled {len(updated)} of {len(credential_ids)} credentials.[/bold yellow]")


def cmd_bulk_remove(
    credential_ids: list[str] = typer.Argument(..., help="List of credential IDs to permanently remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Permanently delete multiple credentials."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    if not yes:
        confirmed = confirm_destructive_action(
            f"Permanently delete {len(credential_ids)} credentials from macOS Keychain and SQLite?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Bulk removal cancelled.[/yellow]")
            raise typer.Exit(int(ExitCode.SUCCESS))

    removed = ctx.credential_manager.bulk_remove_credentials(credential_ids)
    console.print(f"[bold green]✓ Removed {len(removed)} credentials.[/bold green]")


def cmd_bulk_tag(
    credential_ids: list[str] = typer.Argument(..., help="List of credential IDs to tag."),
    tag: list[str] = typer.Option(..., "--tag", "-t", help="Tag(s) to assign."),
) -> None:
    """Assign tags to multiple credentials."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    updated = ctx.credential_manager.bulk_tag(credential_ids, tag)
    console.print(f"[bold green]✓ Tagged {len(updated)} credentials with: {', '.join(tag)}[/bold green]")


def cmd_export_keys(
    output_path: str = typer.Option(..., "--output", "-o", help="File path to write encrypted bundle to."),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Filter by provider."),
    passphrase: str | None = typer.Option(None, "--passphrase", help="Bundle encryption passphrase."),
) -> None:
    """Export credentials securely into an AES-256-GCM encrypted file."""
    import getpass
    from pathlib import Path
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    pwd = passphrase or getpass.getpass("Enter bundle encryption passphrase: ")
    if not pwd:
        console.print("[red]Passphrase cannot be empty.[/red]")
        raise typer.Exit(1)

    bundle_bytes = ctx.credential_manager.export_credentials_bundle(pwd, provider=provider)
    out_file = Path(output_path).expanduser().resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(bundle_bytes)
    out_file.chmod(0o600)
    console.print(f"[bold green]✓ Successfully exported encrypted credential bundle to {out_file}[/bold green]")


def cmd_import_keys(
    input_path: str = typer.Argument(..., help="Path to encrypted credentials bundle file."),
    passphrase: str | None = typer.Option(None, "--passphrase", help="Bundle decryption passphrase."),
    allow_duplicate: bool = typer.Option(False, "--allow-duplicate", help="Allow importing duplicate API keys."),
) -> None:
    """Import credentials safely from an encrypted bundle file into macOS Keychain."""
    import getpass
    from pathlib import Path
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)
    in_file = Path(input_path).expanduser().resolve()
    if not in_file.exists():
        console.print(f"[red]File '{in_file}' not found.[/red]")
        raise typer.Exit(1)

    pwd = passphrase or getpass.getpass("Enter bundle decryption passphrase: ")
    bundle_bytes = in_file.read_bytes()
    imported = ctx.credential_manager.import_credentials_bundle(bundle_bytes, pwd, allow_duplicate=allow_duplicate)
    console.print(f"[bold green]✓ Successfully imported {len(imported)} credentials into macOS Keychain and SQLite.[/bold green]")
