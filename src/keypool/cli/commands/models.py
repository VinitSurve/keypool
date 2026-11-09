"""CLI commands for searching and inspecting provider models (Section 10)."""

from __future__ import annotations

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.core.model_catalog import ModelCatalogManager
from keypool.integrations.json_output import emit_json

models_app = typer.Typer(
    name="models",
    help="Discover, search, and inspect provider AI models (Section 10).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@models_app.callback()
def models_index(
    ctx_typer: typer.Context,
    provider: str | None = typer.Option(None, "--provider", "-p", help="Filter by provider."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List available provider models in the catalog."""
    if ctx_typer.invoked_subcommand is not None:
        return

    ctx = RuntimeContext()
    mcm = ModelCatalogManager(ctx.db)
    models = mcm.list_models(provider=provider)

    if json_output:
        emit_json("models", {"models": [m.to_public_dict() for m in models]})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title="[bold cyan]AI Provider Model Catalog[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Model ID", style="bold white")
    table.add_column("Display Name", style="white")
    table.add_column("Context", justify="right", style="green")
    table.add_column("Max Out", justify="right", style="blue")
    table.add_column("Price / 1M (In/Out)", justify="right", style="magenta")

    for m in models:
        p_str = (
            f"${m.input_price_per_1m:.2f} / ${m.output_price_per_1m:.2f}"
            if m.input_price_per_1m
            else "-"
        )
        ctx_str = f"{m.context_window // 1000}k" if m.context_window else "-"
        out_str = f"{m.max_output_tokens // 1000}k" if m.max_output_tokens else "-"
        table.add_row(m.provider, m.model_id, m.display_name, ctx_str, out_str, p_str)

    console.print(table)


@models_app.command("search")
def cmd_models_search(
    query: str = typer.Argument(..., help="Search term (model name or provider)."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Search models across providers."""
    ctx = RuntimeContext()
    mcm = ModelCatalogManager(ctx.db)
    models = mcm.search_models(query)

    if json_output:
        emit_json("models.search", {"query": query, "models": [m.to_public_dict() for m in models]})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title=f"[bold cyan]Model Search Results for '{query}'[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Model ID", style="bold white")
    table.add_column("Display Name", style="white")
    table.add_column("Context", justify="right", style="green")
    table.add_column("Price / 1M In", justify="right")

    for m in models:
        ctx_str = f"{m.context_window // 1000}k" if m.context_window else "-"
        p_str = f"${m.input_price_per_1m:.2f}" if m.input_price_per_1m else "-"
        table.add_row(m.provider, m.model_id, m.display_name, ctx_str, p_str)

    console.print(table)


@models_app.command("inspect")
def cmd_models_inspect(
    model: str = typer.Argument(
        ..., help="Model identifier (e.g. 'gpt-4o-mini', 'claude-3-5-sonnet-latest')."
    ),
    provider: str = typer.Option(..., "--provider", "-p", help="Provider name."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Inspect detailed model specifications and pricing."""
    ctx = RuntimeContext()
    mcm = ModelCatalogManager(ctx.db)
    info = mcm.get_model(provider=provider, model_id=model)

    if info is None:
        console = get_console(color_mode=ctx.settings.color)
        console.print(f"[red]Model '{model}' for provider '{provider}' not found in catalog.[/red]")
        raise typer.Exit(1)

    if json_output:
        emit_json("models.inspect", info.to_public_dict())
        return

    console = get_console(color_mode=ctx.settings.color)
    grid = Table(show_header=False, box=None)
    grid.add_column("Field", style="bold cyan", width=22)
    grid.add_column("Value")

    grid.add_row("Provider", info.provider)
    grid.add_row("Model ID", f"[bold white]{info.model_id}[/bold white]")
    grid.add_row("Display Name", info.display_name)
    grid.add_row(
        "Context Window", f"{info.context_window:,} tokens" if info.context_window else "Unknown"
    )
    grid.add_row(
        "Max Output Tokens",
        f"{info.max_output_tokens:,} tokens" if info.max_output_tokens else "Unknown",
    )
    grid.add_row("Modalities", ", ".join(info.modalities))
    grid.add_row(
        "Input Price (1M)",
        f"${info.input_price_per_1m:.2f}" if info.input_price_per_1m else "Unknown",
    )
    grid.add_row(
        "Output Price (1M)",
        f"${info.output_price_per_1m:.2f}" if info.output_price_per_1m else "Unknown",
    )

    console.print(
        Panel(
            grid,
            title=f"[bold cyan]Model Specification — {info.display_name}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
