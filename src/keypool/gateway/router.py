"""Routing and model alias resolution for Local AI Gateway."""

from __future__ import annotations

from typing import NamedTuple

from keypool.cli.formatting import RuntimeContext
from keypool.core.alias_manager import ModelAliasManager
from keypool.core.model_catalog import ModelCatalogManager

# Default model aliases mapping to candidate provider/model targets
DEFAULT_MODEL_ALIASES: dict[str, list[str]] = {
    "fast": [
        "groq/llama-3.3-70b-versatile",
        "cerebras/llama3.1-8b",
        "groq/llama-3.1-8b-instant",
    ],
    "smart": [
        "anthropic/claude-3-5-sonnet-20241022",
        "openai/gpt-4o",
        "gemini/gemini-1.5-pro",
    ],
    "coding": [
        "anthropic/claude-3-5-sonnet-20241022",
        "deepseek/deepseek-coder",
        "openai/gpt-4o",
    ],
    "reasoning": [
        "openai/o1",
        "deepseek/deepseek-reasoner",
        "openai/o1-mini",
    ],
    "cheap": [
        "groq/llama-3.1-8b-instant",
        "openai/gpt-4o-mini",
        "gemini/gemini-1.5-flash",
    ],
}


class ResolvedRoute(NamedTuple):
    provider: str
    target_model: str
    original_model: str
    alias_used: str | None = None


class GatewayRouter:
    """Resolves incoming model requests to eligible provider and model targets."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self.ctx = ctx
        self.catalog = ModelCatalogManager(ctx.db)
        self.alias_mgr = ModelAliasManager(ctx.db)

    def resolve(self, requested_model: str) -> ResolvedRoute:
        """Resolve a requested model string or alias into a provider and target model."""
        model_name = requested_model.strip()
        alias_used: str | None = None

        # 1. Check for alias expansion in database
        db_alias = self.alias_mgr.get_alias(model_name)
        if db_alias:
            alias_used = model_name
            model_name = db_alias["target"]
        elif model_name.lower() in DEFAULT_MODEL_ALIASES:
            alias_used = model_name.lower()
            candidates = DEFAULT_MODEL_ALIASES[alias_used]
            # Select first candidate whose provider has credentials in DB
            creds = self.ctx.credential_manager.list_credentials()
            available_providers = {c.provider for c in creds if c.enabled}

            chosen_candidate = candidates[0]
            for cand in candidates:
                cand_provider = cand.split("/")[0]
                if cand_provider in available_providers:
                    chosen_candidate = cand
                    break
            model_name = chosen_candidate

        # 2. Check for explicit prefix "provider/model"
        if "/" in model_name:
            provider, target_model = model_name.split("/", 1)
            provider = provider.strip().lower()
            target_model = target_model.strip()
            return ResolvedRoute(
                provider=provider,
                target_model=target_model,
                original_model=requested_model,
                alias_used=alias_used,
            )

        # 3. Model catalog query
        catalog_matches = self.catalog.search_models(query=model_name)
        for cat_item in catalog_matches:
            if cat_item.model_id.lower() == model_name.lower():
                return ResolvedRoute(
                    provider=cat_item.provider,
                    target_model=cat_item.model_id,
                    original_model=requested_model,
                    alias_used=alias_used,
                )

        # 4. Heuristic provider matching based on model prefix
        lower_name = model_name.lower()
        if lower_name.startswith("claude"):
            return ResolvedRoute("anthropic", model_name, requested_model, alias_used)
        if lower_name.startswith(("gpt", "o1", "o3", "text-embedding")):
            return ResolvedRoute("openai", model_name, requested_model, alias_used)
        if lower_name.startswith("gemini"):
            return ResolvedRoute("gemini", model_name, requested_model, alias_used)
        if lower_name.startswith("command"):
            return ResolvedRoute("cohere", model_name, requested_model, alias_used)
        if "llama" in lower_name or "mixtral" in lower_name:
            # Check if groq has credentials
            creds = self.ctx.credential_manager.list_credentials(provider="groq")
            if creds:
                return ResolvedRoute("groq", model_name, requested_model, alias_used)

        # 5. Fallback to active credentials or preferred provider
        active_creds = [c for c in self.ctx.credential_manager.list_credentials() if c.enabled]
        if active_creds:
            provider = active_creds[0].provider
            return ResolvedRoute(provider, model_name, requested_model, alias_used)

        default_provider = self.ctx.settings.providers.preferred_provider or "groq"
        return ResolvedRoute(default_provider, model_name, requested_model, alias_used)
