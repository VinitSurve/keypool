# Execution Profiles & Multi-Key Selection Policy

## 1. Built-In Profiles

KEYPOOL initializes three permanent built-in profiles in SQLite on first run:

- **`testing`:** Default profile for automated test suites, integration verification, and CI/agent test runs.
- **`development`:** Profile for local interactive development and experimentation.
- **`production`:** Profile for production workflows.

## 2. Custom Profiles

Users can create and manage any number of custom profiles:

```bash
keypool profile create staging --description "Pre-production staging environment"
keypool profile assign staging cred_a83f2109 --priority 50
keypool profile show staging
keypool profile unassign staging cred_a83f2109
keypool profile delete staging --yes
```

## 3. Strict Isolation Guarantee

1. **Explicit Assignments Only:** Credentials only belong to a profile when explicitly assigned via `keypool profile assign <profile> <credential_id>` (or `keypool add <provider> --profile <profile>`).
2. **No Cross-Profile Fallback:** When `--profile testing` is requested, `SelectionEngine` queries exclusively credentials assigned to `testing`. If no eligible credential is assigned to `testing`, KEYPOOL immediately raises `CredentialUnavailableError` (exit code `3`)—it will **never** fall back to `development` or `production` credentials.
3. **Zero Implicit Access on Creation:** Newly created custom profiles start with zero assigned credentials.

## 4. Deterministic Multi-Key Selection Policy

When multiple credentials for a provider are assigned to a profile, `SelectionEngine` selects a credential deterministically without random rotation or opaque scoring:

1. **Filter Ineligible Keys:**
   - Exclude disabled credentials (`enabled = False`).
   - Exclude credentials in terminal error states (`auth_failed`, `permission_denied`, `keychain_error`).
   - Exclude credentials currently in an active cooldown (`cooldown_until > now`) or belonging to a `quota_domain` currently in cooldown.
2. **Sort Eligible Candidates:**
   - **Priority (`priority` ascending):** Lower numeric priority values assigned via `--priority` are preferred first (default `100`).
   - **Health Tier:** Verified `healthy` credentials (`0`) are preferred over previously successful credentials (`1`), which are preferred over `untested` credentials (`2`).
   - **Least-Recently-Used (LRU):** Credentials that have never been used (`last_used_at IS NULL`) are selected before older `last_used_at` timestamps.
   - **Creation Timestamp & ID:** Deterministic final tie-breaker.
3. **Keychain Verification:** Confirms the secret is retrievable from macOS Keychain before returning the selected credential.

