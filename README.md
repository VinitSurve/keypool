# KEYPOOL — Local-First macOS AI API Credential Manager

**KEYPOOL** (`keypool`) is a production-ready, local-first, globally installed macOS command-line utility for managing unlimited API credentials across multiple AI providers (**Groq** and **Google Gemini**), isolating credentials across execution profiles, monitoring credential health, and securely injecting secrets into child processes.

---

## Key Features

- **macOS Keychain Secret Storage:** Actual API keys are stored exclusively in the native **macOS Keychain** (`keyring.backends.macOS.Keyring`) under `com.keypool.credentials`. Secrets are never written to SQLite, `config.toml`, `.env` files, or application logs, and plaintext fallback is strictly prohibited.
- **Unlimited Multi-Key Pool:** Register and manage any number of credentials per provider (`cred_<hex>`), with friendly labels, enable/disable toggles, safe secret replacement, and partial-operation reconciliation.
- **Granular 12-State Health Monitoring:** Distinguishes 5 verification layers (database existence, Keychain retrievability, authentication, model accessibility, and request completion) across 12 states (`healthy`, `untested`, `auth_failed`, `permission_denied`, `rate_limited`, `quota_exhausted`, `model_unavailable`, `provider_unavailable`, `network_error`, `keychain_error`, `disabled`, `unknown`) with configurable TTL caching.
- **Deterministic Multi-Key Selection:** Selects eligible credentials by filtering out disabled, auth-failed, and cooldown-active keys, preferring verified healthy credentials, and tie-breaking via **Least-Recently-Used (LRU)** ordering.
- **Strict Profile Isolation:** Ships with built-in `testing`, `development`, and `production` profiles plus custom profile support (`keypool profile create <name>`). Cross-profile fallback is strictly forbidden—`testing` can never silently consume `production` credentials.
- **Secure Child Process Execution (`keypool exec`):** Launches child commands (`python`, `pytest`, CLI tools) with only the target provider's official environment variable (`GROQ_API_KEY` or `GEMINI_API_KEY`) injected into the child process environment while keeping the parent shell environment unmodified.
- **Automation-Ready JSON Interface:** Supports `--json` across informational commands with predictable ISO 8601 schemas and centralized secret redaction.

---

## Terminal Overview

```text
╭──────────────────────────────────────────────────────╮
│                                                      │
│   ██╗  ██╗███████╗██╗   ██╗██████╗  ██████╗  ██████╗ │
│   ██║ ██╔╝██╔════╝╚██╗ ██╔╝██╔══██╗██╔═══██╗██╔═══██╗│
│   █████╔╝ █████╗   ╚████╔╝ ██████╔╝██║   ██║██║   ██║│
│   ██╔═██╗ ██╔══╝    ╚██╔╝  ██╔═══╝ ██║   ██║██║   ██║│
│   ██║  ██╗███████╗   ██║   ██║     ╚██████╔╝╚██████╔╝│
│   ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝      ╚═════╝  ╚═════╝ │
│                                                      │
│             LOCAL AI CREDENTIAL MANAGER              │
╰──────────────────────────────────────────────────────╯

  SYSTEM OVERVIEW

    Credentials       02
    Enabled           02
    Healthy           02
    Untested          00
    Disabled          00

  PROVIDERS

    GROQ        ● 1 credential
    GEMINI      ● 1 credential

  ACTIVE PROFILE

  testing
```

---

## Requirements

- **Operating System:** macOS (Apple Silicon `arm64` or Intel `x86_64`) with macOS Keychain enabled
- **Python:** Python `>= 3.11` (tested on Python `3.11` through `3.15`)
- **Distribution Tool:** `pipx` (installable via Homebrew: `brew install pipx && pipx ensurepath`)

---

## Installation

### Global Installation via `pipx` (Recommended)

```bash
cd /Users/vinit/Vinit/Keypool
pipx install .
```

Verify the global installation from any directory:

```bash
keypool --version
keypool --help
keypool doctor
```

### Upgrade or Reinstall

```bash
pipx install --force .
```

### Uninstall

```bash
pipx uninstall keypool
```

*(Uninstalling the CLI binary leaves your `~/Library/Application Support/keypool/` metadata and macOS Keychain items intact unless explicitly removed via `keypool remove <id>`.)*

---

## Quick Start

```bash
# 1. Add Groq and Gemini credentials (API key prompted with hidden input)
keypool add groq
keypool add gemini

# 2. List configured credentials
keypool list

# 3. Test live provider connectivity and model discovery
keypool test <credential_id>
keypool test --provider groq

# 4. Assign credentials to the 'testing' profile
keypool profile assign testing <credential_id>
keypool profile show testing

# 5. Execute a child process with the selected provider credential injected
keypool exec --profile testing --provider groq -- python app.py
keypool exec --profile testing --provider gemini -- pytest tests/integration/
```

---

## Architecture Overview

```text
src/keypool/
├── main.py                  # Entry point (keypool = "keypool.main:app")
├── version.py               # Version & schema constants
├── cli/                     # Typer CLI & Rich UI presentation layer
│   ├── app.py               # Command registration & exit-code boundary
│   ├── formatting.py        # Rich tables, panels, badges & dashboard
│   ├── prompts.py           # Hidden secret input & confirmations
│   └── commands/            # Modular command handlers
├── core/                    # Domain managers
│   ├── credential_manager.py
│   ├── profile_manager.py
│   ├── selection_engine.py
│   ├── health_manager.py
│   ├── execution_manager.py
│   ├── usage_tracker.py
│   └── exceptions.py
├── providers/               # Extensible provider adapter system
│   ├── base.py
│   ├── registry.py
│   ├── groq.py              # Groq OpenAI-compatible API adapter
│   └── gemini.py            # Google Gemini v1beta API adapter
├── storage/                 # Persistence layer
│   ├── keychain.py          # SecretStore & MacOSKeychainStore
│   ├── database.py          # SQLite WAL engine & integrity checks
│   ├── models.py            # SQLAlchemy 2.x ORM models
│   └── migrations.py        # Versioned schema migrations
├── security/                # Security engineering
│   ├── redaction.py         # Centralized pattern & known-secret redactor
│   ├── validation.py        # Input, ID, & permission validation
│   └── environment.py       # Child process environment isolation
├── config/                  # TOML settings (~/Library/Application Support/keypool/config.toml)
│   ├── defaults.py
│   └── settings.py
└── integrations/            # Machine-readable JSON envelope
    └── json_output.py
```

---

## Complete Command Summary

| Category | Command | Description |
|---|---|---|
| **Overview** | `keypool` / `keypool status [--json]` | Display live dashboard overview |
| **Credentials** | `keypool add <groq\|gemini>` | Add a credential via hidden input to macOS Keychain |
| | `keypool list [--provider <p>] [--json]` | List credentials and health states |
| | `keypool show <id> [--json]` | Display non-secret metadata for a credential |
| | `keypool update <id>` | Update friendly name and/or replace Keychain secret |
| | `keypool rename <id> [new_name]` | Rename a credential's friendly label |
| | `keypool enable <id>` / `keypool disable <id>` | Enable or disable a credential |
| | `keypool remove <id> [--yes]` | Remove credential from Keychain and SQLite |
| **Providers** | `keypool providers [--json]` | List registered provider adapters |
| | `keypool providers show <groq\|gemini> [--json]` | Show adapter endpoints and env var mapping |
| **Health** | `keypool test [<id> \| --provider <p>] [--json]` | Run live authentication, model, and generation test |
| | `keypool health [--provider <p>] [--refresh] [--json]` | View cached or refreshed 5-layer health status |
| | `keypool doctor [--json]` | Run 17 system, DB, Keychain, and permission checks |
| **Profiles** | `keypool profile list [--json]` | List execution profiles (`testing`, `development`, `production`, custom) |
| | `keypool profile show <profile> [--json]` | Inspect profile assignments |
| | `keypool profile create <profile>` | Create a new isolated profile |
| | `keypool profile assign <profile> <id>` | Explicitly assign a credential to a profile |
| | `keypool profile unassign <profile> <id>` | Remove a credential assignment from a profile |
| | `keypool profile delete <profile> [--yes]` | Delete a custom profile |
| **Execution** | `keypool exec --profile <f> --provider <p> -- <cmd...>` | Run child process with isolated credential env var |
| | `keypool get --provider <p> [--profile <f>] [--show]` | Select credential (metadata by default; raw stdout with `--show`) |
| **Usage** | `keypool usage [--provider <p>] [--credential <id>] [--period 7d] [--json]` | Inspect locally observed selections, executions, and 429s |

### Standardized Exit Codes

- `0`: Success (or child exit code `0` for `exec`)
- `1`: General application failure
- `2`: Invalid arguments (`InvalidArgumentError`, `ProfileError`)
- `3`: Credential or profile unavailable (`CredentialNotFoundError`, `CredentialUnavailableError`, `ProfileNotFoundError`)
- `4`: Provider error (`ProviderError`, failed live `keypool test`)
- `5`: Storage or Keychain error (`StorageError`, `KeychainError`, failed `keypool doctor`)
- `6`: Security policy violation (`SecurityError`, `KeychainAccessDeniedError`)

---

## Documentation Index

- [Architecture Guide](docs/architecture.md)
- [Security & Threat Model](docs/security.md)
- [Command Reference](docs/commands.md)
- [Providers Guide (Groq & Google Gemini)](docs/providers.md)
- [Profiles & Selection Policy](docs/profiles.md)
- [Integrations Guide (Python, Pytest, Antigravity, CLI Tools, RPX)](docs/integrations.md)
- [Troubleshooting Guide](docs/troubleshooting.md)
- [Environment Inspection Report](docs/environment.md)

---

## Development & Testing

```bash
# Run the complete unit, provider, CLI, security, and macOS Keychain test suite
.venv/bin/pytest -v

# Run linting and formatting checks
.venv/bin/ruff check src tests

# Run strict static type checking
.venv/bin/mypy src tests

# Build wheel and sdist packages
.venv/bin/python -m build
```

