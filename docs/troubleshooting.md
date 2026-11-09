# Troubleshooting Guide

## 1. `No credentials are assigned to profile 'testing' for provider 'groq'` (Exit Code 3)

**Cause:** KEYPOOL enforces strict profile isolation. Adding a credential via `keypool add groq` registers it in the pool, but profiles only use credentials explicitly assigned to them.

**Solution:**
```bash
keypool list
keypool profile assign testing <credential_id>
```

---

## 2. `macOS Keychain access was denied or Keychain is locked` (Exit Code 6)

**Cause:** Your macOS `login` Keychain is locked (common in headless SSH sessions) or the macOS Keychain access dialog was denied.

**Solution:**
1. On desktop macOS, click **Allow** or **Always Allow** when prompted by macOS Keychain.
2. In an SSH session, unlock your login Keychain first:
   ```bash
   security unlock-keychain ~/Library/Keychains/login.keychain-db
   ```
3. Run `keypool doctor` to verify Keychain read/write capability.

---

## 3. `Secret missing in macOS Keychain for reference 'keypool:cred_...'` (Exit Code 5)

**Cause:** The credential's metadata exists in SQLite, but the corresponding item in macOS Keychain (`com.keypool.credentials`) was deleted externally (e.g., via Keychain Access.app).

**Solution:**
Either replace the secret or remove the credential:
```bash
keypool update <credential_id> --replace-key
# OR
keypool remove <credential_id>
```

---

## 4. `Google Gemini authentication failed: invalid API key` (HTTP 400 / 401)

**Cause:** Google Gemini returns `HTTP 400 INVALID_ARGUMENT` (`API_KEY_INVALID`) when an API key is malformed, revoked, or restricted to a different Google Cloud API.

**Solution:**
1. Verify your key in Google AI Studio (`https://aistudio.google.com/app/apikey`).
2. Update the stored secret in KEYPOOL:
   ```bash
   keypool update <credential_id> --replace-key
   keypool test <credential_id>
   ```

---

## 5. `Forbidden secret key '...' found in config.toml` (Exit Code 6)

**Cause:** Someone placed an API key or secret field inside `~/Library/Application Support/keypool/config.toml`. KEYPOOL strictly prohibits storing secrets in configuration files.

**Solution:**
Remove the secret line from `~/Library/Application Support/keypool/config.toml` and store the credential securely via `keypool add <provider>`.

