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


@models_app.command("compare")
def cmd_models_compare(
    model_a: str = typer.Argument(..., help="First model ID to compare."),
    model_b: str = typer.Argument(..., help="Second model ID to compare."),
    provider_a: str | None = typer.Option(None, "--provider-a", help="Provider for first model."),
    provider_b: str | None = typer.Option(None, "--provider-b", help="Provider for second model."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Compare specifications, context limits, and pricing between two models."""
    ctx = RuntimeContext()
    mcm = ModelCatalogManager(ctx.db)

    try:
        res = mcm.compare_models(
            model_a=model_a,
            model_b=model_b,
            provider_a=provider_a,
            provider_b=provider_b,
        )
    except ValueError as exc:
        console = get_console(color_mode=ctx.settings.color)
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        emit_json("models.compare", res)
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(
        title=f"[bold cyan]Model Comparison: {model_a} vs {model_b}[/bold cyan]",
        box=box.ROUNDED,
    )
    table.add_column("Specification", style="bold cyan")
    table.add_column(f"{model_a}", style="white")
    table.add_column(f"{model_b}", style="white")

    ma = res["model_a"]
    mb = res["model_b"]

    table.add_row("Provider", ma["provider"], mb["provider"])
    table.add_row("Display Name", ma["display_name"], mb["display_name"])
    table.add_row(
        "Context Window",
        f"{ma['context_window']:,} tokens" if ma["context_window"] else "-",
        f"{mb['context_window']:,} tokens" if mb["context_window"] else "-",
    )
    table.add_row(
        "Max Output",
        f"{ma['max_output_tokens']:,} tokens" if ma["max_output_tokens"] else "-",
        f"{mb['max_output_tokens']:,} tokens" if mb["max_output_tokens"] else "-",
    )
    table.add_row(
        "Input Price / 1M",
        f"${ma['input_price_per_1m']:.2f}" if ma["input_price_per_1m"] is not None else "-",
        f"${mb['input_price_per_1m']:.2f}" if mb["input_price_per_1m"] is not None else "-",
    )
    table.add_row(
        "Output Price / 1M",
        f"${ma['output_price_per_1m']:.2f}" if ma["output_price_per_1m"] is not None else "-",
        f"${mb['output_price_per_1m']:.2f}" if mb["output_price_per_1m"] is not None else "-",
    )
    table.add_row(
        "Modalities",
        ", ".join(ma["modalities"]),
        ", ".join(mb["modalities"]),
    )

    console.print(table)


@models_app.command("refresh")
def cmd_models_refresh(
    provider: str = typer.Argument(..., help="Provider whose models should be refreshed."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Discover live models from a provider using an active credential and update the local catalog."""
    ctx = RuntimeContext()
    console = get_console(color_mode=ctx.settings.color)

    try:
        selected = ctx.selection_engine.select_credential(provider=provider, record_usage=False)
        secret = ctx.credential_manager.retrieve_secret(selected.metadata.id)
    except Exception as exc:
        console.print(f"[red]Unable to retrieve active credential for provider '{provider}': {exc}[/red]")
        raise typer.Exit(1) from exc

    mcm = ModelCatalogManager(ctx.db)
    refreshed = mcm.refresh_provider_models(provider, secret, ctx.registry)

    if json_output:
        emit_json("models.refresh", {"provider": provider, "count": len(refreshed), "models": [m.to_public_dict() for m in refreshed]})
        return

    console.print(f"[bold green]✓ Refreshed {len(refreshed)} models for '{provider}' in local catalog.[/bold green]")


alias_app = typer.Typer(
    name="alias",
    help="Manage model routing aliases (shortcuts mapping to provider/model endpoints).",
    no_args_is_help=True,
)
models_app.add_typer(alias_app, name="alias")
models_app.add_typer(alias_app, name="aliases")


@alias_app.command("list")
def cmd_alias_list(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List all configured model routing aliases."""
    from keypool.core.alias_manager import ModelAliasManager

    ctx = RuntimeContext()
    mgr = ModelAliasManager(ctx.db)
    aliases = mgr.list_aliases()

    if json_output:
        emit_json("models.alias.list", {"aliases": aliases})
        return

    console = get_console(color_mode=ctx.settings.color)
    table = Table(title="Model Routing Aliases", box=box.ROUNDED)
    table.add_column("Alias", style="bold cyan")
    table.add_column("Target Target", style="bold white")
    table.add_column("Description", style="dim")

    for a in aliases:
        table.add_row(a["alias"], a["target"], a["description"])

    console.print(table)


@alias_app.command("set")
def cmd_alias_set(
    alias: str = typer.Argument(..., help="Alias name (e.g. 'coding', 'fast', 'smart')."),
    target: str = typer.Argument(
        ..., help="Target model or provider/model (e.g. 'groq/llama-3.3-70b-versatile')."
    ),
    description: str | None = typer.Option(
        None, "--description", "-d", help="Optional description."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Create or update a model alias shortcut."""
    from keypool.core.alias_manager import ModelAliasManager

    ctx = RuntimeContext()
    mgr = ModelAliasManager(ctx.db)
    res = mgr.set_alias(alias=alias, target=target, description=description)

    if json_output:
        emit_json("models.alias.set", res)
        return

    console = get_console(color_mode=ctx.settings.color)
    console.print(f"[bold green]Alias '{alias}' mapped to '{target}'.[/bold green]")


@alias_app.command("remove")
def cmd_alias_remove(
    alias: str = typer.Argument(..., help="Alias name to remove."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Delete a model alias shortcut."""
    from keypool.core.alias_manager import ModelAliasManager

    ctx = RuntimeContext()
    mgr = ModelAliasManager(ctx.db)
    removed = mgr.remove_alias(alias=alias)

    if json_output:
        emit_json("models.alias.remove", {"alias": alias, "removed": removed})
        return

    console = get_console(color_mode=ctx.settings.color)
    if removed:
        console.print(f"[bold green]Alias '{alias}' removed.[/bold green]")
    else:
        console.print(f"[yellow]Alias '{alias}' not found.[/yellow]")
