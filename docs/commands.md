# KEYPOOL Command Reference

## 1. Global Overview & Diagnostics

```bash
keypool                      # Render interactive Rich system overview dashboard
keypool --help               # Show CLI help
keypool --version            # Print version (keypool 1.0.0)
keypool status [--json]      # Display system overview (or JSON status)
keypool doctor [--json]      # Run 17 system, database, Keychain, and permission checks
```

## 2. Credential Management

```bash
# Add a new credential (prompts for friendly name and hidden API key)
keypool add groq [--name "Groq Testing 01"] [--profile testing]
keypool add gemini [--name "Gemini Dev 01"] [--profile development]

# List credentials
keypool list
keypool list --provider groq
keypool list --provider gemini
keypool list --json

# Show detailed non-secret metadata for one credential
keypool show <credential_id> [--json]

# Update friendly name and/or replace stored Keychain secret
keypool update <credential_id> [--name "New Name"] [--replace-key] [--yes]

# Rename friendly label
keypool rename <credential_id> ["New Name"]

# Enable or disable a credential (disabled credentials are never selected)
keypool enable <credential_id>
keypool disable <credential_id>

# Permanently remove a credential from macOS Keychain and SQLite
keypool remove <credential_id> [--yes]
```

## 3. Provider Management

```bash
keypool providers [--json]
keypool providers show groq [--json]
keypool providers show gemini [--json]
```

## 4. Health & Connectivity Testing

```bash
# Test a single credential or all credentials for a provider
keypool test <credential_id> [--functional / --no-functional] [--json]
keypool test --provider groq [--functional / --no-functional] [--json]
keypool test --provider gemini [--functional / --no-functional] [--json]

# View cached health observations or force a live refresh
keypool health [--provider <groq|gemini>] [--json]
keypool health --refresh [--functional] [--json]
```

## 5. Execution Profiles

```bash
keypool profile list [--json]
keypool profile show <profile_name> [--json]
keypool profile create <profile_name> [--description "..."] [--preferred-provider <p>]
keypool profile assign <profile_name> <credential_id> [--priority 100]
keypool profile unassign <profile_name> <credential_id>
keypool profile delete <profile_name> [--yes]
```

## 6. Child Process Execution & Programmatic Retrieval

```bash
# Preferred: Inject the selected credential into a child process environment
keypool exec --profile testing --provider groq -- python app.py
keypool exec --profile testing --provider gemini -- pytest tests/integration/
keypool exec --profile development --provider groq --env-mode controlled -- python worker.py

# Programmatic selection (outputs metadata by default; raw secret only with --show)
keypool get --provider groq [--profile testing] [--json]
keypool get --provider gemini [--profile testing] --show
```

## 7. Local Usage Tracking

```bash
keypool usage
keypool usage --provider groq
keypool usage --credential <credential_id>
keypool usage --period 7d
keypool usage --json
```

## 8. Machine-Readable JSON Contract

All `--json` commands write a standardized envelope to `stdout`:

```json
{
  "ok": true,
  "command": "status",
  "version": "1.0.0",
  "generated_at": "2026-11-09T14:55:00.000000+00:00",
  "data": { ... }
}
```

When an error occurs with `--json`, a structured error envelope is written to `stderr` and the process exits with the corresponding `ExitCode` (`1`–`6`).

