# KEYPOOL — Universal AI Credential Platform & Local AI Gateway

[![Version](https://img.shields.io/badge/version-5.0.0-blue.svg)](pyproject.toml)
[![macOS](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black.svg)]()
[![Security](https://img.shields.io/badge/storage-macOS%20Keychain%20Only-green.svg)]()
[![License](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

**KEYPOOL** is a production-grade, universal AI credential management and developer infrastructure platform for macOS. Built from the ground up for high reliability, zero plaintext leakage, multi-provider failover, and developer ergonomics, KEYPOOL transforms how developers, agents, and teams manage API keys and route LLM traffic.

---

## The KEYPOOL Platform at a Glance

| Layer | Capabilities | Key Commands / APIs |
|---|---|---|
| **1. Universal Providers** | Native support for Groq, Anthropic, OpenAI, Gemini, Cohere, Replicate, Mistral, Together, Cerebras, DeepSeek, and Custom OpenAI-compatible endpoints with SSRF protection. | `keypool providers`, `keypool add <provider>`, `keypool test` |
| **2. Credential Intelligence** | Multi-key pooling, priority weights, tags, expiration tracking, auto-rotation alerts, duplicate SHA-256 fingerprint detection, and 7 selection strategies (Priority, LRU, Latency, Cost, Round Robin, Health-weighted, Random). | `keypool list`, `keypool tag`, `keypool rotate`, `keypool profile` |
| **3. Project & Budget Guardrails** | Workspace auto-discovery (`.keypool.toml`), per-project provider overrides, soft/hard budget ceilings with automatic hard-limit enforcement and daily/weekly/monthly periods. | `keypool project`, `keypool budget`, `keypool analytics` |
| **4. Developer Ecosystem** | Python SDK (`KeypoolClient`, `AsyncKeypoolClient`), Model Context Protocol (MCP) server for Claude Desktop / Cursor, Plugin architecture, Shell autocompletions (Zsh, Bash, Fish). | `keypool mcp`, `keypool plugin`, `keypool env`, `keypool completion` |
| **5. Local AI Gateway** | Production OpenAI-compatible reverse proxy on `http://127.0.0.1:8765` (`/v1/chat/completions`, `/v1/models`, `/v1/embeddings`), streaming SSE, intelligent routing, multi-credential automatic failover, and local bearer token authentication. | `keypool gateway start --daemon`, `keypool gateway status`, `keypool gateway token` |
| **6. Advanced Intelligence** | Comparative benchmarking (TTFT, latency, throughput TPS, cost), dynamic model aliases (`fast`, `smart`, `coding`), and proactive macOS desktop notifications via Notification Center. | `keypool benchmark run`, `keypool models alias`, `keypool alerts check` |
| **7. Native macOS Companion** | Lightweight Swift / SwiftUI Menu Bar app built with Swift Package Manager (`macos/KeypoolCompanion`), displaying real-time gateway status, model catalog, and one-click actions. | Menu Bar Icon, `swift build` |
| **8. Multi-Device Platform** | End-to-end encrypted synchronization using PBKDF2 (600,000 rounds) + AES-256-GCM authenticated encryption, device enrollment, and Last-Write-Wins conflict resolution. | `keypool sync status`, `keypool sync export`, `keypool sync import` |
| **9. Terminal UI (TUI)** | Rich interactive multi-tab terminal dashboard visualizing credential health matrix, active budgets, model catalog, and live usage rollups. | `keypool tui` |

---

## Complete Documentation Index

- [Installation & Quick-Start Guide](docs/installation.md)
- [Security Architecture & Threat Model](docs/threat_model.md)
- [Python SDK Reference (Sync & Async)](docs/sdk_reference.md)
- [Model Context Protocol (MCP) Guide](docs/mcp_guide.md)
- [Interactive TUI User Guide](docs/tui_guide.md)
- [macOS Companion App Guide](docs/macos_guide.md)
- [Encrypted Multi-Device Synchronization Guide](docs/sync_guide.md)
- [Plugin Architecture & Custom Provider Extension](docs/plugins.md)
- [CI/CD Integration & Headless Automation Guide](docs/cicd.md)
- [Frequently Asked Questions (FAQ)](docs/faq.md)
- [Full CLI Reference](docs/commands.md)
- [Provider Catalog & Adapters](docs/providers.md)

---

## Security Architecture

1. **Keychain-Only Secret Storage:** Real API keys reside exclusively in the macOS Keychain (`keyring.backends.macOS.Keyring`). Secrets are **never** stored in SQLite, config files, shell histories, or crash dumps. Plaintext fallback is strictly prohibited.
2. **Universal Redaction Engine:** Cryptographic fingerprints and known API keys are redacted across all logs, exception stack traces, CLI outputs, and JSON serialization.
3. **Child Process Isolation (`keypool exec`):** Injects only the designated provider environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, etc.) into the child process without modifying the parent shell's `os.environ`.
4. **SSRF Validation for Custom Endpoints:** Enforces strict URL scheme checking (`http://127.0.0.1`, `https://`), private IP blocking, and host validation on all custom provider configurations.

---

## Installation & Setup

### Requirements
- macOS (Apple Silicon `arm64` or Intel `x86_64`)
- Python `>= 3.11` (Python 3.11, 3.12, 3.13, 3.14, 3.15 supported)
- macOS Keychain enabled

### Install via pipx (Recommended)

```bash
# Clone the repository
git clone https://github.com/VinitSurve/keypool.git
cd keypool

# Install globally via pipx
pipx install .

# Verify installation
keypool --version
keypool doctor
```

---

## Quick Start Guide

### 1. Adding Credentials
```bash
# Add Groq, Anthropic, and OpenAI credentials (keys prompted securely with hidden input)
keypool add groq --name "Groq Primary" --priority 100 --tags prod,fast
keypool add anthropic --name "Claude Sonnet Key" --tags reasoning,coding
keypool add openai --name "GPT-4o Key" --priority 90
```

### 2. Testing Provider Connectivity & Model Discovery
```bash
keypool test --provider groq
keypool test --provider anthropic
keypool models list
```

### 3. Launching the Local AI Gateway
```bash
# Start background daemon on 127.0.0.1:8765
keypool gateway start --daemon

# Check gateway status
keypool gateway status

# View active Gateway Token
keypool gateway token
```

### 4. Consuming the Gateway via OpenAI SDK / Cursor / Claude Code
Point any OpenAI-compatible tool to KEYPOOL:
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8765/v1"
export OPENAI_API_KEY="$(keypool gateway token)"

# Example test via curl
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $(keypool gateway token)" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "fast",
    "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}]
  }'
```

### 5. Running Child Commands with Zero-Exposure Key Injection
```bash
# Run pytest or any Python application with selected credential injected into environment
keypool exec --profile production --provider groq -- python app.py
```

### 6. Model Benchmarking
```bash
keypool benchmark run --models fast,smart --prompt "Write a quicksort implementation in Python." --runs 3
```

### 7. Multi-Device Encrypted Sync
```bash
# Export encrypted bundle (AES-256-GCM + PBKDF2 600k rounds)
keypool sync export ~/Desktop/keypool_backup.kp

# Import and merge on another device
keypool sync import ~/Desktop/keypool_backup.kp
```

---

## Native macOS Companion App

Located in `macos/KeypoolCompanion`:
```bash
cd macos/KeypoolCompanion
swift build -c release
```
Run `KeypoolCompanion` to get an Apple-native menu bar utility showing real-time gateway health, active credentials, and quick actions right in your macOS status bar!

---

## Python SDK Reference

```python
from keypool import KeypoolClient, AsyncKeypoolClient

# Synchronous client
client = KeypoolClient()
key = client.get_key(provider="groq")
print("Selected key for Groq:", key.masked_secret)

# Asynchronous client
async with AsyncKeypoolClient() as async_client:
    models = await async_client.list_models()
    print("Discovered models:", len(models))
```

---

## Model Context Protocol (MCP) Server

KEYPOOL includes a full Model Context Protocol stdio server to let Claude Desktop and Cursor inspect credential health, active budgets, model catalogs, and diagnostics:

Add to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "keypool": {
      "command": "keypool",
      "args": ["mcp"]
    }
  }
}
```

---

## Complete CLI Reference

```text
keypool [OPTIONS] COMMAND [ARGS]...

Commands:
  alerts        Manage desktop alerts, notifications, and monitoring rules.
  analytics     Query usage analytics, cost aggregations, and trends.
  backup        Create and verify AES-256-GCM encrypted database backups.
  benchmark     Benchmark latency, TTFT, throughput (TPS), and costs across models.
  budget        Configure spend ceilings and hard/soft limits.
  completion    Generate autocompletion scripts for zsh, bash, and fish.
  disable       Temporarily deactivate a credential without deleting it.
  doctor        Run 17 comprehensive system, keychain, and health diagnostics.
  enable        Reactivate a disabled credential.
  env           Inspect environment variables and export configurations.
  exec          Execute child processes with isolated credential injection.
  gateway       Start, stop, and inspect the local OpenAI-compatible gateway daemon.
  get           Select the best active API key for shell piping or scripts.
  health        Inspect 5-layer health states and cached error classifications.
  list          List all credentials with health badges and priorities.
  mcp           Launch Model Context Protocol stdio server for LLM assistants.
  models        Inspect model catalog, pricing specs, and routing aliases.
  plugin        Inspect and manage custom provider and integration plugins.
  profile       Manage isolated profiles (testing, development, production).
  project       Manage directory projects and workspace overrides.
  providers     Inspect registered provider adapters and API capabilities.
  remove        Permanently delete a credential from Keychain and database.
  rename        Update a credential's friendly name.
  requests      Inspect recent execution requests and latency history.
  restore       Restore a full encrypted backup archive.
  rotate        Rotate an API key in Keychain with zero-downtime swap.
  status        Display system overview dashboard.
  sync          Manage encrypted multi-device synchronization and enrolled peers.
  tag           Add metadata tags to a credential.
  test          Run live authentication probe and test generation against a provider.
  tui           Launch interactive multi-tab terminal UI.
  untag         Remove tags from a credential.
  update        Update credential name, quota domain, or secret.
  usage         View aggregate token usage, executions, and 429 rate limit events.
```

---

## Development & Test Suite

KEYPOOL maintains a rigorous test suite covering provider conformance, Keychain encryption, selection algorithms, gateway routing, and error classification:

```bash
# Run pytest test suite
.venv/bin/pytest -v

# Run Ruff linter and code formatter
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests

# Run Mypy strict type checking
.venv/bin/mypy src

# Build package distributions
.venv/bin/python -m build
```

---

## License

MIT License. Designed and engineered for production macOS environments.
