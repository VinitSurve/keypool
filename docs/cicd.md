# KEYPOOL CI/CD Pipeline & Headless Automation Guide

While KEYPOOL is engineered for macOS workstations with native Keychain integration, headless CI/CD runners (GitHub Actions, GitLab CI, Buildkite) can utilize KEYPOOL in software key storage mode.

---

## 1. Headless CI Environment Setup

Set the following environment variables in your CI workflow:

```bash
# Force memory/file fallback secret store in non-GUI Linux / macOS runners
export KEYPOOL_KEYCHAIN_SERVICE="test_keypool"
export KEYPOOL_COLOR="never"
export KEYPOOL_EXEC_ENV_MODE="isolated"
```

---

## 2. GitHub Actions Workflow Example

```yaml
name: AI Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install KEYPOOL
        run: |
          pip install --upgrade pip
          pip install ".[dev]"

      - name: Run Diagnostic Doctor
        run: keypool doctor

      - name: Import CI Ephemeral Bundle
        env:
          CI_KEYPOOL_BUNDLE: ${{ secrets.CI_KEYPOOL_BUNDLE }}
          CI_BUNDLE_PASSPHRASE: ${{ secrets.CI_BUNDLE_PASSPHRASE }}
        run: |
          echo "$CI_KEYPOOL_BUNDLE" | base64 -d > /tmp/bundle.kpb
          echo "$CI_BUNDLE_PASSPHRASE" | keypool import-keys /tmp/bundle.kpb

      - name: Execute Test Suite with Keypool Fallback
        run: |
          keypool exec --profile testing -- pytest tests/ai_integration/
```

---

## 3. Ephemeral Cleanup

To guarantee zero residual secrets after a CI job completes:
```bash
rm -rf ~/.config/keypool
```
