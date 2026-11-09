"""Integration tests for KEYPOOL Local AI Gateway (v3.0)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from starlette.testclient import TestClient
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1, FAKE_GROQ_KEY_2

from keypool.gateway.app import create_gateway_app
from keypool.gateway.auth import GatewayAuth
from keypool.gateway.router import GatewayRouter


def test_gateway_auth_token_generation_and_validation(runtime_ctx):
    auth = GatewayAuth(runtime_ctx.paths)
    token = auth.get_or_create_token()
    assert token.startswith("kp_gw_")
    assert auth.validate_token(token) is True
    assert auth.validate_token("invalid_token") is False

    new_token = auth.regenerate_token()
    assert new_token != token
    assert auth.validate_token(new_token) is True
    assert auth.validate_token(token) is False


def test_gateway_app_endpoints_and_auth(runtime_ctx):
    token = "test_secret_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)
    client = TestClient(app)

    # Root and health are public
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

    # Protected endpoints without token -> 401
    resp = client.get("/v1/models")
    assert resp.status_code == 401

    resp = client.get("/v1/models", headers={"Authorization": "Bearer wrong_token"})
    assert resp.status_code == 401

    # Protected endpoints with valid token -> 200
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/v1/models", headers=headers)
    assert resp.status_code == 200
    models_data = resp.json()
    assert "data" in models_data
    alias_ids = [m["id"] for m in models_data["data"]]
    assert "fast" in alias_ids
    assert "smart" in alias_ids

    # Metrics endpoint
    resp = client.get("/v1/metrics", headers=headers)
    assert resp.status_code == 200
    assert "usage" in resp.json()


def test_gateway_router_resolves_aliases_and_models(runtime_ctx):
    router = GatewayRouter(runtime_ctx)

    route_fast = router.resolve("fast")
    assert route_fast.provider in ("groq", "cerebras")
    assert route_fast.alias_used == "fast"

    route_prefix = router.resolve("groq/llama-3.3-70b-versatile")
    assert route_prefix.provider == "groq"
    assert route_prefix.target_model == "llama-3.3-70b-versatile"

    route_claude = router.resolve("claude-3-5-sonnet-20241022")
    assert route_claude.provider == "anthropic"

    route_gpt = router.resolve("gpt-4o")
    assert route_gpt.provider == "openai"


@pytest.mark.anyio
async def test_chat_completions_forward_and_failover(runtime_ctx):
    # Add two credentials for groq
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Key Primary",
        secret=FAKE_GROQ_KEY_1,
        priority=100,
    )
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Key Backup",
        secret=FAKE_GROQ_KEY_2,
        priority=50,
    )

    token = "test_secret_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)

    mock_chat_response = {
        "id": "chatcmpl-test12345",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from Keypool Gateway!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 8,
            "total_tokens": 23,
        },
    }

    # Simulate: first call returns 429 Rate Limit on Primary, second call succeeds on Backup!
    call_count = 0
    orig_post = httpx.AsyncClient.post

    async def mock_post(client_self, url, *args, **kwargs):
        nonlocal call_count
        if "api.groq.com" in str(url):
            call_count += 1
            if call_count == 1:
                req = httpx.Request("POST", str(url))
                resp = httpx.Response(429, request=req, text="Rate limit exceeded")
                raise httpx.HTTPStatusError("Rate limited", request=req, response=resp)
            else:
                req = httpx.Request("POST", str(url))
                return httpx.Response(200, request=req, json=mock_chat_response)
        return await orig_post(client_self, url, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "post", new=mock_post):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "groq/llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["choices"][0]["message"]["content"] == "Hello from Keypool Gateway!"
            assert call_count == 2

    # Verify that cred1 was flagged with failure/rate limit, while usage was recorded
    report = runtime_ctx.usage_tracker.get_usage_report()
    assert report.total_executions >= 2
    assert report.failed_executions >= 1
    assert report.successful_executions >= 1


def test_gateway_cli_commands(isolated_env):
    from typer.testing import CliRunner

    from keypool.cli.app import app

    runner = CliRunner()

    res = runner.invoke(app, ["gateway", "status"])
    assert res.exit_code == 0
    assert "Gateway is stopped" in res.stdout

    res_tok = runner.invoke(app, ["gateway", "token"])
    assert res_tok.exit_code == 0
    assert "Active Gateway Token:" in res_tok.stdout

    res_regen = runner.invoke(app, ["gateway", "token", "--regenerate"])
    assert res_regen.exit_code == 0
    assert "New Gateway Token generated:" in res_regen.stdout
