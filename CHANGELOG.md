# Changelog

All notable changes to **KEYPOOL** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

