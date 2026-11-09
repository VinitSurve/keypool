# KEYPOOL Encrypted Multi-Device Synchronization Guide

KEYPOOL provides peer-to-peer encrypted synchronization across multiple macOS devices without relying on third-party cloud accounts.

---

## 1. Cryptographic Protocol

- **Cipher:** AES-256-GCM (Galois/Counter Mode) with authenticated 128-bit integrity tag.
- **Key Derivation:** PBKDF2-HMAC-SHA256 with 100,000 iterations and cryptographically random 16-byte salt (`os.urandom(16)`).
- **Integrity & Anti-Replay:** Payload embeds UTC generation timestamp, source device identifier, and bundle checksum.
- **Replay Protection:** Sync bundles generated more than 24 hours prior are rejected by default.

---

## 2. Exporting an Encrypted Bundle

```bash
# Export all credentials and metadata to an encrypted bundle
keypool export-keys --output ~/Desktop/keypool-backup.kpb
# You will be prompted to enter a strong encryption passphrase
```

Or programmatically:
```python
bundle_bytes = cm.export_credentials_bundle(passphrase="YourSecurePassphrase!")
Path("bundle.kpb").write_bytes(bundle_bytes)
```

---

## 3. Importing an Encrypted Bundle

On the destination Mac:
```bash
keypool import-keys ~/Desktop/keypool-backup.kpb
```

During import:
1. Passphrase decrypts and verifies the GCM tag.
2. Fingerprint comparison checks for duplicate credentials. By default, existing credentials with matching secrets are preserved rather than duplicated. Use `--allow-duplicate` to override.
3. Decrypted secrets are inserted directly into the destination machine's macOS Keychain.
4. Profiles, tags, priorities, and rotation intervals are updated in SQLite.

---

## 4. Conflict Resolution

KEYPOOL employs a deterministic **Last-Modified Wins** conflict resolution strategy keyed by `updated_at` ISO 8601 UTC timestamps.
