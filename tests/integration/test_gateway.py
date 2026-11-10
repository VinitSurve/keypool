"""Integration tests for KEYPOOL Local AI Gateway (v3.0)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.fixtures.provider_payloads import FAKE_GROQ_KEY_1, FAKE_GROQ_KEY_2

from keypool.gateway.app import create_gateway_app
from keypool.gateway.auth import GatewayAuth
from keypool.gateway.router import GatewayRouter
from keypool.storage.models import HealthState


def test_gateway_auth_token_generation_and_validation(runtime_ctx: Any) -> None:
    auth = GatewayAuth(runtime_ctx.paths)
    token = auth.get_or_create_token()
    assert token.startswith("kp_gw_")
    assert auth.validate_token(token) is True
    assert auth.validate_token("invalid_token") is False

    new_token = auth.regenerate_token()
    assert new_token != token
    assert auth.validate_token(new_token) is True
    assert auth.validate_token(token) is False


@pytest.mark.anyio
async def test_gateway_app_endpoints_and_auth(runtime_ctx: Any) -> None:
    token = "test_secret_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Root and health are public
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

        # Protected endpoints without token -> 401
        resp = await client.get("/v1/models")
        assert resp.status_code == 401

        resp = await client.get("/v1/models", headers={"Authorization": "Bearer wrong_token"})
        assert resp.status_code == 401

        # Protected endpoints with valid token -> 200
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/v1/models", headers=headers)
        assert resp.status_code == 200
        models_data = resp.json()
        assert "data" in models_data
        alias_ids = [m["id"] for m in models_data["data"]]
        assert "fast" in alias_ids
        assert "smart" in alias_ids

        # Metrics endpoint
        resp = await client.get("/v1/metrics", headers=headers)
        assert resp.status_code == 200
        assert "usage" in resp.json()


def test_gateway_router_resolves_aliases_and_models(runtime_ctx: Any) -> None:
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

    route_mistral = router.resolve("mistral-large-latest")
    assert route_mistral.provider == "mistral"

    route_deepseek = router.resolve("deepseek-chat")
    assert route_deepseek.provider == "deepseek"


@pytest.mark.anyio
async def test_chat_completions_forward_and_failover(runtime_ctx: Any) -> None:
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

    async def mock_post(client_self: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
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


@pytest.mark.anyio
async def test_gateway_anthropic_streaming_and_translation(runtime_ctx: Any) -> None:
    runtime_ctx.credential_manager.add_credential(
        provider="anthropic",
        name="Claude Key",
        secret="sk-ant-api03-testkey1234567890abcdef1234567890abcdef",
        priority=100,
    )

    token = "test_secret_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)

    recorded_urls: list[str] = []

    # 1. Non-streaming Anthropic call
    anthropic_mock_resp = {
        "id": "msg_01XyZ",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Anthropic response via Keypool"}],
        "model": "claude-3-5-haiku-latest",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }

    orig_post = httpx.AsyncClient.post

    async def mock_post(client_self: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
        url_str = str(url)
        if "api.anthropic.com" in url_str:
            recorded_urls.append(url_str)
            assert "/v1/v1/" not in url_str  # Must not contain double /v1
            req = httpx.Request("POST", url_str)
            return httpx.Response(200, request=req, json=anthropic_mock_resp)
        return await orig_post(client_self, url, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "post", new=mock_post):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "claude-3-5-haiku-latest",
                    "messages": [{"role": "user", "content": "Ping"}],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["choices"][0]["message"]["content"] == "Anthropic response via Keypool"
            assert any(u.endswith("/messages") for u in recorded_urls)

    # 2. Streaming Anthropic call with SSE translation
    anthropic_sse_stream = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123"}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello "}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"World!"}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )

    orig_send = httpx.AsyncClient.send

    async def mock_send(client_self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        url_str = str(request.url)
        if "api.anthropic.com" in url_str:
            assert url_str.endswith("/messages")
            assert "/v1/v1/" not in url_str

            async def aiter_raw() -> Any:
                yield anthropic_sse_stream

            resp = httpx.Response(200, request=request)
            object.__setattr__(resp, "aiter_raw", aiter_raw)
            return resp
        return await orig_send(client_self, request, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "send", new=mock_send):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "claude-3-5-haiku-latest",
                    "messages": [{"role": "user", "content": "Ping"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            stream_body = resp.text
            assert "data: " in stream_body
            assert "Hello " in stream_body
            assert "World!" in stream_body
            assert "data: [DONE]" in stream_body


@pytest.mark.anyio
async def test_gateway_gemini_routing_and_embeddings_failover(runtime_ctx: Any) -> None:
    runtime_ctx.credential_manager.add_credential(
        provider="gemini",
        name="Gemini Key 1",
        secret="AIzaSyA_GeminiTestKey1_1234567890",
        priority=100,
    )
    runtime_ctx.credential_manager.add_credential(
        provider="gemini",
        name="Gemini Key 2",
        secret="AIzaSyA_GeminiTestKey2_0987654321",
        priority=50,
    )

    token = "test_secret_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)

    gemini_chat_urls: list[str] = []
    gemini_auth_headers: list[str] = []

    orig_post = httpx.AsyncClient.post

    async def mock_post(client_self: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
        url_str = str(url)
        if "generativelanguage.googleapis.com" in url_str:
            gemini_chat_urls.append(url_str)
            headers = kwargs.get("headers", {})
            if "Authorization" in headers:
                gemini_auth_headers.append(headers["Authorization"])
            req = httpx.Request("POST", url_str)
            return httpx.Response(
                200,
                request=req,
                json={
                    "id": "chatcmpl-gemini",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gemini-1.5-flash",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "Gemini OK"}}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                },
            )
        return await orig_post(client_self, url, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "post", new=mock_post):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "gemini-1.5-flash",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            assert resp.status_code == 200
            assert any("/openai/chat/completions" in u for u in gemini_chat_urls)
            assert any("Bearer AIzaSyA_" in h for h in gemini_auth_headers)

    # Embeddings failover: first credential returns 429, second succeeds!
    call_count = 0

    async def mock_emb_post(client_self: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
        url_str = str(url)
        if "generativelanguage.googleapis.com" in url_str:
            nonlocal call_count
            call_count += 1
            req = httpx.Request("POST", url_str)
            if call_count == 1:
                resp = httpx.Response(429, request=req, text="Rate limit exceeded")
                raise httpx.HTTPStatusError("Rate limited", request=req, response=resp)
            return httpx.Response(
                200,
                request=req,
                json={
                    "object": "list",
                    "data": [{"object": "embedding", "embedding": [0.1, 0.2, 0.3], "index": 0}],
                    "model": "text-embedding-004",
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                },
            )
        return await orig_post(client_self, url, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "post", new=mock_emb_post):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "gemini/text-embedding-004",
                    "input": "Embed this text",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["data"][0]["embedding"]) == 3
            assert call_count == 2


def test_gateway_cli_commands(isolated_env: Any) -> None:
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


@pytest.mark.anyio
async def test_gateway_streaming_failover_and_client_cleanup(runtime_ctx: Any) -> None:
    """Verify that when the primary credential fails with 429 during streaming initiation,
    the gateway fails over to the backup credential without leaking any httpx.AsyncClient handles."""
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Key Primary Stream",
        secret=FAKE_GROQ_KEY_1,
        priority=100,
    )
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Key Backup Stream",
        secret=FAKE_GROQ_KEY_2,
        priority=50,
    )

    token = "test_stream_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)

    clients_created: list[httpx.AsyncClient] = []
    orig_init = httpx.AsyncClient.__init__

    def track_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        orig_init(self, *args, **kwargs)
        clients_created.append(self)

    call_count = 0
    orig_send = httpx.AsyncClient.send

    async def mock_send(self: httpx.AsyncClient, req: httpx.Request, *args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        if "api.groq.com" in str(req.url):
            call_count += 1
            if call_count == 1:
                r = httpx.Response(429, request=req, text="Rate limit exceeded")
                return r
            async def aiter() -> Any:
                yield b'data: {"choices": [{"delta": {"content": "Streamed OK"}}]}\n\n'
                yield b"data: [DONE]\n\n"
            r = httpx.Response(200, request=req)
            object.__setattr__(r, "aiter_raw", aiter)
            return r
        return await orig_send(self, req, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "__init__", new=track_init), patch.object(
        httpx.AsyncClient, "send", new=mock_send
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "groq/llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            stream_body = resp.text
            assert "Streamed OK" in stream_body
            assert "data: [DONE]" in stream_body
            assert call_count == 2

    # Verify that all upstream streaming AsyncClients have been cleanly closed
    for cl in clients_created:
        assert cl.is_closed, "Leaked unclosed httpx.AsyncClient instance found!"


@pytest.mark.anyio
async def test_gateway_streaming_midstream_disconnection_and_health_degradation(
    runtime_ctx: Any,
) -> None:
    """Verify that when an upstream network disconnect happens midway through SSE streaming,
    the credential health is updated to NETWORK_ERROR, usage failure is recorded, and client receives an error event."""
    runtime_ctx.credential_manager.add_credential(
        provider="groq",
        name="Key Drop Stream",
        secret=FAKE_GROQ_KEY_1,
        priority=100,
    )

    token = "test_stream_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)

    orig_send = httpx.AsyncClient.send

    async def mock_send(self: httpx.AsyncClient, req: httpx.Request, *args: Any, **kwargs: Any) -> Any:
        if "api.groq.com" in str(req.url):
            async def aiter() -> Any:
                yield b'data: {"choices": [{"delta": {"content": "Initial chunk"}}]}\n\n'
                raise httpx.RemoteProtocolError("Peer closed connection unexpectedly")

            r = httpx.Response(200, request=req)
            object.__setattr__(r, "aiter_raw", aiter)
            return r
        return await orig_send(self, req, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "send", new=mock_send):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "groq/llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            stream_text = resp.text
            assert "Initial chunk" in stream_text
            assert "upstream_stream_error" in stream_text

    # Verify credential health was degraded to NETWORK_ERROR
    creds = runtime_ctx.credential_manager.list_credentials(provider="groq")
    snapshot = runtime_ctx.health_manager.get_cached_snapshot(creds[0])
    assert snapshot.state == HealthState.NETWORK_ERROR

    # Verify usage tracker recorded failure
    report = runtime_ctx.usage_tracker.get_usage_report()
    assert report.failed_executions >= 1


@pytest.mark.anyio
async def test_gateway_anthropic_streaming_midstream_disconnection(runtime_ctx: Any) -> None:
    """Verify Anthropic SSE translator degrades health to NETWORK_ERROR on mid-stream disconnection."""
    runtime_ctx.credential_manager.add_credential(
        provider="anthropic",
        name="Claude Stream Key",
        secret="sk-ant-api03-testdisconnect1234567890abcdef1234567890",
        priority=100,
    )

    token = "test_stream_token"
    app = create_gateway_app(runtime_ctx, require_auth=True, token_override=token)

    orig_send = httpx.AsyncClient.send

    async def mock_send(self: httpx.AsyncClient, req: httpx.Request, *args: Any, **kwargs: Any) -> Any:
        if "api.anthropic.com" in str(req.url):
            async def aiter() -> Any:
                yield b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Start"}}\n\n'
                raise httpx.ReadTimeout("Read timed out from Anthropic")

            r = httpx.Response(200, request=req)
            object.__setattr__(r, "aiter_raw", aiter)
            return r
        return await orig_send(self, req, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "send", new=mock_send):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "claude-3-5-haiku-latest",
                    "messages": [{"role": "user", "content": "Ping"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            stream_text = resp.text
            assert "Start" in stream_text
            assert "upstream_stream_error" in stream_text

    creds = runtime_ctx.credential_manager.list_credentials(provider="anthropic")
    snapshot = runtime_ctx.health_manager.get_cached_snapshot(creds[0])
    assert snapshot.state == HealthState.NETWORK_ERROR
