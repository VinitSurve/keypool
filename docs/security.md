# Security Architecture & Threat Model

## 1. Secret Storage Guarantee

- **Exclusive Backend:** All API secrets are stored in the **macOS login Keychain** via `keyring.backends.macOS.Keyring` under service identifier `com.keypool.credentials` and account reference `keypool:<credential_id>`.
- **Zero Plaintext Fallback:** `MacOSKeychainStore._resolve_backend()` verifies at runtime that the active backend is the native macOS Keychain. If a non-macOS, file-based, or null backend is encountered, KEYPOOL raises `SecurityError` (exit code `6`) and aborts immediately.
- **SQLite Guardrails:** SQLAlchemy `before_insert` and `before_update` listeners enforce that `keychain_reference` always begins with `keypool:` and scrub all free-text fields (`last_error_message`, `sanitized_message`, `summary`) through the centralized secret redactor before persistence.
- **Configuration Guardrails:** `load_settings()` scans `config.toml` both as raw text and parsed keys before validation. Any forbidden key (`api_key`, `secret`, `token`, `groq_api_key`, `gemini_api_key`, etc.) or API key regex match immediately raises `SecurityError`.

## 2. Centralized Secret Redaction

`src/keypool/security/redaction.py` implements `SecretRedactor`:

1. **Known-Secret Registry:** Whenever a secret is entered, stored, or retrieved from Keychain in memory, it is registered with `_GLOBAL_REDACTOR`. Any exact substring occurrence is scrubbed to `[REDACTED]`.
2. **Pattern Matching:** Regex filters scrub `gsk_...` (Groq), `AIza...` (Google Gemini), `sk-...`, `Bearer ...`, `x-goog-api-key: ...`, URL query parameters (`?key=...`), and JSON secret fields.
3. **Automatic Exception & Log Redaction:**
   - `KeypoolError.__init__` redacts both `message` and `hint` on instantiation.
   - `RedactingLogFilter` and `RedactingFormatter` scrub all log records and tracebacks before writing to `logs/keypool.log`.
   - `format_json_envelope` recursively scrubs all JSON output dictionaries and strings before serialization.

## 3. Process Execution Security Boundary (`keypool exec`)

When running `keypool exec --profile <profile> --provider <provider> -- <command...>`:

1. **No Shell Interpretation:** Child processes are spawned via `subprocess.Popen(..., shell=False)`.
2. **Parent Environment Immutability:** `build_child_environment()` clones `os.environ` into a new dictionary; the parent process's `os.environ` is never modified.
3. **Single-Provider Injection:** Any pre-existing `GROQ_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` variables are stripped from the child environment copy, and **only** the requested provider's environment variable is injected.
4. **Controlled Environment Mode (`--env-mode controlled`):** Users who want stricter isolation can run with `--env-mode controlled` (or set `execution.env_mode = "controlled"` in `config.toml`), which passes only a strict whitelist of baseline OS variables (`PATH`, `HOME`, `USER`, `LANG`, `TERM`, `TMPDIR`, TLS certificate variables, `VIRTUAL_ENV`) plus the single target provider variable.
5. **Command-Line Argument Verification:** `verify_command_contains_no_secret()` inspects the child `argv` list and refuses execution if the secret value appears in any command-line argument.

### Honest Security Boundary Disclosure: Child Process Inheritance

Environment variable injection is the industry-standard mechanism expected by official AI SDKs (`groq`, `google-genai`), but **it is not invisible inside the child process tree**:

- The child process launched by `keypool exec` and any descendant processes it spawns inherit the child environment and can read the injected environment variable (`GROQ_API_KEY` or `GEMINI_API_KEY`).
- On macOS, processes running under the **same local macOS user account (`uid`)** generally have permission to inspect environment variables of other processes owned by that same user or query the unlocked login Keychain under that user's session.

## 4. Threat Model & Known Limitations

| Threat | Mitigation in KEYPOOL | Residual Risk / Limitation |
|---|---|---|
| **Accidental Git commit of `.env` or config files** | Secrets are stored in macOS Keychain; `config.toml` rejects secrets; no temporary `.env` files are ever created. | If a user runs `keypool get --show > .env`, the user creates a plaintext file outside KEYPOOL's control. |
| **Secret leakage in logs, tracebacks, or JSON output** | Centralized `SecretRedactor` scrubs known secrets and key patterns across exceptions, logs, HTTP errors, and JSON output. | Unusual custom token formats shorter than 4 characters cannot be pattern-matched unless registered. |
| **Cross-environment credential misuse (e.g. test using prod key)** | Strict profile isolation in `SelectionEngine`: `testing` only sees credentials explicitly assigned to `testing`. | User must assign the appropriate credentials to each profile. |
| **Keychain unavailable or locked** | Raises `KeychainAccessDeniedError` / `KeychainError`; never falls back to plaintext files. | Automated headless SSH/cron sessions on macOS may require unlocking the login Keychain first. |
| **Malicious process running as the same macOS user** | Restrictive `0700`/`0600` permissions on metadata files prevent other local Unix users from reading metadata. | **macOS Keychain does not prevent a malicious process running under the same unlocked user session from accessing user-accessible Keychain items or inspecting child processes.** |

