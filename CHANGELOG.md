# Changelog

All notable changes to **KEYPOOL** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [5.0.0] - 2026-11-10

### Added
- **Universal Provider Ecosystem (v5.0):**
  - Added enterprise cloud specialized provider adapters: Microsoft Azure OpenAI (`azure_openai`), Amazon Bedrock (`bedrock`), Google Cloud Vertex AI (`vertex`), IBM watsonx.ai (`watsonx`).
  - Broadened full provider catalog to 43+ leading AI providers and local runtimes (OpenAI, Anthropic, Gemini, Groq, Cohere, Replicate, Mistral, Together, DeepSeek, Cerebras, Ollama, LM Studio, vLLM, llama.cpp, LocalAI, etc.).
  - Generic OpenAI-compatible adapter framework with SSRF-safe URL validation.
- **Advanced Credential Intelligence:**
  - Bulk credential management operations (`bulk_add_credentials`, `bulk_remove_credentials`, `bulk_set_enabled`, `bulk_tag`, `bulk_untag`).
  - Credential rotation reminders (`keypool reminders`, `check_rotation_reminders`) and audit history (`keypool history`).
  - Encrypted credential bundle export/import (`export-keys`, `import-keys`) with AES-256-GCM.
- **Deterministic 9-Strategy Selection Engine:**
  - Full selection strategy implementation: `priority`, `round_robin`, `lru`, `health_first`, `latency`, `cost`, `random`, `weighted`, and `manual`.
  - Cryptographically secure randomized weighted selection via `secrets.SystemRandom`.
- **Model Catalog Discovery & Comparison:**
  - Dynamic discovery and caching of provider model endpoints.
  - Model comparison (`keypool models compare <model_a> <model_b>`, SDK `compare_models`) evaluating context windows, pricing, modalities, and streaming support.
- **Budget Governance & Forecasting:**
  - Model-scoped budgets supporting daily, weekly, and monthly periods.
  - Linear regression spend forecasting (`keypool budget forecast`, `forecast_spend`).
  - Hard limit budget enforcement directly blocking API calls in Local AI Gateway and CLI executions.
  - CSV usage event export (`keypool usage --csv`).
- **Developer SDK & MCP Server:**
  - Full synchronous (`KeypoolClient`) and asynchronous (`AsyncKeypoolClient`) Python SDKs.
  - Typed text embeddings API (`client.embeddings.create`, `async_client.embeddings.create`).
  - SSE streaming chat completion chunks (`ChatCompletionChunk`).
  - Complete MCP server exposing 9 safe tools (`list_credentials`, `test_credential`, `get_usage_summary`, `list_profiles`, `list_projects`, `get_gateway_status`, `compare_models`, `list_alerts`, `check_rotation_reminders`).
- **Interactive 12-Tab Terminal User Interface (TUI):**
  - 12 comprehensive Rich tabs: `overview`, `credentials`, `providers`, `health`, `models`, `profiles`, `projects`, `usage`, `budgets`, `gateway`, `settings`, `diagnostics`.
  - Direct view jump flag: `keypool tui --view <tab>`.
- **Native macOS Companion App:**
  - SwiftUI MenuBar application (`macos/KeypoolCompanion`) with real-time status, one-click gateway toggle, and system notifications.
- **Encrypted Multi-Device Sync:**
  - Zero-cloud AES-256-GCM encrypted peer-to-peer export/import with PBKDF2 key derivation and replay protection.
- **Comprehensive Documentation Suite:**
  - Installation, Threat Model, Python SDK, MCP Guide, TUI Guide, macOS Guide, Sync Guide, Plugin Development, CI/CD Integration, and FAQ.

## [1.0.0] - 2026-11-09

### Added
- **macOS Keychain Secret Backend (`SecretStore`):** Exclusive secret storage via macOS Keychain (`keyring`) with zero plaintext fallback, safe replacement, deletion, and partial-operation reconciliation.
- **SQLite Metadata & Usage Store:** SQLAlchemy 2.x backed metadata persistence in `~/Library/Application Support/keypool/keypool.db` with WAL mode, schema versioning, migrations, strict `0700`/`0600` filesystem permissions, and integrity verification.
- **Credential Lifecycle Management:** `add`, `list`, `show`, `update`, `rename`, `enable`, `disable`, `remove`, and `test` commands supporting unlimited credentials per provider.
- **Provider Adapter System:** Extensible `BaseProvider` and `ProviderRegistry` with official adapters for **Groq** (`https://api.groq.com/openai/v1`) and **Google Gemini** (`https://generativelanguage.googleapis.com/v1beta`), dynamic model discovery, minimal functional generation testing, HTTP status/error classification, and `Retry-After` / rate-limit header parsing.
- **Health Monitoring & Caching:** Granular 12-state health model (`healthy`, `untested`, `auth_failed`, `permission_denied`, `rate_limited`, `quota_exhausted`, `model_unavailable`, `provider_unavailable`, `network_error`, `keychain_error`, `disabled`, `unknown`) with configurable TTL caching and `keypool health [--refresh]`.
- **Profile Management:** Explicit credential isolation across default profiles (`testing`, `development`, `production`) and user-defined custom profiles with strict cross-profile boundary enforcement.
- **Multiple-Key Selection Engine:** Deterministic credential selection filtering disabled, auth-failed, and cooldown-active keys, preferring healthy credentials and using Least-Recently-Used (LRU) tie-breaking.
- **Child Process Execution Engine (`keypool exec`):** Subprocess execution injecting only the target provider's environment variable into the child process while keeping the parent environment unmodified, propagating exit codes and signals cleanly.
- **Controlled Programmatic Retrieval (`keypool get`):** Safe metadata selection with explicit `--show` raw secret stdout mode and stderr security warnings.
- **Local Usage Tracking (`keypool usage`):** Locally observed execution, selection, HTTP 429, and token tracking filterable by provider, credential, and time period.
- **System Diagnostics (`keypool doctor`):** 17-point diagnostic verification of Python, macOS, SQLite integrity, Keychain read/write, provider registry, profile/credential consistency, permissions, and PATH availability.
- **Rich Terminal Dashboard & JSON Automation Interface:** Full Rich CLI UX plus stable `--json` output across informational commands (`status`, `list`, `health`, `providers`, `profile list`, `doctor`, `usage`).
- **Centralized Security & Redaction Layer:** Multi-pattern and known-secret redaction across logs, exceptions, HTTP responses, and CLI/JSON output.

