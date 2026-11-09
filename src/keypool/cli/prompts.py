"""Interactive terminal prompts for credential entry and destructive confirmations."""

from __future__ import annotations

import typer

from keypool.security.redaction import register_secret_for_redaction


def prompt_credential_name(provider_display: str, default: str | None = None) -> str:
    """Prompt the user for a friendly credential name."""
    if default:
        value: str = typer.prompt(
            f"Friendly name for {provider_display} credential",
            default=default,
        )
    else:
        value = typer.prompt(f"Friendly name for {provider_display} credential")
    return value.strip()


def prompt_api_key(provider_display: str) -> str:
    """Request an API key using hidden terminal input (never echoed)."""
    secret: str = typer.prompt(
        f"Enter {provider_display} API key (hidden input)",
        hide_input=True,
    )
    register_secret_for_redaction(secret)
    return secret.strip()


def confirm_destructive_action(prompt_text: str, *, default: bool = False) -> bool:
    """Request confirmation before replacing or deleting a credential or profile."""
    return bool(typer.confirm(prompt_text, default=default))
