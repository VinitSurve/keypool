"""Asynchronous typed Python SDK for KEYPOOL (Section 15)."""

from __future__ import annotations

from typing import Any

import anyio

from keypool.sdk.client import KeypoolClient
from keypool.sdk.models import ChatCompletionResponse, ChatMessage


class AsyncCompletionsResource:
    """Async chat completions resource."""

    def __init__(self, sync_client: KeypoolClient) -> None:
        self._sync_client = sync_client

    async def create(
        self,
        *,
        messages: list[dict[str, Any]] | list[ChatMessage],
        model: str,
        provider: str | None = None,
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra_kwargs: Any,
    ) -> ChatCompletionResponse:
        """Asynchronously create a chat completion."""
        return await anyio.to_thread.run_sync(
            lambda: self._sync_client.chat.completions.create(
                messages=messages,
                model=model,
                provider=provider,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
                **extra_kwargs,
            )
        )


class AsyncChatResource:
    """Async chat resource."""

    def __init__(self, sync_client: KeypoolClient) -> None:
        self.completions = AsyncCompletionsResource(sync_client)


class AsyncKeypoolClient:
    """Asynchronous client for executing AI requests with intelligent credential routing."""

    def __init__(
        self,
        profile: str | None = None,
        project_dir: str | None = None,
        timeout: float = 30.0,
        runtime_context: Any = None,
    ) -> None:
        self._sync = KeypoolClient(
            profile=profile,
            project_dir=project_dir,
            timeout=timeout,
            runtime_context=runtime_context,
        )
        self.chat = AsyncChatResource(self._sync)

    async def list_models(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(lambda: self._sync.models.list(provider=provider))

    async def list_credentials(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(
            lambda: self._sync.credentials.list(provider=provider)
        )

    async def check_health(self, *, provider: str | None = None) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(lambda: self._sync.health.check(provider=provider))
