"""Model Context Protocol (MCP) server implementation for KEYPOOL (Section 16)."""

from __future__ import annotations

import json
import sys
from typing import Any

from keypool.cli.formatting import RuntimeContext, build_status_summary
from keypool.core.budget_manager import BudgetManager
from keypool.core.model_catalog import ModelCatalogManager
from keypool.core.usage_tracker import UsageTracker
from keypool.security.redaction import redact_data
from keypool.version import APP_NAME, __version__

MCP_TOOLS = [
    {
        "name": "list_providers",
        "description": "List all supported AI providers, supported API endpoints, and capabilities.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_credentials",
        "description": "List configured AI credentials as non-secret metadata (secrets are never returned).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Optional provider filter (e.g. 'openai', 'groq')",
                }
            },
        },
    },
    {
        "name": "get_credential_health",
        "description": "Inspect health states, cooldowns, and recent test results for managed credentials.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Optional provider name filter"}
            },
        },
    },
    {
        "name": "list_models",
        "description": "List models from the local catalog including context windows and token pricing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Optional provider name filter"}
            },
        },
    },
    {
        "name": "search_models",
        "description": "Search the provider model catalog by name or capability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (e.g. 'claude', 'llama', 'reasoning')",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_usage_summary",
        "description": "Query locally observed API executions, token metrics, and rate limit counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time window: 'today', '7d', '30d', or 'all'",
                    "default": "7d",
                }
            },
        },
    },
    {
        "name": "get_budget_status",
        "description": "Check current spending against configured soft and hard budget limits.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_doctor",
        "description": "Run diagnostic checks on macOS Keychain, SQLite, PATH, and permissions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class MCPServer:
    """Stdio JSON-RPC 2.0 Model Context Protocol server."""

    def __init__(self, ctx: RuntimeContext | None = None) -> None:
        self.ctx = ctx or RuntimeContext()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Process incoming JSON-RPC request and return response."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": APP_NAME, "version": __version__},
                    "capabilities": {"tools": {}},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                result_text = self._execute_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Error executing tool '{tool_name}': {exc}"}
                        ],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute tool and return sanitized JSON string."""
        if name == "list_providers":
            providers = [
                {
                    "name": p.name,
                    "display_name": p.display_name,
                    "env_var": p.get_environment_variable(),
                    "base_url": p.base_url,
                    "docs_url": p.docs_url,
                }
                for p in self.ctx.registry.list_providers()
            ]
            return json.dumps(providers, indent=2)

        if name == "list_credentials":
            creds = self.ctx.credential_manager.list_credentials(provider=args.get("provider"))
            return json.dumps([c.to_public_dict() for c in creds], indent=2)

        if name == "get_credential_health":
            snapshots = self.ctx.health_manager.get_health_report(provider=args.get("provider"))
            healthy = sum(1 for s in snapshots if s.state == "healthy")
            failed = sum(
                1
                for s in snapshots
                if s.state in ("auth_failed", "permission_denied", "keychain_error")
            )
            cooldown = sum(1 for s in snapshots if s.state == "rate_limited")
            return json.dumps(
                {
                    "total_credentials": len(snapshots),
                    "healthy": healthy,
                    "failed": failed,
                    "cooldown": cooldown,
                    "snapshots": [s.to_public_dict() for s in snapshots],
                },
                indent=2,
            )

        if name == "list_models":
            mcm = ModelCatalogManager(self.ctx.db)
            models = mcm.list_models(provider=args.get("provider"))
            return json.dumps([m.to_public_dict() for m in models], indent=2)

        if name == "search_models":
            mcm = ModelCatalogManager(self.ctx.db)
            results = mcm.search_models(args.get("query", ""))
            return json.dumps([m.to_public_dict() for m in results], indent=2)

        if name == "get_usage_summary":
            ut = UsageTracker(self.ctx.db)
            u_report = ut.get_usage_report(period=args.get("period", "7d"))
            return json.dumps(
                {
                    "period": u_report.period,
                    "total_selections": u_report.total_selections,
                    "total_executions": u_report.total_executions,
                    "successful_executions": u_report.successful_executions,
                    "failed_executions": u_report.failed_executions,
                    "observed_429_events": u_report.observed_429_events,
                    "observed_tokens": u_report.observed_tokens,
                },
                indent=2,
            )

        if name == "get_budget_status":
            bm = BudgetManager(self.ctx.db)
            budgets = bm.list_budgets()
            return json.dumps([b.to_public_dict() for b in budgets], indent=2)

        if name == "run_doctor":
            summary = build_status_summary(self.ctx)
            return json.dumps(redact_data(summary), indent=2)

        return json.dumps({"error": f"Unknown tool '{name}'"})

    def run_stdio(self) -> None:
        """Run interactive stdio JSON-RPC loop."""
        for line in sys.stdin:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                payload = json.loads(line_str)
                response = self.handle_request(payload)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
            except Exception as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
