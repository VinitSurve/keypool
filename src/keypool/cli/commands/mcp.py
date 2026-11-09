"""CLI entry point for running the Model Context Protocol (MCP) server (Section 16)."""

from __future__ import annotations

from keypool.cli.formatting import RuntimeContext
from keypool.mcp.server import MCPServer


def cmd_mcp() -> None:
    """Run the KEYPOOL Model Context Protocol (MCP) server over stdio."""
    ctx = RuntimeContext()
    server = MCPServer(ctx)
    server.run_stdio()
