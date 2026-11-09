# KEYPOOL Interactive Terminal User Interface (TUI)

KEYPOOL features a full-featured 12-tab terminal dashboard built using Rich and Textual primitives.

---

## 1. Launching the TUI

```bash
# Launch interactive overview
keypool tui

# Jump directly to a specific operational view
keypool tui --view health
keypool tui --view gateway
keypool tui --view models
```

---

## 2. Available Views

| View Name | Description | Key Display Elements |
| :--- | :--- | :--- |
| `overview` | System executive summary | Provider counts, healthy/cooldown/error key counts, recent activity log. |
| `credentials`| Credential inventory | Full list of keys, providers, quota domains, priority tags, and health state. |
| `providers` | Adapter capabilities | Catalog matrix, supported modalities, base URLs, and auth types. |
| `health` | Real-time health matrix | Consecutive failure counters, cooldown timers, and probe latencies. |
| `models` | Global model catalog | Discovered models, context window limits, pricing per 1M tokens, modalities. |
| `profiles` | Isolation profiles | Testing, development, production, custom profiles, and assigned credentials. |
| `projects` | Local project roots | Discovered Git repository roots, project profiles, and model alias mappings. |
| `usage` | Local activity tracking | Observed executions, 429 backoff events, token metrics, and billing notice. |
| `budgets` | Budget governance | Spend ceilings (daily, weekly, monthly), spend forecasting, and alert thresholds. |
| `gateway` | Local AI Gateway | Listener status, port, auth token status, routing mode, and upstream targets. |
| `settings` | Config inspector | Active timeouts, retries, log level, keychain service, and env mode. |
| `diagnostics`| Doctor snapshot | SQLite WAL status, Keychain access, directory permissions, schema version. |

---

## 3. Keyboard & CLI Navigation

Use the `--view` flag or arrow keys inside the terminal interface to cycle through tabs. All data is rendered from live SQLite state and requires zero external network calls to display cached metrics.
