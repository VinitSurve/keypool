"""CLI commands for encrypted backup creation and restoration (Section 19)."""

from __future__ import annotations

import getpass
from pathlib import Path

import typer
from rich import box
from rich.panel import Panel

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.core.backup_manager import BackupManager
from keypool.core.exceptions import InvalidArgumentError
from keypool.integrations.json_output import emit_json

backup_app = typer.Typer(
    name="backup",
    help="Create authenticated encrypted backups of metadata and Keychain secrets (Section 19).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@backup_app.callback()
def backup_index(
    ctx_typer: typer.Context,
) -> None:
    if ctx_typer.invoked_subcommand is None:
        console = get_console()
        console.print(
            "[cyan]Run 'keypool backup create <file>' or 'keypool restore <file>'.[/cyan]"
        )


@backup_app.command("create")
def cmd_backup_create(
    output_path: str = typer.Argument(..., help="Destination path for encrypted backup bundle."),
    password: str | None = typer.Option(
        None, "--password", "-P", help="Encryption password (prompted if omitted)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Create an encrypted AES-256-GCM backup bundle."""
    ctx = RuntimeContext()
    bm = BackupManager(ctx.db, ctx.secret_store, ctx.credential_manager)

    pass_to_use = password
    if not pass_to_use:
        pass_to_use = getpass.getpass("Enter encryption password for backup: ")
        confirm = getpass.getpass("Confirm encryption password: ")
        if pass_to_use != confirm:
            raise InvalidArgumentError("Passwords do not match.")

    count = bm.create_backup(Path(output_path), password=pass_to_use)

    if json_output:
        emit_json("backup.create", {"path": output_path, "credentials_backed_up": count})
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(
        Panel(
            f"[bold green]Encrypted backup created successfully.[/bold green]\n"
            f"[dim]File:[/dim] {output_path}\n"
            f"[dim]Credentials Archived:[/dim] {count}\n"
            f"[dim]Encryption:[/dim] AES-256-GCM (PBKDF2-HMAC-SHA256, 600k rounds)",
            title="Backup Complete",
            border_style="green",
            box=box.ROUNDED,
        )
    )


def cmd_restore(
    backup_file: str = typer.Argument(..., help="Path to encrypted backup bundle file."),
    password: str | None = typer.Option(
        None, "--password", "-P", help="Decryption password (prompted if omitted)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Restore metadata and Keychain secrets from an encrypted backup bundle."""
    ctx = RuntimeContext()
    bm = BackupManager(ctx.db, ctx.secret_store, ctx.credential_manager)

    pass_to_use = password
    if not pass_to_use:
        pass_to_use = getpass.getpass("Enter decryption password for backup: ")

    report = bm.restore_backup(Path(backup_file), password=pass_to_use)

    if json_output:
        emit_json(
            "restore",
            {
                "credentials_restored": report.credentials_restored,
                "profiles_restored": report.profiles_restored,
                "projects_restored": report.projects_restored,
                "budgets_restored": report.budgets_restored,
                "warnings": report.warnings,
            },
        )
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(
        Panel(
            f"[bold green]Backup restored successfully.[/bold green]\n"
            f"[dim]Credentials restored:[/dim] {report.credentials_restored}\n"
            f"[dim]Profiles restored:[/dim] {report.profiles_restored}\n"
            f"[dim]Projects restored:[/dim] {report.projects_restored}\n"
            f"[dim]Budgets restored:[/dim] {report.budgets_restored}",
            title="Restore Complete",
            border_style="green",
            box=box.ROUNDED,
        )
    )
