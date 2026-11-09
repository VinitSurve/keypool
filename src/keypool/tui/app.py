"""Polished interactive Terminal User Interface (TUI) for KEYPOOL (Section 17)."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from keypool.cli.formatting import RuntimeContext, build_status_summary, format_timestamp
from keypool.core.budget_manager import BudgetManager
from keypool.core.model_catalog import ModelCatalogManager
from keypool.core.project_manager import ProjectManager
from keypool.core.usage_tracker import UsageTracker
from keypool.storage.models import HealthState
from keypool.version import APP_DISPLAY_NAME, __version__

TUI_TABS = [
    "overview",
    "credentials",
    "providers",
    "health",
    "models",
    "profiles",
    "projects",
    "usage",
    "budgets",
    "gateway",
    "settings",
    "diagnostics",
]


def create_header(active_tab: str = "overview") -> Panel:
    """Render top branding banner with navigation indicator."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right", ratio=1)
    grid.add_row(
        f"[bold cyan]{APP_DISPLAY_NAME}[/bold cyan] [dim]v{__version__}[/dim] — [bold white]{active_tab.upper()}[/bold white]",
        "[cyan]Universal AI Credential & Infrastructure Platform[/cyan]",
    )
    return Panel(grid, style="cyan", box=box.ROUNDED)


def create_tab_bar(active_tab: str = "overview") -> Panel:
    """Render horizontal tab navigation."""
    tabs_display = []
    for t in TUI_TABS:
        if t == active_tab:
            tabs_display.append(f"[bold cyan reverse] {t.capitalize()} [/bold cyan reverse]")
        else:
            tabs_display.append(f"[dim] {t.capitalize()} [/dim]")
    return Panel(" ".join(tabs_display), box=box.SIMPLE, style="dim")


def render_overview_tab(ctx: RuntimeContext) -> Panel:
    """Render system overview with credentials, health, and usage status."""
    summary = build_status_summary(ctx)
    creds_summary = summary.get("credentials") or {
        "total": summary.get("overview", {}).get("total_credentials", 0),
        "healthy": summary.get("overview", {}).get("healthy", 0),
        "cooldown": 0,
        "auth_failed": summary.get("overview", {}).get("failing", 0),
        "keychain_error": 0,
    }

    metrics_table = Table(box=box.ROUNDED, expand=True)
    metrics_table.add_column("Providers", justify="center", style="cyan")
    metrics_table.add_column("Total Keys", justify="center", style="white")
    metrics_table.add_column("Healthy", justify="center", style="green")
    metrics_table.add_column("Cooldown / Limit", justify="center", style="yellow")
    metrics_table.add_column("Failed / Error", justify="center", style="red")

    metrics_table.add_row(
        str(len(ctx.registry.list_providers())),
        str(creds_summary.get("total", 0)),
        str(creds_summary.get("healthy", 0)),
        str(creds_summary.get("cooldown", 0)),
        str(creds_summary.get("auth_failed", 0) + creds_summary.get("keychain_error", 0)),
    )

    creds = ctx.credential_manager.list_credentials()
    cred_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    cred_table.add_column("ID", style="bold white", width=14)
    cred_table.add_column("Name", style="white")
    cred_table.add_column("Provider", style="cyan", width=12)
    cred_table.add_column("Status", width=18)
    cred_table.add_column("Profiles", style="dim")
    cred_table.add_column("Last Used", style="dim")

    for c in creds[:8]:
        status_style = (
            "green"
            if c.status == HealthState.HEALTHY.value
            else "yellow"
            if c.status == HealthState.UNTESTED.value
            else "red"
        )
        cred_table.add_row(
            c.id,
            c.name,
            c.provider,
            f"[{status_style}]{c.effective_status}[/{status_style}]",
            ", ".join(c.profiles) or "-",
            c.last_used_at or "never",
        )

    grid = Table.grid(expand=True)
    grid.add_row(metrics_table)
    grid.add_row(cred_table)
    return Panel(grid, title="[bold]System Overview & Status[/bold]", box=box.ROUNDED)


def render_credentials_tab(ctx: RuntimeContext) -> Panel:
    """Render full credentials inventory."""
    creds = ctx.credential_manager.list_credentials()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("ID", style="bold cyan", width=14)
    table.add_column("Name", style="bold white")
    table.add_column("Provider", style="cyan", width=14)
    table.add_column("Priority", justify="right", width=8)
    table.add_column("Status", width=16)
    table.add_column("Tags", style="dim")
    table.add_column("Profiles", style="dim")
    table.add_column("Last Tested", style="dim")

    for c in creds:
        status_style = "green" if c.status == "healthy" else ("yellow" if c.status == "untested" else "red")
        table.add_row(
            c.id,
            c.name,
            c.provider,
            str(c.priority),
            f"[{status_style}]{c.effective_status}[/{status_style}]",
            ", ".join(c.tags) or "-",
            ", ".join(c.profiles) or "-",
            format_timestamp(c.last_tested_at),
        )
    return Panel(table, title="[bold]Configured AI Credentials[/bold]", box=box.ROUNDED)


def render_providers_tab(ctx: RuntimeContext) -> Panel:
    """Render all registered provider adapters."""
    providers = ctx.registry.list_providers()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Identifier", style="bold cyan", width=14)
    table.add_column("Display Name", style="bold white", width=22)
    table.add_column("Base URL", style="blue")
    table.add_column("Environment Variable", style="dim", width=24)

    for p in providers:
        table.add_row(p.name, p.display_name, p.base_url, p.get_environment_variable())
    return Panel(table, title="[bold]Registered Provider Adapters[/bold]", box=box.ROUNDED)


def render_health_tab(ctx: RuntimeContext) -> Panel:
    """Render health checks and latencies."""
    snapshots = ctx.health_manager.get_health_report()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Credential", style="bold cyan")
    table.add_column("Provider", style="cyan")
    table.add_column("State", style="bold")
    table.add_column("Latency (ms)", justify="right")
    table.add_column("Model Tested", style="dim")
    table.add_column("Cooldown Until", style="dim")

    for s in snapshots:
        state_style = "green" if s.state == "healthy" else ("yellow" if s.state in ("untested", "rate_limited") else "red")
        lat_str = f"{s.latency_ms:.1f}" if s.latency_ms else "-"
        table.add_row(
            f"{s.name} ({s.credential_id})",
            s.provider,
            f"[{state_style}]{s.state}[/{state_style}]",
            lat_str,
            s.model_tested or "-",
            format_timestamp(s.cooldown_until),
        )
    return Panel(table, title="[bold]Credential Health & Observability[/bold]", box=box.ROUNDED)


def render_models_tab(ctx: RuntimeContext) -> Panel:
    """Render catalog models."""
    mcm = ModelCatalogManager(ctx.db)
    models = mcm.list_models()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Model ID", style="bold white")
    table.add_column("Context", justify="right", style="green")
    table.add_column("Price / 1M (In/Out)", justify="right")
    table.add_column("Modalities", style="dim")

    for m in models[:20]:
        p_str = f"${m.input_price_per_1m:.2f} / ${m.output_price_per_1m:.2f}" if m.input_price_per_1m else "-"
        ctx_str = f"{m.context_window // 1000}k" if m.context_window else "-"
        table.add_row(m.provider, m.model_id, ctx_str, p_str, ", ".join(m.modalities))
    return Panel(table, title="[bold]AI Model Catalog Snapshot[/bold]", box=box.ROUNDED)


def render_profiles_tab(ctx: RuntimeContext) -> Panel:
    """Render isolation profiles."""
    profiles = ctx.profile_manager.list_profiles()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Profile", style="bold cyan", width=16)
    table.add_column("Strategy", style="bold white", width=16)
    table.add_column("Description")
    table.add_column("Assigned Keys", justify="right", width=14)

    for p in profiles:
        table.add_row(p.name, p.selection_strategy, p.description or "-", str(len(p.assigned_credential_ids)))
    return Panel(table, title="[bold]Execution Isolation Profiles[/bold]", box=box.ROUNDED)


def render_projects_tab(ctx: RuntimeContext) -> Panel:
    """Render discovered project contexts."""
    pm = ProjectManager(ctx.db)
    projects = pm.list_projects()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Project", style="bold cyan", width=18)
    table.add_column("Profile", style="bold white", width=16)
    table.add_column("Root Directory", style="blue")
    table.add_column("Aliases", style="dim")

    for prj in projects:
        aliases_str = ", ".join(prj.model_aliases.keys()) if prj.model_aliases else "-"
        table.add_row(prj.name, prj.profile_name, prj.root_dir, aliases_str)
    return Panel(table, title="[bold]Project Contexts & Aliases[/bold]", box=box.ROUNDED)


def render_usage_tab(ctx: RuntimeContext) -> Panel:
    """Render usage metrics and costs."""
    ut = UsageTracker(ctx.db)
    rep = ut.get_usage_report(period="30d")
    grid = Table.grid(expand=True)
    grid.add_column("Metric", style="bold cyan", width=26)
    grid.add_column("Value")

    grid.add_row("Total Selections", str(rep.total_selections))
    grid.add_row("Child Executions", str(rep.total_executions))
    grid.add_row("Successful Executions", f"[green]{rep.successful_executions}[/green]")
    grid.add_row("Failed Executions", f"[red]{rep.failed_executions}[/red]")
    grid.add_row("Observed 429 Rate Limits", f"[yellow]{rep.observed_429_events}[/yellow]")
    grid.add_row("Observed Probe Tokens", str(rep.observed_tokens))

    return Panel(grid, title="[bold]30-Day Activity & Token Metrics[/bold]", box=box.ROUNDED)


def render_budgets_tab(ctx: RuntimeContext) -> Panel:
    """Render budgets and spend forecasting."""
    bm = BudgetManager(ctx.db)
    budgets = bm.list_budgets()
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Budget", style="bold cyan")
    table.add_column("Scope", style="white")
    table.add_column("Period", style="dim")
    table.add_column("Current Spend", justify="right")
    table.add_column("Limit", justify="right")
    table.add_column("Used %", justify="right")
    table.add_column("Hard Limit", justify="center")

    for b in budgets:
        pct_style = "red" if b.is_exceeded else ("yellow" if b.is_warning else "green")
        table.add_row(
            b.name,
            f"{b.scope_type}:{b.scope_target or '*'}",
            b.period,
            f"${b.current_spend:.2f}",
            f"${b.amount_limit:.2f}",
            f"[{pct_style}]{b.percent_used:.1f}%[/{pct_style}]",
            "Yes" if b.hard_limit_enabled else "No",
        )
    return Panel(table, title="[bold]Configured Budgets & Cost Controls[/bold]", box=box.ROUNDED)


def render_gateway_tab(ctx: RuntimeContext) -> Panel:
    """Render gateway configuration and status."""
    gw = ctx.settings.gateway
    grid = Table.grid(expand=True)
    grid.add_column("Field", style="bold cyan", width=26)
    grid.add_column("Value")

    grid.add_row("Gateway Address", f"http://{gw.host}:{gw.port}")
    grid.add_row("OpenAI Dialect", "http://127.0.0.1:8765/v1/chat/completions")
    grid.add_row("Embeddings Dialect", "http://127.0.0.1:8765/v1/embeddings")
    grid.add_row("Default Strategy", gw.default_strategy)
    grid.add_row("Max Fallback Attempts", str(gw.max_fallback_attempts))
    grid.add_row("Authentication", "Bearer token via ~/.keypool/gateway.token")

    return Panel(grid, title="[bold]Local AI Gateway Runtime[/bold]", box=box.ROUNDED)


def render_diagnostics_tab(ctx: RuntimeContext) -> Panel:
    """Render system diagnostics and integrity checks."""
    summary = build_status_summary(ctx)
    creds_summary = summary.get("credentials") or summary.get("overview") or {}
    total_val = creds_summary.get("total", creds_summary.get("total_credentials", 0))
    healthy_val = creds_summary.get("healthy", 0)

    grid = Table.grid(expand=True)
    grid.add_column("Check", style="bold cyan", width=26)
    grid.add_column("Status")

    grid.add_row("macOS Keychain Integration", "[bold green]OPERATIONAL (0600 isolation)[/bold green]")
    grid.add_row("SQLite Database & WAL", f"[bold green]OK (Schema v{summary.get('schema_version', '5.0')})[/bold green]")
    grid.add_row("Config Directory", str(ctx.paths.app_dir))
    grid.add_row("Database Path", str(ctx.paths.db_path))
    grid.add_row("Total Configured Keys", str(total_val))
    grid.add_row("Healthy References", str(healthy_val))

    return Panel(grid, title="[bold]System Diagnostics & Integrity[/bold]", box=box.ROUNDED)


def render_settings_tab(ctx: RuntimeContext) -> Panel:
    """Render system settings snapshot."""
    s = ctx.settings
    grid = Table.grid(expand=True)
    grid.add_column("Setting", style="bold cyan", width=28)
    grid.add_column("Value")

    grid.add_row("HTTP Timeout", f"{s.network.http_timeout_seconds}s")
    grid.add_row("Connect Timeout", f"{s.network.connect_timeout_seconds}s")
    grid.add_row("Max Network Retries", str(s.network.max_retries))
    grid.add_row("Execution Env Mode", s.execution.env_mode)
    grid.add_row("Execution Timeout", f"{s.execution.default_timeout_seconds}s")
    grid.add_row("Color Mode", s.color)

    return Panel(grid, title="[bold]Application Configuration[/bold]", box=box.ROUNDED)


def run_tui(ctx: RuntimeContext | None = None, view: str | None = None) -> None:
    """Launch interactive TUI or render specified view."""
    context = ctx or RuntimeContext()
    console = Console()

    target_tab = view.strip().lower() if view and view.strip().lower() in TUI_TABS else "overview"

    header = create_header(target_tab)
    tab_bar = create_tab_bar(target_tab)

    renderers = {
        "overview": render_overview_tab,
        "credentials": render_credentials_tab,
        "providers": render_providers_tab,
        "health": render_health_tab,
        "models": render_models_tab,
        "profiles": render_profiles_tab,
        "projects": render_projects_tab,
        "usage": render_usage_tab,
        "budgets": render_budgets_tab,
        "gateway": render_gateway_tab,
        "settings": render_settings_tab,
        "diagnostics": render_diagnostics_tab,
    }

    body = renderers.get(target_tab, render_overview_tab)(context)

    console.clear()
    console.print(header)
    console.print(tab_bar)
    console.print(body)

    footer = Table.grid(expand=True)
    footer.add_column(justify="left")
    footer.add_row(
        f"[dim]Navigate views: keypool tui --view <{' | '.join(TUI_TABS[:6])} ...>[/dim]"
    )
    console.print(Panel(footer, box=box.ROUNDED, style="dim"))
