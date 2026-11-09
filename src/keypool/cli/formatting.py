"""Rich terminal styling, dashboard rendering, and runtime service container."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from keypool.config.settings import (
    AppPaths,
    KeypoolSettings,
    configure_logging,
    load_settings,
)
from keypool.core.credential_manager import CredentialManager, CredentialMetadata
from keypool.core.execution_manager import ExecutionManager
from keypool.core.health_manager import HealthManager
from keypool.core.profile_manager import ProfileManager
from keypool.core.selection_engine import SelectionEngine
from keypool.core.usage_tracker import UsageTracker
from keypool.providers.registry import ProviderRegistry
from keypool.security.redaction import redact_text
from keypool.storage.database import DatabaseManager
from keypool.storage.keychain import MacOSKeychainStore, SecretStore
from keypool.storage.models import HealthState

KEYPOOL_ASCII_BANNER = (
    "  ██╗  ██╗███████╗██╗   ██╗██████╗  ██████╗  ██████╗ \n"
    "  ██║ ██╔╝██╔════╝╚██╗ ██╔╝██╔══██╗██╔═══██╗██╔═══██╗\n"
    "  █████╔╝ █████╗   ╚████╔╝ ██████╔╝██║   ██║██║   ██║\n"
    "  ██╔═██╗ ██╔══╝    ╚██╔╝  ██╔═══╝ ██║   ██║██║   ██║\n"
    "  ██║  ██╗███████╗   ██║   ██║     ╚██████╔╝╚██████╔╝\n"
    "  ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝      ╚═════╝  ╚═════╝ \n"
    "\n"
    "            LOCAL AI CREDENTIAL MANAGER              "
)


class RuntimeOverrides:
    """Optional test hooks for dependency injection during unit/CLI tests."""

    secret_store: SecretStore | None = None
    http_client: httpx.Client | None = None
    paths: AppPaths | None = None


_OVERRIDES = RuntimeOverrides()


def configure_test_overrides(
    *,
    secret_store: SecretStore | None = None,
    http_client: httpx.Client | None = None,
    paths: AppPaths | None = None,
) -> None:
    """Configure test-time dependency overrides (reset by passing None)."""
    _OVERRIDES.secret_store = secret_store
    _OVERRIDES.http_client = http_client
    _OVERRIDES.paths = paths


class RuntimeContext:
    """Wires application services together for a CLI invocation."""

    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or _OVERRIDES.paths or AppPaths()
        self.settings: KeypoolSettings = load_settings(self.paths)
        self.logger = configure_logging(self.paths, level=self.settings.log_level)
        self.db = DatabaseManager(self.paths.db_path, auto_migrate=True)
        self.secret_store: SecretStore = _OVERRIDES.secret_store or MacOSKeychainStore(
            service_name=self.settings.keychain_service
        )
        self.registry = ProviderRegistry(
            settings=self.settings,
            http_client=_OVERRIDES.http_client,
        )
        self.usage_tracker = UsageTracker(self.db)
        self.credential_manager = CredentialManager(
            db=self.db,
            secret_store=self.secret_store,
            provider_registry=self.registry,
            usage_tracker=self.usage_tracker,
        )
        self.profile_manager = ProfileManager(self.db)
        self.health_manager = HealthManager(
            db=self.db,
            secret_store=self.secret_store,
            provider_registry=self.registry,
            credential_manager=self.credential_manager,
            settings=self.settings,
            usage_tracker=self.usage_tracker,
        )
        self.selection_engine = SelectionEngine(
            db=self.db,
            credential_manager=self.credential_manager,
            provider_registry=self.registry,
            health_manager=self.health_manager,
            usage_tracker=self.usage_tracker,
        )
        self.execution_manager = ExecutionManager(
            db=self.db,
            credential_manager=self.credential_manager,
            selection_engine=self.selection_engine,
            provider_registry=self.registry,
            settings=self.settings,
            usage_tracker=self.usage_tracker,
        )


def get_console(*, stderr: bool = False, color_mode: str = "auto") -> Console:
    """Create a Rich Console respecting terminal color preferences."""
    no_color_env = bool(os.environ.get("NO_COLOR", "").strip())
    no_color = no_color_env or (color_mode == "never")
    force_terminal: bool | None = True if color_mode == "always" and not no_color else None
    return Console(stderr=stderr, no_color=no_color, force_terminal=force_terminal, highlight=False)


def format_health_badge(state_str: str, *, enabled: bool = True) -> Text:
    """Render a color-coded Rich Text badge for a credential health state."""
    if not enabled or state_str == HealthState.DISABLED.value:
        return Text("○ Disabled", style="dim")

    styles: dict[str, tuple[str, str, str]] = {
        HealthState.HEALTHY.value: ("●", "Healthy", "bold green"),
        HealthState.UNTESTED.value: ("◐", "Untested", "yellow"),
        HealthState.AUTH_FAILED.value: ("✖", "Authentication failed", "bold red"),
        HealthState.PERMISSION_DENIED.value: ("✖", "Permission denied", "bold red"),
        HealthState.RATE_LIMITED.value: ("▲", "Rate limited", "bold yellow"),
        HealthState.QUOTA_Exhausted.value: ("✖", "Quota exhausted", "bold red"),
        HealthState.MODEL_UNAVAILABLE.value: ("▲", "Model unavailable", "yellow"),
        HealthState.PROVIDER_UNAVAILABLE.value: ("▲", "Provider unavailable", "yellow"),
        HealthState.NETWORK_ERROR.value: ("▲", "Network error", "yellow"),
        HealthState.KEYCHAIN_ERROR.value: ("✖", "Keychain error", "bold red"),
        HealthState.UNKNOWN.value: ("?", "Unknown", "dim"),
    }
    icon, label, style = styles.get(state_str, ("?", state_str, "dim"))
    return Text(f"{icon} {label}", style=style)


def format_enabled_badge(enabled: bool) -> Text:
    """Render an Enabled / Disabled badge."""
    if enabled:
        return Text("Enabled", style="green")
    return Text("Disabled", style="dim")


def format_timestamp(iso_str: str | None) -> str:
    """Format an ISO 8601 timestamp into a concise human-readable string."""
    if not iso_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso_str


def format_time_hhmm(iso_str: str | None) -> str:
    """Format an ISO 8601 timestamp into HH:MM for the recent activity feed."""
    if not iso_str:
        return "--:--"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M")
    except ValueError:
        return iso_str[:5]


def build_status_summary(ctx: RuntimeContext) -> dict[str, Any]:
    """Compute real database statistics for the dashboard and `keypool status`."""
    creds = ctx.credential_manager.list_credentials()
    total = len(creds)
    enabled_count = sum(1 for c in creds if c.enabled)
    disabled_count = sum(1 for c in creds if not c.enabled)
    healthy_count = sum(1 for c in creds if c.enabled and c.status == HealthState.HEALTHY.value)
    untested_count = sum(1 for c in creds if c.enabled and c.status == HealthState.UNTESTED.value)
    failing_count = sum(
        1
        for c in creds
        if c.enabled
        and c.status
        not in (HealthState.HEALTHY.value, HealthState.UNTESTED.value, HealthState.DISABLED.value)
    )

    provider_counts: dict[str, int] = {}
    for adapter in ctx.registry.list_providers():
        provider_counts[adapter.name] = sum(1 for c in creds if c.provider == adapter.name)

    recent_activity = ctx.usage_tracker.get_recent_activity(limit=6)
    profiles = ctx.profile_manager.list_profiles()

    return {
        "overview": {
            "total_credentials": total,
            "enabled": enabled_count,
            "healthy": healthy_count,
            "untested": untested_count,
            "failing": failing_count,
            "disabled": disabled_count,
        },
        "providers": provider_counts,
        "active_profile": ctx.settings.default_profile,
        "profiles": [
            {
                "name": p.name,
                "credential_count": len(p.assigned_credential_ids),
                "is_builtin": p.is_builtin,
            }
            for p in profiles
        ],
        "recent_activity": recent_activity,
    }


def render_dashboard(ctx: RuntimeContext, console: Console) -> None:
    """Render the main KEYPOOL Rich overview dashboard using live database state."""
    summary = build_status_summary(ctx)
    ov = summary["overview"]
    prov_counts = summary["providers"]
    recent = summary["recent_activity"]

    banner_panel = Panel(
        Text(KEYPOOL_ASCII_BANNER, style="bold cyan"),
        border_style="cyan",
        box=box.ROUNDED,
        expand=False,
        padding=(1, 1),
    )
    console.print()
    console.print(banner_panel)
    console.print()

    console.print("  [bold cyan]SYSTEM OVERVIEW[/bold cyan]\n")
    overview_table = Table(show_header=False, box=None, padding=(0, 3))
    overview_table.add_column("Metric", style="white", width=16)
    overview_table.add_column("Count", justify="right")

    overview_table.add_row(
        "  Credentials", f"[bold white]{ov['total_credentials']:02d}[/bold white]"
    )
    overview_table.add_row("  Enabled", f"[blue]{ov['enabled']:02d}[/blue]")
    overview_table.add_row("  Healthy", f"[bold green]{ov['healthy']:02d}[/bold green]")
    overview_table.add_row("  Untested", f"[yellow]{ov['untested']:02d}[/yellow]")
    if ov["failing"] > 0:
        overview_table.add_row("  Issues", f"[bold red]{ov['failing']:02d}[/bold red]")
    overview_table.add_row("  Disabled", f"[dim]{ov['disabled']:02d}[/dim]")
    console.print(overview_table)
    console.print()

    console.print("  [bold cyan]PROVIDERS[/bold cyan]\n")
    prov_table = Table(show_header=False, box=None, padding=(0, 3))
    prov_table.add_column("Provider", width=10)
    prov_table.add_column("Status")
    for prov_name, count in prov_counts.items():
        dot_style = "green" if count > 0 else "dim"
        label = "credential" if count == 1 else "credentials"
        prov_table.add_row(
            f"  [bold]{prov_name.upper()}[/bold]",
            f"[{dot_style}]●[/{dot_style}] {count} {label}",
        )
    console.print(prov_table)
    console.print()

    console.print("  [bold cyan]ACTIVE PROFILE[/bold cyan]\n")
    console.print(f"  [bold green]{summary['active_profile']}[/bold green]\n")

    console.print("  [bold cyan]RECENT ACTIVITY[/bold cyan]\n")
    if not recent:
        console.print(
            "  [dim]No activity recorded yet. Run 'keypool add groq' or 'keypool add gemini' to get started.[/dim]\n"
        )
    else:
        act_table = Table(show_header=False, box=None, padding=(0, 2))
        act_table.add_column("Time", style="dim", width=8)
        act_table.add_column("Summary")
        for item in recent[:5]:
            t_str = format_time_hhmm(item.get("occurred_at"))
            st = item.get("status", "info")
            color = (
                "green"
                if st == "success"
                else "red"
                if st == "failure"
                else "yellow"
                if st == "warning"
                else "white"
            )
            act_table.add_row(
                f"  {t_str}", f"[{color}]{redact_text(item.get('summary', ''))}[/{color}]"
            )
        console.print(act_table)
        console.print()

    console.print(
        "  [dim]Run[/dim] [bold cyan]'keypool --help'[/bold cyan] [dim]for available commands.[/dim]\n"
    )


def render_credentials_table(
    credentials: list[CredentialMetadata],
    console: Console,
    *,
    title: str = "Managed Credentials",
) -> None:
    """Render a styled Rich table of credentials."""
    if not credentials:
        console.print(
            Panel(
                "[yellow]No credentials configured.[/yellow]\n"
                "Add a credential with [bold cyan]keypool add groq[/bold cyan] or [bold cyan]keypool add gemini[/bold cyan].",
                title=title,
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        return

    table = Table(
        title=f"[bold cyan]{title}[/bold cyan]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
        show_lines=False,
    )
    table.add_column("ID", style="bold white", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Provider", style="cyan")
    table.add_column("State", no_wrap=True)
    table.add_column("Health", no_wrap=True)
    table.add_column("Profiles", style="blue")
    table.add_column("Last Tested", style="dim", no_wrap=True)
    table.add_column("Last Used", style="dim", no_wrap=True)

    for cred in credentials:
        profiles_str = ", ".join(cred.profiles) if cred.profiles else "—"
        table.add_row(
            cred.id,
            cred.name,
            cred.provider.upper(),
            format_enabled_badge(cred.enabled),
            format_health_badge(cred.status, enabled=cred.enabled),
            profiles_str,
            format_timestamp(cred.last_tested_at),
            format_timestamp(cred.last_used_at),
        )

    console.print(table)


def render_error_panel(
    message: str,
    *,
    hint: str | None = None,
    title: str = "KEYPOOL Error",
) -> None:
    """Render a sanitized error message to stderr."""
    err_console = get_console(stderr=True)
    safe_msg = redact_text(message)
    body = f"[bold red]✖ {safe_msg}[/bold red]"
    if hint:
        body += f"\n\n[yellow]Hint:[/yellow] {redact_text(hint)}"
    err_console.print(
        Panel(
            body,
            title=f"[bold red]{title}[/bold red]",
            border_style="red",
            box=box.ROUNDED,
            expand=False,
        )
    )
