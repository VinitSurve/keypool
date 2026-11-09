"""Synchronous typed Python SDK for KEYPOOL (Section 15)."""

from __future__ import annotations

import time
from typing import Any

from keypool.cli.formatting import RuntimeContext
from keypool.core.exceptions import InvalidArgumentError, ProviderExecutionError
from keypool.core.model_catalog import ModelCatalogManager
from keypool.core.project_manager import ProjectManager
from keypool.sdk.models import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatMessage,
    SelectedKey,
    UsageInfo,
)
from keypool.security.redaction import mask_secret_display


class CompletionsResource:
    """Chat completions resource client."""

    def __init__(self, client: KeypoolClient) -> None:
        self._client = client

    def create(
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
        """Create a chat completion using the optimal healthy credential for the provider."""
        resolved_provider, resolved_model = self._client._resolve_provider_and_model(
            provider, model
        )
        selected = self._client._runtime_ctx.selection_engine.select_credential(
            provider=resolved_provider,
            profile=self._client.profile,
            record_usage=True,
        )
        secret = self._client._runtime_ctx.credential_manager.retrieve_secret(selected.metadata.id)
        adapter = self._client._runtime_ctx.registry.get(resolved_provider)

        norm_messages: list[dict[str, Any]] = [
            m.model_dump() if isinstance(m, ChatMessage) else dict(m) for m in messages
        ]

        start_time = time.perf_counter()
        headers = adapter._build_headers(secret)

        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": norm_messages,
            **extra_kwargs,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Check adapter API type
        meta = self._client._runtime_ctx.registry.get_metadata(resolved_provider)
        api_type = meta.api_type.value if meta else "openai_compatible"

        if api_type == "anthropic":
            endpoint = f"{adapter.base_url}/messages"
            # Translate to Anthropic payload
            anthropic_payload: dict[str, Any] = {
                "model": resolved_model,
                "max_tokens": max_tokens or 1024,
                "messages": [m for m in norm_messages if m.get("role") != "system"],
            }
            sys_msg = next((m["content"] for m in norm_messages if m.get("role") == "system"), None)
            if sys_msg:
                anthropic_payload["system"] = sys_msg

            resp, exc, _ = adapter._request_with_retry(
                "POST", endpoint, headers=headers, json_body=anthropic_payload
            )
        else:
            # Default OpenAI-compatible /chat/completions endpoint
            endpoint = f"{adapter.base_url}/chat/completions"
            resp, exc, _ = adapter._request_with_retry(
                "POST", endpoint, headers=headers, json_body=payload
            )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if exc is not None or resp is None or resp.status_code != 200:
            classification = adapter.classify_error(resp, exc)
            self._client._runtime_ctx.usage_tracker.record_event(
                provider=resolved_provider,
                credential_id=selected.metadata.id,
                profile_name=self._client.profile,
                event_type="sdk_chat_completion",
                status="failure",
                http_status=resp.status_code if resp else None,
                model=resolved_model,
                duration_ms=duration_ms,
                summary=f"SDK call failed: {classification.sanitized_message}",
            )
            raise ProviderExecutionError(
                f"{adapter.display_name} API call failed (HTTP {resp.status_code if resp else 'N/A'}): {classification.sanitized_message}",
                hint=f"Check provider status at {adapter.docs_url}.",
            )

        body = resp.json()
        now_ts = int(time.time())

        # Normalize choices & usage
        choices: list[ChatCompletionChoice] = []
        usage_info = UsageInfo()

        if api_type == "anthropic":
            content_blocks = body.get("content", [])
            text_val = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            choices.append(
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text_val),
                    finish_reason=body.get("stop_reason") or "stop",
                )
            )
            u = body.get("usage", {})
            in_t = int(u.get("input_tokens") or 0)
            out_t = int(u.get("output_tokens") or 0)
            usage_info = UsageInfo(
                prompt_tokens=in_t,
                completion_tokens=out_t,
                total_tokens=in_t + out_t,
            )
        else:
            raw_choices = body.get("choices", [])
            for rc in raw_choices:
                msg_dict = rc.get("message", {})
                choices.append(
                    ChatCompletionChoice(
                        index=rc.get("index", 0),
                        message=ChatMessage(
                            role=msg_dict.get("role", "assistant"),
                            content=msg_dict.get("content"),
                        ),
                        finish_reason=rc.get("finish_reason") or "stop",
                    )
                )
            u = body.get("usage", {})
            in_t = int(u.get("prompt_tokens") or 0)
            out_t = int(u.get("completion_tokens") or 0)
            usage_info = UsageInfo(
                prompt_tokens=in_t,
                completion_tokens=out_t,
                total_tokens=in_t + out_t,
            )

        # Estimate cost
        mcm = ModelCatalogManager(self._client._runtime_ctx.db)
        cost_est = mcm.estimate_cost(
            provider=resolved_provider,
            model_id=resolved_model,
            input_tokens=usage_info.prompt_tokens,
            output_tokens=usage_info.completion_tokens,
        )
        usage_info.estimated_cost_usd = cost_est

        # Record usage
        self._client._runtime_ctx.usage_tracker.record_event(
            provider=resolved_provider,
            credential_id=selected.metadata.id,
            profile_name=self._client.profile,
            event_type="sdk_chat_completion",
            status="success",
            http_status=200,
            model=resolved_model,
            observed_tokens=usage_info.total_tokens,
            cost_usd=cost_est,
            duration_ms=duration_ms,
            summary=f"SDK completion via {resolved_model} ({usage_info.total_tokens} tokens)",
        )

        return ChatCompletionResponse(
            id=body.get("id") or f"chatcmpl_{int(time.time() * 1000)}",
            created=body.get("created") or now_ts,
            model=resolved_model,
            provider=resolved_provider,
            choices=choices,
            usage=usage_info,
        )


class ChatResource:
    """Chat API sub-resource."""

    def __init__(self, client: KeypoolClient) -> None:
        self.completions = CompletionsResource(client)


class ModelsResource:
    """Models inspection resource."""

    def __init__(self, client: KeypoolClient) -> None:
        self._client = client

    def list(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        """List models available across providers in catalog."""
        mcm = ModelCatalogManager(self._client._runtime_ctx.db)
        models = mcm.list_models(provider=provider)
        return [m.to_public_dict() for m in models]


class CredentialsResource:
    """Credential metadata inspection resource."""

    def __init__(self, client: KeypoolClient) -> None:
        self._client = client

    def list(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        """List managed credentials (metadata only; never secrets)."""
        creds = self._client._runtime_ctx.credential_manager.list_credentials(provider=provider)
        return [c.to_public_dict() for c in creds]


class HealthResource:
    """Provider health checking resource."""

    def __init__(self, client: KeypoolClient) -> None:
        self._client = client

    def check(self, *, provider: str | None = None) -> dict[str, Any]:
        """Inspect health status for configured provider credentials."""
        hm = self._client._runtime_ctx.health_manager
        snapshots = hm.get_health_report(provider=provider)
        healthy = sum(1 for s in snapshots if s.state == "healthy")
        failed = sum(
            1
            for s in snapshots
            if s.state in ("auth_failed", "permission_denied", "keychain_error")
        )
        cooldown = sum(1 for s in snapshots if s.state == "rate_limited")
        return {
            "total_credentials": len(snapshots),
            "healthy_count": healthy,
            "failed_count": failed,
            "cooldown_count": cooldown,
            "details": [s.to_public_dict() for s in snapshots],
        }


class KeypoolClient:
    """Unified client for executing AI requests across providers with intelligent credential routing."""

    def __init__(
        self,
        profile: str | None = None,
        project_dir: str | None = None,
        timeout: float = 30.0,
        runtime_context: RuntimeContext | None = None,
    ) -> None:
        self._runtime_ctx = runtime_context or RuntimeContext()
        self.timeout = timeout
        pm = ProjectManager(self._runtime_ctx.db)
        self.project_context = (
            pm.discover_project(project_dir) if project_dir else pm.discover_project()
        )
        self.profile = profile or (
            self.project_context.profile_name if self.project_context else "development"
        )

        self.chat = ChatResource(self)
        self.models = ModelsResource(self)
        self.credentials = CredentialsResource(self)
        self.health = HealthResource(self)

    def __enter__(self) -> KeypoolClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Release underlying resources."""
        self._runtime_ctx.close()

    def get_key(
        self,
        provider: str,
        *,
        profile: str | None = None,
        strategy: str | None = None,
    ) -> SelectedKey:
        """Select and retrieve the optimal active API credential for a provider."""
        target_profile = profile or self.profile
        selected = self._runtime_ctx.selection_engine.select_credential(
            provider=provider,
            profile=target_profile,
            strategy=strategy,
            record_usage=True,
        )
        secret = self._runtime_ctx.credential_manager.retrieve_secret(selected.metadata.id)
        return SelectedKey(
            credential_id=selected.metadata.id,
            provider=selected.metadata.provider,
            secret=secret,
            masked_secret=mask_secret_display(secret),
            env_var_name=selected.env_var_name,
            profile=target_profile,
            selection_reason=selected.selection_reason,
        )

    def _resolve_provider_and_model(self, provider: str | None, model: str) -> tuple[str, str]:
        """Infer or map provider and model according to aliases and project context."""
        clean_model = model.strip()

        # Check project model aliases first
        if self.project_context and clean_model in self.project_context.model_aliases:
            clean_model = self.project_context.model_aliases[clean_model]

        if provider:
            return provider.strip().lower(), clean_model

        # If model includes provider prefix like "groq/llama-3.1-8b-instant"
        if "/" in clean_model:
            parts = clean_model.split("/", 1)
            prov_cand = parts[0].strip().lower()
            if self._runtime_ctx.registry.is_registered(prov_cand):
                return prov_cand, parts[1]

        # Infer from known model prefixes
        lower = clean_model.lower()
        if (
            lower.startswith("gpt-")
            or lower.startswith("o1")
            or lower.startswith("o3")
            or lower.startswith("text-embedding")
        ):
            return "openai", clean_model
        if lower.startswith("claude"):
            return "anthropic", clean_model
        if lower.startswith("gemini"):
            return "gemini", clean_model
        if lower.startswith("llama") or "instant" in lower:
            return "groq", clean_model
        if lower.startswith("mistral"):
            return "mistral", clean_model
        if lower.startswith("deepseek"):
            return "deepseek", clean_model
        if lower.startswith("command"):
            return "cohere", clean_model

        raise InvalidArgumentError(
            f"Cannot infer provider for model '{model}'. Please specify the 'provider' argument explicitly (e.g. provider='openai')."
        )
