# KEYPOOL Security Architecture & Threat Model

KEYPOOL is designed under a **Zero-Secret Disk Footprint** model. All cryptographic secrets and API tokens are restricted to the encrypted macOS Keychain and runtime process memory.

---

## 1. Threat Vectors and Mitigations

| Threat | Risk Level | Mitigation Strategy |
| :--- | :--- | :--- |
| **Plaintext Leak via Config/Logs** | Critical | Strict AST redaction engine (`register_secret_for_redaction`), regex pattern detection (`_SECRET_VALUE_RE`), and schema guard rejecting secrets in `config.toml`. |
| **Malicious Environment Inheritance** | High | Transparent child process isolation. Environment injection occurs only within spawned child scopes; parent shell remains untainted. |
| **Server-Side Request Forgery (SSRF)** | High | `validate_loopback_url` restricts local HTTP gateways, webhook URLs, and reverse proxy forwarders from redirecting to link-local (`169.254.0.0/16`) or arbitrary internal cloud metadata services (`169.254.169.254`). |
| **Database File Exfiltration** | High | SQLite database stores metadata only (`keychain_reference`, `provider`, `last_used_at`). API keys are never written to disk or database files. DB permissions are locked to `0600`. |
| **Key Theft via Memory Inspection** | Medium | Secure memory wiping upon token dereference. Ephemeral access tokens and session headers are garbage-collected immediately following API calls. |
| **Multi-Device Sync Tampering** | High | AES-256-GCM symmetric encryption using PBKDF2 (100,000 rounds) and HMAC-SHA256 signature verification. Replay protection via UTC timestamp cutoffs. |

---

## 2. macOS Keychain Security Boundary

- **Service Name:** `com.vinit.keypool` (or user-defined in `config.toml`)
- **Key Reference Format:** `keypool:<provider>:<id>`
- **Access Group:** Isolated to KEYPOOL CLI and KEYPOOL Companion application via code-signing entitlements.
- **Access Control:** Keys cannot be retrieved by unauthorized third-party binaries without user approval prompt.

---

## 3. Cryptographic Storage & File Isolation

```
~/.config/keypool/
├── config.toml           [0600 - Owner Read/Write Only]
├── keypool.db            [0600 - SQLite Metadata DB, WAL Mode]
├── keypool.db-wal        [0600 - Write-Ahead Log]
├── gateway_token         [0600 - Ephemeral 256-bit Hex Bearer Token]
└── logs/                 [0700 - Directory]
    ├── keypool.log       [0600 - Sanitized Application Log]
    └── gateway.log       [0600 - Sanitized Reverse Proxy Log]
```

---

## 4. Error Sanitization & Secret Redaction

KEYPOOL employs bidirectional redaction:
1. When any credential is added, its secret and derived signatures are registered in the global redaction trie (`register_secret_for_redaction`).
2. All exception messages, HTTP headers, stdout logs, stderr panels, and SQLite error fields pass through `redact_text(text)`.
3. Standard patterns (`Bearer ...`, `sk-...`, `gsk_...`, `AIza...`) are automatically converted to `[REDACTED]`.
