"""Model catalog and capability intelligence management (Section 10)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from keypool.storage.database import DatabaseManager
from keypool.storage.models import CatalogModelRecord, utc_now_iso


class CatalogModelInfo(BaseModel):
    """Metadata describing a model's capabilities, context, and pricing."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model_id: str
    display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    is_deprecated: bool = False
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "modalities": list(self.modalities),
            "input_price_per_1m": self.input_price_per_1m,
            "output_price_per_1m": self.output_price_per_1m,
            "is_deprecated": self.is_deprecated,
            "updated_at": self.updated_at,
        }


class ModelCatalogManager:
    """Manages multi-provider model catalog and cost estimation."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    @classmethod
    def _record_to_info(cls, rec: CatalogModelRecord) -> CatalogModelInfo:
        mods = [m.strip() for m in (rec.modalities or "text").split(",") if m.strip()]
        return CatalogModelInfo(
            provider=rec.provider,
            model_id=rec.model_id,
            display_name=rec.display_name,
            context_window=rec.context_window,
            max_output_tokens=rec.max_output_tokens,
            modalities=mods,
            input_price_per_1m=rec.input_price_per_1m,
            output_price_per_1m=rec.output_price_per_1m,
            is_deprecated=rec.is_deprecated,
            updated_at=rec.updated_at,
        )

    def list_models(self, *, provider: str | None = None) -> list[CatalogModelInfo]:
        """List catalog models optionally filtered by provider."""
        with self._db.session_scope() as session:
            stmt = select(CatalogModelRecord).order_by(
                CatalogModelRecord.provider, CatalogModelRecord.model_id
            )
            if provider:
                stmt = stmt.where(CatalogModelRecord.provider == provider.strip().lower())
            records = session.execute(stmt).scalars().all()
            return [self._record_to_info(r) for r in records]

    def get_model(self, provider: str, model_id: str) -> CatalogModelInfo | None:
        """Fetch model metadata by provider and model ID."""
        with self._db.session_scope() as session:
            rec = session.execute(
                select(CatalogModelRecord).where(
                    CatalogModelRecord.provider == provider.strip().lower(),
                    CatalogModelRecord.model_id == model_id.strip(),
                )
            ).scalar_one_or_none()
            if rec is None:
                return None
            return self._record_to_info(rec)

    def search_models(self, query: str) -> list[CatalogModelInfo]:
        """Search catalog by model ID or display name."""
        clean = f"%{query.strip().lower()}%"
        with self._db.session_scope() as session:
            stmt = (
                select(CatalogModelRecord)
                .where(
                    or_(
                        CatalogModelRecord.model_id.ilike(clean),
                        CatalogModelRecord.display_name.ilike(clean),
                        CatalogModelRecord.provider.ilike(clean),
                    )
                )
                .order_by(CatalogModelRecord.provider, CatalogModelRecord.model_id)
            )
            records = session.execute(stmt).scalars().all()
            return [self._record_to_info(r) for r in records]

    def upsert_model(
        self,
        *,
        provider: str,
        model_id: str,
        display_name: str,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        modalities: str = "text",
        input_price_per_1m: float | None = None,
        output_price_per_1m: float | None = None,
        is_deprecated: bool = False,
    ) -> CatalogModelInfo:
        """Add or update a model record in the catalog."""
        now = utc_now_iso()
        clean_prov = provider.strip().lower()
        clean_mid = model_id.strip()
        with self._db.session_scope() as session:
            existing = session.execute(
                select(CatalogModelRecord).where(
                    CatalogModelRecord.provider == clean_prov,
                    CatalogModelRecord.model_id == clean_mid,
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.display_name = display_name.strip()
                existing.context_window = context_window
                existing.max_output_tokens = max_output_tokens
                existing.modalities = modalities
                existing.input_price_per_1m = input_price_per_1m
                existing.output_price_per_1m = output_price_per_1m
                existing.is_deprecated = is_deprecated
                existing.updated_at = now
                session.flush()
                rec = existing
            else:
                rec = CatalogModelRecord(
                    provider=clean_prov,
                    model_id=clean_mid,
                    display_name=display_name.strip(),
                    context_window=context_window,
                    max_output_tokens=max_output_tokens,
                    modalities=modalities,
                    input_price_per_1m=input_price_per_1m,
                    output_price_per_1m=output_price_per_1m,
                    is_deprecated=is_deprecated,
                    updated_at=now,
                )
                session.add(rec)
                session.flush()

            return self._record_to_info(rec)

    def estimate_cost(
        self,
        *,
        provider: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float | None:
        """Estimate request cost in USD based on observed or reported token usage."""
        model_info = self.get_model(provider, model_id)
        if model_info is None or model_info.input_price_per_1m is None:
            return None

        inp_cost = (input_tokens / 1_000_000.0) * model_info.input_price_per_1m
        out_price = model_info.output_price_per_1m or model_info.input_price_per_1m
        out_cost = (output_tokens / 1_000_000.0) * out_price
        return round(inp_cost + out_cost, 6)
