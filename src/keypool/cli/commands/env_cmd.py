"""Developer integration commands for shell environment variables and completions (Section 22)."""

from __future__ import annotations

import sys

import typer

from keypool.cli.formatting import RuntimeContext, get_console
from keypool.integrations.json_output import emit_json

env_app = typer.Typer(
    name="env",
    help="Export resolved API credentials to current shell or CI environment (Section 22).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@env_app.callback()
def cmd_env(
    ctx_typer: typer.Context,
    provider: str = typer.Option(
        ..., "--provider", "-p", help="Provider to resolve (e.g. 'openai', 'anthropic', 'groq')."
    ),
    profile: str | None = typer.Option(None, "--profile", help="Execution profile to select from."),
    fmt: str = typer.Option(
        "export", "--format", "-f", help="Output format: 'export', 'sh', 'json'."
    ),
) -> None:
    """Print safe shell export syntax for the optimal credential."""
    ctx = RuntimeContext()
    selected = ctx.selection_engine.select_credential(
        provider=provider,
        profile=profile,
        record_usage=True,
    )
    secret = ctx.credential_manager.retrieve_secret(selected.metadata.id)
    var_name = selected.env_var_name

    if fmt == "json":
        emit_json(
            "env",
            {"env_var": var_name, "provider": provider, "credential_id": selected.metadata.id},
        )
        return

    if fmt == "sh":
        sys.stdout.write(f'{var_name}="{secret}"\n')
    else:
        sys.stdout.write(f'export {var_name}="{secret}"\n')
    sys.stdout.flush()


def cmd_completion(
    shell: str = typer.Argument(..., help="Target shell: 'bash', 'zsh', 'fish'."),
) -> None:
    """Generate shell completion script."""
    clean = shell.lower().strip()
    console = get_console()
    if clean == "zsh":
        sys.stdout.write(
            '#compdef keypool\n_keypool() { eval "$(_KEYPOOL_COMPLETE=zsh_complete keypool)" }\ncompdef _keypool keypool\n'
        )
    elif clean == "bash":
        sys.stdout.write('eval "$(_KEYPOOL_COMPLETE=bash_complete keypool)"\n')
    elif clean == "fish":
        sys.stdout.write("eval (env _KEYPOOL_COMPLETE=fish_complete keypool)\n")
    else:
        console.print(f"[red]Unsupported shell '{shell}'. Supported: bash, zsh, fish.[/red]")
        raise typer.Exit(1)
    sys.stdout.flush()
