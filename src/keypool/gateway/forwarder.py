"""Gateway request forwarder with automatic failover, streaming, and usage tracking."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from keypool.cli.formatting import RuntimeContext
from keypool.core.exceptions import (
    CredentialUnavailableError,
    KeypoolError,
)
from keypool.core.model_catalog import ModelCatalogManager
from keypool.gateway.router import ResolvedRoute
from keypool.providers.anthropic import AnthropicProvider
from keypool.providers.base import ProviderErrorClassification
from keypool.storage.models import HealthState


class GatewayForwarder:
    """Dispatches gateway requests to upstream providers with multi-credential failover."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self.ctx = ctx
        self.catalog = ModelCatalogManager(ctx.db)

    async def forward_chat_completion(
        self,
        route: ResolvedRoute,
        request_data: dict[str, Any],
        *,
        profile: str | None = None,
        project_id: str | None = None,
        strategy: str | None = None,
    ) -> Any:
        """Execute chat completion with automatic fallback across credentials."""
        target_profile = profile
        is_stream = bool(request_data.get("stream", False))
        max_attempts = self.ctx.settings.gateway.max_fallback_attempts

        try:
            candidates = self.ctx.selection_engine.get_ordered_candidates(
                provider=route.provider,
                profile=target_profile,
                strategy=strategy or self.ctx.settings.gateway.default_strategy,
            )
        except (CredentialUnavailableError, KeypoolError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No healthy credential found for provider '{route.provider}': {exc}",
            ) from exc

        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No healthy eligible credentials found for provider '{route.provider}'",
            )

        last_error_detail = "All credentials exhausted"
        last_status_code = status.HTTP_502_BAD_GATEWAY
        attempted_count = 0

        for meta, _prio, _reason in candidates:
            if attempted_count >= max_attempts:
                break
            attempted_count += 1
            cred_id = meta.id

            secret = self.ctx.credential_manager.retrieve_secret(cred_id)
            if not secret:
                continue

            adapter = self.ctx.registry.get(route.provider)
            start_time = time.monotonic()

            try:
                if isinstance(adapter, AnthropicProvider):
                    return await self._dispatch_anthropic_chat(
                        adapter=adapter,
                        secret=secret,
                        cred_id=cred_id,
                        route=route,
                        payload=request_data,
                        profile=target_profile,
                        project_id=project_id,
                        is_stream=is_stream,
                        start_time=start_time,
                    )
                else:
                    return await self._dispatch_openai_chat(
                        adapter=adapter,
                        secret=secret,
                        cred_id=cred_id,
                        route=route,
                        payload=request_data,
                        profile=target_profile,
                        project_id=project_id,
                        is_stream=is_stream,
                        start_time=start_time,
                    )
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    classification = adapter.classify_error(exc.response)
                    last_status_code = exc.response.status_code
                    last_error_detail = exc.response.text
                else:
                    classification = ProviderErrorClassification(
                        state=HealthState.NETWORK_ERROR,
                        error_category="network_error",
                        http_status=None,
                        sanitized_message=str(exc)[:200],
                        retry_after_seconds=5,
                        is_retryable=True,
                    )
                    last_status_code = status.HTTP_502_BAD_GATEWAY
                    last_error_detail = str(exc)

                self.ctx.health_manager.record_runtime_failure(cred_id, classification)
                self.ctx.usage_tracker.record_event(
                    provider=route.provider,
                    event_type="gateway_completion",
                    status="failure",
                    credential_id=cred_id,
                    profile_name=target_profile,
                    model=route.target_model,
                    duration_ms=(time.monotonic() - start_time) * 1000.0,
                    is_rate_limit=(classification.state == HealthState.RATE_LIMITED),
                    http_status=classification.http_status,
                    project_id=project_id,
                )
                continue

        raise HTTPException(
            status_code=last_status_code,
            detail=f"Gateway failed to complete request across {attempted_count} credential(s): {last_error_detail}",
        )

    async def _dispatch_openai_chat(
        self,
        *,
        adapter: Any,
        secret: str,
        cred_id: str,
        route: ResolvedRoute,
        payload: dict[str, Any],
        profile: str | None,
        project_id: str | None,
        is_stream: bool,
        start_time: float,
    ) -> Any:
        url = f"{adapter.base_url.rstrip('/')}/chat/completions"
        headers = adapter._build_headers(secret)
        headers["Content-Type"] = "application/json"

        req_body = dict(payload)
        req_body["model"] = route.target_model

        if is_stream:
            client = httpx.AsyncClient(timeout=60.0)
            req = client.build_request("POST", url, headers=headers, json=req_body)
            resp = await client.send(req, stream=True)
            if resp.status_code != 200:
                await resp.aread()
                resp.raise_for_status()

            async def stream_generator() -> AsyncIterator[bytes]:
                try:
                    async for chunk in resp.aiter_raw():
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()
                    duration_ms = (time.monotonic() - start_time) * 1000.0
                    self.ctx.usage_tracker.record_event(
                        provider=route.provider,
                        event_type="gateway_completion",
                        status="success",
                        credential_id=cred_id,
                        profile_name=profile,
                        model=route.target_model,
                        duration_ms=duration_ms,
                        project_id=project_id,
                    )

            return StreamingResponse(stream_generator(), media_type="text/event-stream")

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=req_body)
            resp.raise_for_status()
            data = resp.json()

        duration_ms = (time.monotonic() - start_time) * 1000.0
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost_usd = self.catalog.estimate_cost(
            provider=route.provider,
            model_id=route.target_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        self.ctx.usage_tracker.record_event(
            provider=route.provider,
            event_type="gateway_completion",
            status="success",
            credential_id=cred_id,
            profile_name=profile,
            model=route.target_model,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            project_id=project_id,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )
        return data

    async def _dispatch_anthropic_chat(
        self,
        *,
        adapter: AnthropicProvider,
        secret: str,
        cred_id: str,
        route: ResolvedRoute,
        payload: dict[str, Any],
        profile: str | None,
        project_id: str | None,
        is_stream: bool,
        start_time: float,
    ) -> Any:
        url = f"{adapter.base_url.rstrip('/')}/v1/messages"
        headers = adapter._build_headers(secret)
        headers["content-type"] = "application/json"

        # Transform OpenAI messages to Anthropic messages
        system_prompt: str | None = None
        anthropic_messages: list[dict[str, Any]] = []
        for msg in payload.get("messages", []):
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_prompt = content
            else:
                anthropic_messages.append({"role": role, "content": content})

        req_body: dict[str, Any] = {
            "model": route.target_model,
            "messages": anthropic_messages,
            "max_tokens": payload.get("max_tokens", 1024),
        }
        if system_prompt:
            req_body["system"] = system_prompt
        if "temperature" in payload:
            req_body["temperature"] = payload["temperature"]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=req_body)
            resp.raise_for_status()
            data = resp.json()

        duration_ms = (time.monotonic() - start_time) * 1000.0
        # Map Anthropic response to OpenAI ChatCompletion structure
        anthropic_usage = data.get("usage", {})
        prompt_tokens = anthropic_usage.get("input_tokens", 0)
        completion_tokens = anthropic_usage.get("output_tokens", 0)

        text_content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")

        openai_response = {
            "id": data.get("id", f"chatcmpl-{int(time.time())}"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": route.target_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": text_content,
                    },
                    "finish_reason": data.get("stop_reason") or "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

        cost_usd = self.catalog.estimate_cost(
            provider=route.provider,
            model_id=route.target_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        self.ctx.usage_tracker.record_event(
            provider=route.provider,
            event_type="gateway_completion",
            status="success",
            credential_id=cred_id,
            profile_name=profile,
            model=route.target_model,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            project_id=project_id,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )
        return openai_response

    async def forward_embeddings(
        self,
        route: ResolvedRoute,
        request_data: dict[str, Any],
        *,
        profile: str | None = None,
        project_id: str | None = None,
    ) -> Any:
        """Execute embeddings forward request."""
        target_profile = profile or self.ctx.settings.default_profile
        selected = self.ctx.selection_engine.select_credential(
            provider=route.provider,
            profile=target_profile,
            record_usage=False,
            verify_keychain=True,
        )
        cred_id = selected.metadata.id
        secret = self.ctx.credential_manager.retrieve_secret(cred_id)
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Secret for credential '{cred_id}' could not be retrieved from Keychain",
            )

        adapter = self.ctx.registry.get(route.provider)
        url = f"{adapter.base_url.rstrip('/')}/embeddings"
        headers = adapter._build_headers(secret)
        headers["Content-Type"] = "application/json"

        req_body = dict(request_data)
        req_body["model"] = route.target_model

        start_time = time.monotonic()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=req_body)
            resp.raise_for_status()
            data = resp.json()

        duration_ms = (time.monotonic() - start_time) * 1000.0
        prompt_tokens = data.get("usage", {}).get("prompt_tokens", 0)

        self.ctx.usage_tracker.record_event(
            provider=route.provider,
            event_type="gateway_embeddings",
            status="success",
            credential_id=cred_id,
            profile_name=target_profile,
            model=route.target_model,
            duration_ms=duration_ms,
            project_id=project_id,
            input_tokens=prompt_tokens,
        )
        return data
