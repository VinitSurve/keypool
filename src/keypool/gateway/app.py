"""FastAPI application factory for Local AI Gateway."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from keypool.cli.formatting import RuntimeContext
from keypool.core.model_catalog import ModelCatalogManager
from keypool.gateway.auth import GatewayAuth, create_auth_dependency
from keypool.gateway.forwarder import GatewayForwarder
from keypool.gateway.router import DEFAULT_MODEL_ALIASES, GatewayRouter
from keypool.version import __version__


def create_gateway_app(
    ctx: RuntimeContext | None = None,
    require_auth: bool = True,
    token_override: str | None = None,
) -> FastAPI:
    """Instantiate and configure the FastAPI local gateway application."""
    context = ctx or RuntimeContext()
    auth = GatewayAuth(context.paths, token_override=token_override)
    auth_dep = create_auth_dependency(auth, require_auth=require_auth)

    router = GatewayRouter(context)
    forwarder = GatewayForwarder(context)
    catalog = ModelCatalogManager(context.db)

    app = FastAPI(
        title="Keypool Local AI Gateway",
        description="Unified local OpenAI-compatible API gateway with credential intelligence and failover",
        version=__version__,
    )

    # Enable CORS for local frontends / companion apps
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app_start_time = time.monotonic()

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "name": "Keypool Local AI Gateway",
            "version": __version__,
            "status": "running",
            "openai_compatible": True,
            "docs": "/docs",
        }

    @app.get("/health")
    @app.get("/v1/health")
    async def health_check() -> dict[str, Any]:
        creds = context.credential_manager.list_credentials()
        enabled_creds = [c for c in creds if c.enabled]
        return {
            "status": "healthy",
            "version": __version__,
            "total_credentials": len(creds),
            "enabled_credentials": len(enabled_creds),
            "uptime_seconds": round(time.monotonic() - app_start_time, 2),
        }

    @app.get("/metrics")
    @app.get("/v1/metrics")
    async def metrics_endpoint(_: Any = Depends(auth_dep)) -> dict[str, Any]:
        report = context.usage_tracker.get_usage_report()
        return {
            "version": __version__,
            "uptime_seconds": round(time.monotonic() - app_start_time, 2),
            "usage": report.model_dump(),
        }

    @app.get("/v1/models")
    async def list_models(_: Any = Depends(auth_dep)) -> dict[str, Any]:
        models_list: list[dict[str, Any]] = []

        # Add models from catalog
        cat_models = catalog.list_models()
        seen_ids = set()
        for m in cat_models:
            seen_ids.add(m.model_id)
            models_list.append(
                {
                    "id": m.model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": m.provider,
                    "permission": [],
                    "root": m.model_id,
                    "parent": None,
                }
            )

        # Add aliases
        for alias in DEFAULT_MODEL_ALIASES:
            if alias not in seen_ids:
                seen_ids.add(alias)
                models_list.append(
                    {
                        "id": alias,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "keypool_alias",
                        "permission": [],
                        "root": alias,
                        "parent": None,
                    }
                )

        return {"object": "list", "data": models_list}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        _: Any = Depends(auth_dep),
        x_keypool_profile: str | None = Header(None),
        x_keypool_project: str | None = Header(None),
        x_keypool_strategy: str | None = Header(None),
    ) -> Any:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed JSON request body: {exc}",
            ) from exc

        model = body.get("model")
        if not model or not isinstance(model, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Field 'model' is required and must be a string",
            )

        route = router.resolve(model)
        return await forwarder.forward_chat_completion(
            route=route,
            request_data=body,
            profile=x_keypool_profile,
            project_id=x_keypool_project,
            strategy=x_keypool_strategy,
        )

    @app.post("/v1/embeddings")
    async def embeddings(
        request: Request,
        _: Any = Depends(auth_dep),
        x_keypool_profile: str | None = Header(None),
        x_keypool_project: str | None = Header(None),
    ) -> Any:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed JSON request body: {exc}",
            ) from exc

        model = body.get("model")
        if not model or not isinstance(model, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Field 'model' is required and must be a string",
            )

        route = router.resolve(model)
        return await forwarder.forward_embeddings(
            route=route,
            request_data=body,
            profile=x_keypool_profile,
            project_id=x_keypool_project,
        )

    return app
