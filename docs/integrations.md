# Integrations Guide

KEYPOOL is designed as a standalone, consumer-independent credential manager. Downstream tools, test suites, and scripts integrate with KEYPOOL through `keypool exec`, `keypool get`, and `--json` outputs.

---

## 1. Python Scripts (`keypool exec`)

Your Python application reads standard SDK environment variables (`GROQ_API_KEY` or `GEMINI_API_KEY`) without importing KEYPOOL or managing `.env` files:

```python
# app.py
import os

def main() -> None:
    groq_key = os.environ["GROQ_API_KEY"]
    print(f"Loaded Groq key from environment (length={len(groq_key)})")

if __name__ == "__main__":
    main()
```

Run with KEYPOOL:

```bash
keypool exec --profile development --provider groq -- python app.py
```

---

## 2. Pytest Integration Suites

Run `pytest` with an isolated `testing` profile credential injected only for the test run:

```bash
# Run Groq integration tests
keypool exec --profile testing --provider groq -- pytest tests/integration/test_groq_client.py

# Run Google Gemini integration tests with strict environment inheritance
keypool exec --profile testing --provider gemini --env-mode controlled -- pytest tests/integration/
```

---

## 3. Antigravity-Launched Processes & Automation

Automated agents and CI/local runners can query KEYPOOL's machine-readable JSON contract prior to launching child tasks:

```bash
# Inspect available healthy credentials in JSON
keypool health --provider groq --json

# Check profile assignments in JSON
keypool profile show testing --json

# Execute agent task with testing profile
keypool exec --profile testing --provider gemini -- python agent_task.py
```

---

## 4. Independent CLI Tools

Wrap any third-party CLI tool or shell utility that expects `GROQ_API_KEY` or `GEMINI_API_KEY`:

```bash
keypool exec --profile development --provider gemini -- my-ai-cli summarize README.md
```

---

## 5. RPX (Repository Project Explorer) as an Optional Consumer

KEYPOOL has **zero dependency on RPX**, but RPX (or any similar CLI tool installed via `pipx`) can consume credentials managed by KEYPOOL seamlessly:

```bash
# Launch RPX with a Groq credential from the development profile
keypool exec --profile development --provider groq -- rpx analyze .

# Or launch RPX with a Google Gemini credential
keypool exec --profile development --provider gemini -- rpx analyze .
```

