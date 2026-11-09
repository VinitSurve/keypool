"""CLI commands for multi-device encrypted synchronization."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.integrations.json_output import emit_json
from keypool.sync.manager import SyncManager

sync_app = typer.Typer(
    name="sync",
    help="Manage multi-device encrypted credential synchronization and device enrollment.",
    no_args_is_help=True,
)

device_app = typer.Typer(
    name="device",
    help="Manage enrolled synchronization devices.",
    no_args_is_help=True,
)
sync_app.add_typer(device_app, name="device")
sync_app.add_typer(device_app, name="devices")


@sync_app.command("status")
def sync_status(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Display synchronization status and enrolled devices."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = SyncManager(ctx)
    devices = mgr.list_devices()

    if json_output:
        emit_json("sync_status", {"enrolled_devices": devices})
        return

    table = Table(title="Multi-Device Synchronization Status", box=None)
    table.add_column("Device ID", style="bold cyan")
    table.add_column("Name", style="bold white")
    table.add_column("Role", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Registered", style="dim")

    for d in devices:
        role = (
            "[bold yellow]Current Device[/bold yellow]" if d["is_current_device"] else "Paired Peer"
        )
        status = "[red]Revoked[/red]" if d["is_revoked"] else "[green]Enrolled[/green]"
        table.add_row(d["id"], d["name"], role, status, d["registered_at"][:10])

    console.print(table)


@device_app.command("list")
def device_list(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List all enrolled synchronization devices."""
    sync_status(json_output=json_output)


@device_app.command("register")
def device_register(
    name: str = typer.Argument(..., help="Friendly name for the newly paired device."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Enroll a new trusted device."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = SyncManager(ctx)
    res = mgr.register_device(name=name)

    if json_output:
        emit_json("sync_device_register", res)
        return

    console.print(f"[bold green]Enrolled new device '{res['name']}' [{res['id']}].[/bold green]")


@device_app.command("revoke")
def device_revoke(
    device_id: str = typer.Argument(..., help="ID of the device to revoke."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Revoke a trusted synchronization device."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = SyncManager(ctx)
    revoked = mgr.revoke_device(device_id=device_id)

    if json_output:
        emit_json("sync_device_revoke", {"device_id": device_id, "revoked": revoked})
        return

    if revoked:
        console.print(f"[bold green]Device '{device_id}' has been revoked.[/bold green]")
    else:
        console.print(f"[yellow]Device '{device_id}' not found.[/yellow]")


@sync_app.command("export")
def sync_export(
    file_path: Path = typer.Argument(..., help="Target file path to write encrypted sync bundle."),
    passphrase: str = typer.Option(
        ...,
        "--passphrase",
        "-p",
        prompt=True,
        hide_input=True,
        help="Master synchronization passphrase for AES-256-GCM encryption.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Export encrypted synchronization bundle for cross-device migration."""
    console = get_console()
    ctx = RuntimeContext()
    mgr = SyncManager(ctx)
    bundle_bytes = mgr.export_sync_bundle(passphrase=passphrase, output_file=file_path)

    if json_output:
        emit_json("sync_export", {"path": str(file_path), "bundle_size_bytes": len(bundle_bytes)})
        return

    console.print(
        f"[bold green]Successfully exported encrypted sync bundle to '{file_path}' ({len(bundle_bytes):,} bytes).[/bold green]"
    )


@sync_app.command("import")
def sync_import(
    file_path: Path = typer.Argument(..., help="Path to the encrypted sync bundle file."),
    passphrase: str = typer.Option(
        ...,
        "--passphrase",
        "-p",
        prompt=True,
        hide_input=True,
        help="Master synchronization passphrase.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Import and merge encrypted synchronization bundle with conflict resolution."""
    console = get_console()
    if not file_path.exists():
        console.print(f"[red]Sync bundle file '{file_path}' does not exist.[/red]")
        raise typer.Exit(1)

    ctx = RuntimeContext()
    mgr = SyncManager(ctx)
    bundle_bytes = file_path.read_bytes()
    res = mgr.import_sync_bundle(bundle_bytes=bundle_bytes, passphrase=passphrase)

    if json_output:
        emit_json("sync_import", res)
        return

    console.print(
        f"[bold green]Sync import complete![/bold green] "
        f"Synced: [cyan]{res['credentials_synced']}[/cyan] credential(s), "
        f"Conflicts Resolved: [yellow]{res['conflicts_resolved']}[/yellow]."
    )
