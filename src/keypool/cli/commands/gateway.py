"""CLI commands for managing the Local AI Gateway."""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.gateway.auth import GatewayAuth
from keypool.gateway.server import GatewayServer

gateway_app = typer.Typer(
    name="gateway",
    help="Manage the local OpenAI-compatible AI gateway and daemon.",
    no_args_is_help=True,
)


@gateway_app.command("start")
def start_gateway(
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host interface to bind."),
    daemon: bool = typer.Option(
        False, "--daemon", "-d", help="Run gateway as a background daemon."
    ),
    no_auth: bool = typer.Option(False, "--no-auth", help="Disable bearer token authentication."),
) -> None:
    """Start the Local AI Gateway server."""
    console = get_console()
    ctx = RuntimeContext()
    server = GatewayServer(ctx)

    if daemon:
        result = server.start(host=host, port=port, daemon=True, require_auth=not no_auth)
        if result["status"] == "already_running":
            console.print(f"[yellow]Gateway is already running (PID: {result['pid']}).[/yellow]")
            return

        token_info = (
            f"\n[dim]Token:[/dim] [cyan]{result.get('token')}[/cyan]" if result.get("token") else ""
        )
        console.print(
            Panel(
                f"[bold green]Local AI Gateway daemon started![/bold green]\n"
                f"[dim]URL:[/dim] [bold white]http://{host}:{port}[/bold white]\n"
                f"[dim]PID:[/dim] [cyan]{result['pid']}[/cyan]\n"
                f"[dim]Logs:[/dim] [dim]{result.get('log_file')}[/dim]{token_info}\n\n"
                f"[bold]OpenAI SDK Integration:[/bold]\n"
                f'export OPENAI_BASE_URL="http://{host}:{port}/v1"\n'
                + (
                    f'export OPENAI_API_KEY="{result.get("token")}"\n'
                    if result.get("token")
                    else 'export OPENAI_API_KEY="none"\n'
                ),
                title="KEYPOOL Local AI Gateway",
                border_style="green",
            )
        )
    else:
        console.print(
            f"[bold cyan]Starting Keypool Local AI Gateway on http://{host}:{port}...[/bold cyan]"
        )
        if not no_auth:
            token = GatewayAuth(ctx.paths).get_or_create_token()
            console.print(f"[dim]Bearer Token:[/dim] [yellow]{token}[/yellow]")
        server.start(host=host, port=port, daemon=False, require_auth=not no_auth)


@gateway_app.command("stop")
def stop_gateway() -> None:
    """Stop the running Local AI Gateway daemon."""
    console = get_console()
    ctx = RuntimeContext()
    server = GatewayServer(ctx)
    res = server.stop()
    if res["status"] == "not_running":
        console.print("[yellow]Gateway is not running.[/yellow]")
    else:
        console.print(f"[bold green]Stopped gateway process (PID: {res.get('pid')}).[/bold green]")


@gateway_app.command("status")
def status_gateway() -> None:
    """Check the operational status of the Local AI Gateway."""
    console = get_console()
    ctx = RuntimeContext()
    server = GatewayServer(ctx)
    info = server.status()

    if not info.get("running"):
        console.print("[yellow]Gateway is stopped.[/yellow]")
        return

    table = Table(title="Local AI Gateway Status", box=None)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Status", "[bold green]Online[/bold green]")
    table.add_row("PID", str(info.get("pid")))
    table.add_row("Base URL", f"http://{info.get('host')}:{info.get('port')}/v1")
    health = info.get("health", {})
    if health:
        table.add_row("Uptime (seconds)", str(health.get("uptime_seconds", "N/A")))
        table.add_row("Total Credentials", str(health.get("total_credentials", 0)))
        table.add_row("Enabled Credentials", str(health.get("enabled_credentials", 0)))

    console.print(table)


@gateway_app.command("token")
def token_gateway(
    regenerate: bool = typer.Option(
        False, "--regenerate", "-r", help="Regenerate a new gateway token."
    ),
) -> None:
    """Display or regenerate the Local AI Gateway authentication token."""
    console = get_console()
    ctx = RuntimeContext()
    auth = GatewayAuth(ctx.paths)

    if regenerate:
        token = auth.regenerate_token()
        console.print(
            f"[bold green]New Gateway Token generated:[/bold green] [yellow]{token}[/yellow]"
        )
    else:
        token = auth.get_or_create_token()
        console.print(f"[bold cyan]Active Gateway Token:[/bold cyan] [yellow]{token}[/yellow]")


@gateway_app.command("logs")
def logs_gateway(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of log lines to view."),
) -> None:
    """View recent gateway server log entries."""
    console = get_console()
    ctx = RuntimeContext()
    log_path = ctx.paths.gateway_log_path

    if not log_path.exists():
        console.print("[yellow]No gateway log file found.[/yellow]")
        return

    try:
        content = log_path.read_text(encoding="utf-8").splitlines()
        tail = content[-lines:] if len(content) > lines else content
        console.print("\n".join(tail))
    except OSError as exc:
        console.print(f"[red]Failed to read log file: {exc}[/red]")
