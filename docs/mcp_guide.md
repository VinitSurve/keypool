# KEYPOOL Model Context Protocol (MCP) Guide

KEYPOOL provides a native Model Context Protocol (MCP) stdio server conforming to the MCP Specification. It enables Claude Desktop, Cursor, Zed, and other AI IDEs to query credential health, model catalogs, usage metrics, and isolation profiles safely.

---

## 1. Running the MCP Server

```bash
keypool mcp
```

The server communicates over `stdio` using JSON-RPC 2.0.

---

## 2. Claude Desktop Integration

Add KEYPOOL to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "keypool": {
      "command": "/usr/local/bin/keypool",
      "args": ["mcp"]
    }
  }
}
```

Or if installed inside a virtualenv:

```json
{
  "mcpServers": {
    "keypool": {
      "command": "/Users/vinit/Vinit/Keypool/.venv/bin/keypool",
      "args": ["mcp"]
    }
  }
}
```

---

## 3. Supported MCP Tools

| Tool | Parameters | Description |
| :--- | :--- | :--- |
| `list_credentials` | `provider` (optional) | Lists all configured credentials with health badges and profiles (secrets are NEVER returned). |
| `test_credential` | `credential_id` (required) | Executes a real functional connection probe for a credential. |
| `get_usage_summary` | `period` (`24h`, `7d`, `30d`, `all`) | Returns observed execution count, 429 rate limit events, and token rollups. |
| `list_profiles` | None | Lists all isolation profiles with assigned credential counts. |
| `list_projects` | None | Inspects registered project directories and model alias mappings. |
| `get_gateway_status` | None | Checks if the local AI gateway daemon is running, its port, and PID. |
| `compare_models` | `model_a`, `model_b` | Performs side-by-side comparison of pricing, context windows, and latency. |
| `list_alerts` | None | Returns active alert notifications (rate limits, outages, budget thresholds). |
| `check_rotation_reminders` | `threshold_days` (default `7`) | Scans credentials for impending expiration or overdue rotation. |
