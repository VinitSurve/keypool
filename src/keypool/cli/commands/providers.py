"""CLI commands for inspecting registered AI provider adapters."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.integrations.json_output import emit_json
from keypool.storage.models import HealthState

providers_app = typer.Typer(
    name="providers",
    help="Inspect registered AI provider adapters (Groq, Google Gemini).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@providers_app.callback()
def providers_index(
    ctx_typer: typer.Context,
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON metadata.",
    ),
) -> None:
    """List all registered AI provider adapters and their configured credential counts."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    adapters = ctx.registry.list_providers()
    all_creds = ctx.credential_manager.list_credentials()

    provider_items = []
    for adapter in adapters:
        p_creds = [c for c in all_creds if c.provider == adapter.name]
        enabled_count = sum(1 for c in p_creds if c.enabled)
        healthy_count = sum(
            1 for c in p_creds if c.enabled and c.status == HealthState.HEALTHY.value
        )
        provider_items.append(
            {
                "name": adapter.name,
                "display_name": adapter.display_name,
                "env_var": adapter.get_environment_variable(),
                "base_url": adapter.base_url,
                "docs_url": adapter.docs_url,
                "default_test_model": adapter.default_test_model,
                "total_credentials": len(p_creds),
                "enabled_credentials": enabled_count,
                "healthy_credentials": healthy_count,
            }
        )

    if json_output:
        emit_json("providers", {"providers": provider_items})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]Registered AI Provider Adapters[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("ID", style="bold white")
    table.add_column("Display Name", style="white")
    table.add_column("Env Variable", style="cyan")
    table.add_column("Credentials", justify="right")
    table.add_column("Enabled", justify="right", style="blue")
    table.add_column("Healthy", justify="right", style="green")
    table.add_column("Base Endpoint", style="dim")

    for item in provider_items:
        table.add_row(
            str(item["name"]),
            str(item["display_name"]),
            str(item["env_var"]),
            str(item["total_credentials"]),
            str(item["enabled_credentials"]),
            str(item["healthy_credentials"]),
            str(item["base_url"]),
        )
    console.print(table)


@providers_app.command("show")
def providers_show(
    provider: str = typer.Argument(..., help="Provider identifier ('groq' or 'gemini')."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON metadata.",
    ),
) -> None:
    """Display adapter configuration, endpoints, and credentials for a specific provider."""
    ctx = RuntimeContext()
    adapter = ctx.registry.get(provider)
    creds = ctx.credential_manager.list_credentials(provider=adapter.name)

    data = {
        "name": adapter.name,
        "display_name": adapter.display_name,
        "env_var": adapter.get_environment_variable(),
        "base_url": adapter.base_url,
        "docs_url": adapter.docs_url,
        "rate_limits_docs_url": adapter.rate_limits_docs_url,
        "models_docs_url": adapter.models_docs_url,
        "default_test_model": adapter.default_test_model,
        "credential_count": len(creds),
        "credentials": [c.to_public_dict() for c in creds],
    }

    if json_output:
        emit_json("providers.show", data)
        return

    console = get_console(color_mode=ctx.settings.color)
    grid = Table(show_header=False, box=None, padding=(0, 2))
    grid.add_column("Field", style="bold cyan", width=22)
    grid.add_column("Value")

    grid.add_row("Provider ID", f"[bold white]{adapter.name}[/bold white]")
    grid.add_row("Display Name", adapter.display_name)
    grid.add_row(
        "Environment Variable", f"[bold green]{adapter.get_environment_variable()}[/bold green]"
    )
    grid.add_row("API Base URL", adapter.base_url)
    grid.add_row("Default Test Model", adapter.default_test_model)
    grid.add_row("Documentation", adapter.docs_url)
    grid.add_row("Rate Limits Docs", adapter.rate_limits_docs_url)
    grid.add_row("Models Docs", adapter.models_docs_url)
    grid.add_row("Configured Keys", str(len(creds)))

    console.print(
        Panel(
            grid,
            title=f"[bold cyan]Provider Adapter — {adapter.display_name}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            expand=False,
        )
    )
