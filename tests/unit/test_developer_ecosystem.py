"""Tests for Developer Ecosystem (v2.5): Python SDK, MCP server, plugins, env, and requests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1

from keypool.cli.formatting import RuntimeContext
from keypool.mcp.server import MCPServer
from keypool.plugins.base import ProviderPlugin
from keypool.plugins.manager import PluginManager
from keypool.providers.base import BaseProvider
from keypool.providers.openai_compatible import OpenAICompatibleProvider
from keypool.sdk import AsyncKeypoolClient, KeypoolClient
from keypool.sdk.models import ChatCompletionResponse


def test_sdk_chat_completion_mock(
    runtime_ctx: RuntimeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify KeypoolClient synchronous completion call routes to selected credential."""
    cm = runtime_ctx.credential_manager
    cred = cm.add_credential(provider="groq", name="SDK Test Key", secret=FAKE_GROQ_KEY_1)
    runtime_ctx.profile_manager.assign_credential("testing", cred.id)

    client = KeypoolClient(profile="testing", runtime_context=runtime_ctx)
    adapter = runtime_ctx.registry.get("groq")

    # Mock adapter's _request_with_retry
    def fake_request(*args: object, **kwargs: object) -> tuple[MagicMock, None, float]:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "chatcmpl_test123",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Hello SDK user!"}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return mock_resp, None, 45.0

    monkeypatch.setattr(adapter, "_request_with_retry", fake_request)

    resp = client.chat.completions.create(
        provider="groq",
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": "Hello"}],
    )

    assert isinstance(resp, ChatCompletionResponse)
    assert resp.id == "chatcmpl_test123"
    assert resp.provider == "groq"
    assert resp.choices[0].message.content == "Hello SDK user!"
    assert resp.usage.total_tokens == 15

    # Test get_key and context manager
    with KeypoolClient(profile="testing", runtime_context=runtime_ctx) as cm_client:
        selected_key = cm_client.get_key(provider="groq")
        assert selected_key.secret == FAKE_GROQ_KEY_1
        assert "••••" in selected_key.masked_secret
        assert selected_key.provider == "groq"
        assert selected_key.env_var_name == "GROQ_API_KEY"


@pytest.mark.anyio
async def test_async_sdk_client(runtime_ctx: RuntimeContext) -> None:
    """Verify AsyncKeypoolClient lists models and credentials asynchronously."""
    cm = runtime_ctx.credential_manager
    cred = cm.add_credential(provider="groq", name="Async Key", secret=FAKE_GROQ_KEY_1)
    runtime_ctx.profile_manager.assign_credential("testing", cred.id)

    async with AsyncKeypoolClient(profile="testing", runtime_context=runtime_ctx) as async_client:
        creds = await async_client.list_credentials(provider="groq")
        assert len(creds) >= 1

        models = await async_client.list_models(provider="openai")
        assert len(models) >= 1

        key = await async_client.get_key(provider="groq")
        assert key.secret == FAKE_GROQ_KEY_1
        assert key.provider == "groq"


def test_mcp_server_protocol_and_tools(runtime_ctx: RuntimeContext) -> None:
    """Verify MCP JSON-RPC protocol implementation and safe tool execution."""
    cm = runtime_ctx.credential_manager
    cm.add_credential(provider="groq", name="MCP Key", secret=FAKE_GROQ_KEY_1)

    server = MCPServer(runtime_ctx)

    # 1. Initialize
    init_resp = server.handle_request({"id": 1, "method": "initialize", "params": {}})
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "keypool"

    # 2. Tools list
    tools_resp = server.handle_request({"id": 2, "method": "tools/list", "params": {}})
    assert tools_resp is not None
    tool_names = [t["name"] for t in tools_resp["result"]["tools"]]
    assert "list_providers" in tool_names
    assert "list_credentials" in tool_names
    assert "run_doctor" in tool_names

    # 3. Call tool: list_credentials
    call_resp = server.handle_request(
        {
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_credentials", "arguments": {"provider": "groq"}},
        }
    )
    assert call_resp is not None
    assert call_resp["result"]["isError"] is False
    content_text = call_resp["result"]["content"][0]["text"]
    parsed_creds = json.loads(content_text)
    assert len(parsed_creds) >= 1
    # Secrets must never appear
    assert FAKE_GROQ_KEY_1 not in content_text

    # 4. Call tool: get_usage_summary with 'today'
    usage_resp = server.handle_request(
        {
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_usage_summary", "arguments": {"period": "today"}},
        }
    )
    assert usage_resp is not None
    assert usage_resp["result"]["isError"] is False
    usage_json = json.loads(usage_resp["result"]["content"][0]["text"])
    assert "total_executions" in usage_json

    # 5. Call tool error resilience
    err_resp = server.handle_request(
        {
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_usage_summary", "arguments": {"period": "invalid_period_xyz"}},
        }
    )
    assert err_resp is not None
    assert err_resp["result"]["isError"] is True
    assert "Error executing tool" in err_resp["result"]["content"][0]["text"]


def test_plugin_manager_lifecycle(runtime_ctx: RuntimeContext) -> None:
    """Verify plugin loading, registration, enable/disable, and hook execution."""
    pm = PluginManager(runtime_ctx)

    class CustomTestPlugin(ProviderPlugin):
        id = "unit_test_plugin"
        name = "Unit Test Plugin"
        version = "1.0.0"
        author = "Test"
        description = "Testing plugin manager"

        def get_provider(self) -> BaseProvider:
            return OpenAICompatibleProvider(
                name="test_plugin_prov",
                display_name="Plugin Provider",
                base_url="https://test.plugin.ai/v1",
                env_var_name="TEST_PLUGIN_KEY",
            )

    test_plugin = CustomTestPlugin()
    test_plugin.enabled = False
    pm.register_plugin(test_plugin)

    plugins = pm.list_plugins()
    assert any(p.id == "unit_test_plugin" for p in plugins)
    assert not runtime_ctx.registry.is_registered("test_plugin_prov")

    # Enable plugin
    pm.enable_plugin("unit_test_plugin")
    assert runtime_ctx.registry.is_registered("test_plugin_prov")

    # Disable plugin
    pm.disable_plugin("unit_test_plugin")
    meta = next(p for p in pm.list_plugins() if p.id == "unit_test_plugin")
    assert meta.enabled is False
