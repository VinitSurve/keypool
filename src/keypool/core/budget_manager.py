"""Budget management, threshold evaluation, and cost controls (Section 13)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from keypool.core.exceptions import InvalidArgumentError
from keypool.storage.database import DatabaseManager
from keypool.storage.models import BudgetRecord, UsageEventRecord, utc_now_iso


class BudgetStatus(BaseModel):
    """Evaluation result for a configured budget."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scope_type: str
    scope_target: str | None = None
    amount_limit: float
    currency: str
    period: str
    current_spend: float
    percent_used: float
    is_warning: bool
    is_exceeded: bool
    hard_limit_enabled: bool

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scope_type": self.scope_type,
            "scope_target": self.scope_target,
            "amount_limit": self.amount_limit,
            "currency": self.currency,
            "period": self.period,
            "current_spend": round(self.current_spend, 4),
            "percent_used": round(self.percent_used, 1),
            "is_warning": self.is_warning,
            "is_exceeded": self.is_exceeded,
            "hard_limit_enabled": self.hard_limit_enabled,
        }


def _get_period_cutoff(period: str) -> str:
    now = datetime.now(UTC)
    clean = period.lower().strip()
    if clean == "daily":
        cutoff = now - timedelta(days=1)
    elif clean == "weekly":
        cutoff = now - timedelta(days=7)
    elif clean == "monthly":
        cutoff = now - timedelta(days=30)
    else:
        cutoff = now - timedelta(days=30)
    return cutoff.isoformat(timespec="microseconds")


class BudgetManager:
    """Evaluates and enforces observable usage budgets across scopes."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def set_budget(
        self,
        *,
        name: str,
        amount_limit: float,
        scope_type: str = "global",
        scope_target: str | None = None,
        currency: str = "USD",
        period: str = "monthly",
        soft_limit_percent: float = 80.0,
        hard_limit_enabled: bool = False,
    ) -> BudgetStatus:
        """Create or update a budget rule."""
        clean_name = name.strip()
        if not clean_name:
            raise InvalidArgumentError("Budget name cannot be empty.")
        if amount_limit <= 0:
            raise InvalidArgumentError("Budget limit must be greater than zero.")

        valid_scopes = ("global", "provider", "project", "profile", "model")
        clean_scope = scope_type.strip().lower()
        if clean_scope not in valid_scopes:
            raise InvalidArgumentError(
                f"Invalid budget scope '{scope_type}'. Must be one of: {', '.join(valid_scopes)}."
            )

        clean_period = period.strip().lower()
        if clean_period not in ("daily", "weekly", "monthly"):
            raise InvalidArgumentError("Budget period must be 'daily', 'weekly', or 'monthly'.")

        with self._db.session_scope() as session:
            existing = session.execute(
                select(BudgetRecord).where(
                    BudgetRecord.scope_type == clean_scope,
                    BudgetRecord.scope_target == (scope_target.strip() if scope_target else None),
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.name = clean_name
                existing.amount_limit = float(amount_limit)
                existing.currency = currency.strip().upper()
                existing.period = clean_period
                existing.soft_limit_percent = float(soft_limit_percent)
                existing.hard_limit_enabled = hard_limit_enabled
                rec = existing
            else:
                bid = f"bgt_{secrets.token_hex(4)}"
                rec = BudgetRecord(
                    id=bid,
                    name=clean_name,
                    scope_type=clean_scope,
                    scope_target=scope_target.strip() if scope_target else None,
                    amount_limit=float(amount_limit),
                    currency=currency.strip().upper(),
                    period=clean_period,
                    soft_limit_percent=float(soft_limit_percent),
                    hard_limit_enabled=hard_limit_enabled,
                    created_at=utc_now_iso(),
                )
                session.add(rec)
                session.flush()

            budget_id = rec.id

        return self.evaluate_budget(budget_id)

    def list_budgets(self) -> list[BudgetStatus]:
        """List and evaluate all configured budgets."""
        with self._db.session_scope() as session:
            records = session.execute(select(BudgetRecord)).scalars().all()
            bids = [r.id for r in records]

        return [self.evaluate_budget(bid) for bid in bids]

    def evaluate_budget(self, budget_id: str) -> BudgetStatus:
        """Evaluate current period spend against budget limits."""
        with self._db.session_scope() as session:
            rec = session.get(BudgetRecord, budget_id)
            if rec is None:
                raise InvalidArgumentError(f"Budget '{budget_id}' not found.")

            cutoff = _get_period_cutoff(rec.period)
            query = select(func.coalesce(func.sum(UsageEventRecord.cost_usd), 0.0)).where(
                UsageEventRecord.occurred_at >= cutoff
            )

            if rec.scope_type == "provider" and rec.scope_target:
                query = query.where(UsageEventRecord.provider == rec.scope_target)
            elif rec.scope_type == "profile" and rec.scope_target:
                query = query.where(UsageEventRecord.profile_name == rec.scope_target)
            elif rec.scope_type == "project" and rec.scope_target:
                query = query.where(UsageEventRecord.project_id == rec.scope_target)
            elif rec.scope_type == "model" and rec.scope_target:
                query = query.where(UsageEventRecord.model == rec.scope_target)

            total_spend = float(session.execute(query).scalar_one() or 0.0)

            percent = (total_spend / rec.amount_limit * 100.0) if rec.amount_limit > 0 else 0.0
            is_warning = percent >= rec.soft_limit_percent
            is_exceeded = total_spend >= rec.amount_limit

            return BudgetStatus(
                id=rec.id,
                name=rec.name,
                scope_type=rec.scope_type,
                scope_target=rec.scope_target,
                amount_limit=rec.amount_limit,
                currency=rec.currency,
                period=rec.period,
                current_spend=total_spend,
                percent_used=percent,
                is_warning=is_warning,
                is_exceeded=is_exceeded,
                hard_limit_enabled=rec.hard_limit_enabled,
            )

    def forecast_spend(self, budget_id: str) -> dict[str, Any]:
        """Project period-end spend based on current linear burn rate."""
        status = self.evaluate_budget(budget_id)
        now = datetime.now(UTC)

        if status.period == "daily":
            # Hours elapsed today / 24
            fraction = max(0.04, (now.hour * 3600 + now.minute * 60 + now.second) / 86400.0)
        elif status.period == "weekly":
            # Days elapsed this week / 7
            fraction = max(0.05, (now.weekday() * 86400 + now.hour * 3600 + now.minute * 60) / (7 * 86400.0))
        else:
            # Days elapsed in month / 30
            day_of_month = now.day
            fraction = max(0.03, day_of_month / 30.0)

        fraction = min(1.0, fraction)
        projected_spend = round(status.current_spend / fraction, 4)
        projected_percent = (
            round(projected_spend / status.amount_limit * 100.0, 1)
            if status.amount_limit > 0
            else 0.0
        )
        will_exceed = projected_spend >= status.amount_limit

        return {
            "budget_id": status.id,
            "name": status.name,
            "period": status.period,
            "amount_limit": status.amount_limit,
            "currency": status.currency,
            "current_spend": round(status.current_spend, 4),
            "fraction_elapsed": round(fraction, 3),
            "projected_spend": projected_spend,
            "projected_percent_used": projected_percent,
            "will_exceed": will_exceed,
        }

    def check_hard_limits(
        self,
        *,
        provider: str | None = None,
        profile: str | None = None,
        project_id: str | None = None,
        model: str | None = None,
    ) -> list[BudgetStatus]:
        """Return list of any hard-limited budgets that are currently exceeded."""
        budgets = self.list_budgets()
        blocked: list[BudgetStatus] = []
        for b in budgets:
            if not b.hard_limit_enabled or not b.is_exceeded:
                continue
            matches_global = b.scope_type == "global"
            matches_prov = b.scope_type == "provider" and provider and b.scope_target == provider
            matches_prof = b.scope_type == "profile" and profile and b.scope_target == profile
            matches_proj = b.scope_type == "project" and project_id and b.scope_target == project_id
            matches_model = b.scope_type == "model" and model and b.scope_target == model
            if matches_global or matches_prov or matches_prof or matches_proj or matches_model:
                blocked.append(b)
        return blocked

    def remove_budget(self, budget_id: str) -> bool:
        """Remove a budget by ID."""
        clean = budget_id.strip()
        with self._db.session_scope() as session:
            rec = session.get(BudgetRecord, clean)
            if rec is None:
                return False
            session.delete(rec)
            return True
