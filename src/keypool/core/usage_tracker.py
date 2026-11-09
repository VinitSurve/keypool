"""Local usage and activity tracking for KEYPOOL (Section 14)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select

from keypool.core.exceptions import InvalidArgumentError
from keypool.security.redaction import redact_text
from keypool.storage.database import DatabaseManager
from keypool.storage.models import (
    CredentialRecord,
    TokenSource,
    UsageEventRecord,
    utc_now_iso,
)

_PERIOD_RE = re.compile(r"^(\d+)\s*([hdwm])$")


def parse_period_cutoff(period: str | None) -> str | None:
    """Parse a period string like '24h', '7d', '30d', '4w', 'today', or 'all' into an ISO 8601 UTC cutoff."""
    if not period or period.strip().lower() in ("all", "*"):
        return None
    cleaned = period.strip().lower()
    if cleaned == "today":
        cleaned = "24h"
    match = _PERIOD_RE.match(cleaned)
    if not match:
        raise InvalidArgumentError(
            f"Invalid period '{period}'. Expected format like '24h', '7d', '30d', '2w', 'today', or 'all'."
        )
    amount = int(match.group(1))
    unit = match.group(2)
    if amount <= 0:
        raise InvalidArgumentError("Period duration must be greater than zero.")

    now = datetime.now(UTC)
    if unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    elif unit == "w":
        delta = timedelta(weeks=amount)
    elif unit == "m":
        delta = timedelta(days=30 * amount)
    else:
        delta = timedelta(days=amount)

    return (now - delta).isoformat(timespec="seconds")


class CredentialUsageRollup(BaseModel):
    """Aggregated usage metrics for a single credential."""

    model_config = ConfigDict(extra="forbid")

    credential_id: str
    credential_name: str
    provider: str
    selections: int = 0
    executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    observed_429_events: int = 0
    observed_tokens: int = 0
    token_source: str = TokenSource.UNKNOWN.value
    last_used_at: str | None = None
    last_successful_at: str | None = None
    last_failure_at: str | None = None


class UsageSummaryReport(BaseModel):
    """Comprehensive local usage tracking report."""

    model_config = ConfigDict(extra="forbid")

    period: str
    provider_filter: str | None = None
    credential_filter: str | None = None
    total_selections: int = 0
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    observed_429_events: int = 0
    observed_tokens: int = 0
    token_source: str = TokenSource.UNKNOWN.value
    billing_disclaimer: str = (
        "Metrics reflect locally observed CLI executions and provider health probes only; "
        "KEYPOOL does not have authoritative provider billing data."
    )
    by_provider: dict[str, dict[str, Any]] = Field(default_factory=dict)
    by_credential: list[CredentialUsageRollup] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class UsageTracker:
    """Records and queries local credential usage and activity."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def record_event(
        self,
        *,
        provider: str,
        event_type: str,
        status: str = "success",
        credential_id: str | None = None,
        profile_name: str | None = None,
        exit_code: int | None = None,
        http_status: int | None = None,
        is_rate_limit: bool = False,
        model: str | None = None,
        observed_tokens: int | None = None,
        token_source: TokenSource = TokenSource.UNKNOWN,
        duration_ms: float | None = None,
        command_name: str | None = None,
        summary: str | None = None,
        cost_usd: float | None = None,
        project_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Record a sanitized local usage/activity event."""
        safe_summary = redact_text(summary) if summary else None
        safe_cmd = redact_text(command_name) if command_name else None
        with self._db.session_scope() as session:
            session.add(
                UsageEventRecord(
                    credential_id=credential_id,
                    provider=provider.strip().lower(),
                    profile_name=profile_name.strip().lower() if profile_name else None,
                    event_type=event_type,
                    status=status,
                    exit_code=exit_code,
                    http_status=http_status,
                    is_rate_limit=is_rate_limit or (http_status == 429),
                    model=model,
                    observed_tokens=observed_tokens,
                    token_source=token_source.value,
                    duration_ms=duration_ms,
                    command_name=safe_cmd,
                    summary=safe_summary,
                    occurred_at=utc_now_iso(),
                    cost_usd=cost_usd,
                    project_id=project_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )

    def get_recent_activity(self, limit: int = 8) -> list[dict[str, Any]]:
        """Fetch recent activity entries for dashboard and status views."""
        with self._db.session_scope() as session:
            rows = (
                session.execute(
                    select(UsageEventRecord)
                    .order_by(desc(UsageEventRecord.occurred_at), desc(UsageEventRecord.id))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": row.id,
                    "occurred_at": row.occurred_at,
                    "provider": row.provider,
                    "credential_id": row.credential_id,
                    "profile": row.profile_name,
                    "event_type": row.event_type,
                    "status": row.status,
                    "summary": redact_text(row.summary or row.event_type),
                }
                for row in rows
            ]

    def list_events(
        self,
        *,
        provider: str | None = None,
        credential_id: str | None = None,
        limit: int = 500,
    ) -> list[UsageEventRecord]:
        """Fetch raw usage event records for export or detailed auditing."""
        norm_prov = provider.strip().lower() if provider else None
        norm_cred = credential_id.strip() if credential_id else None
        with self._db.session_scope() as session:
            stmt = select(UsageEventRecord)
            if norm_prov:
                stmt = stmt.where(UsageEventRecord.provider == norm_prov)
            if norm_cred:
                stmt = stmt.where(UsageEventRecord.credential_id == norm_cred)
            stmt = stmt.order_by(desc(UsageEventRecord.occurred_at), desc(UsageEventRecord.id)).limit(limit)
            return list(session.execute(stmt).scalars().all())

    def get_usage_report(
        self,
        *,
        provider: str | None = None,
        credential_id: str | None = None,
        period: str = "all",
    ) -> UsageSummaryReport:
        """Aggregate local usage statistics with optional filters."""
        cutoff = parse_period_cutoff(period)
        norm_provider = provider.strip().lower() if provider else None
        norm_cred = credential_id.strip() if credential_id else None

        with self._db.session_scope() as session:
            cred_stmt = select(CredentialRecord).order_by(
                CredentialRecord.provider, CredentialRecord.created_at
            )
            if norm_provider:
                cred_stmt = cred_stmt.where(CredentialRecord.provider == norm_provider)
            if norm_cred:
                cred_stmt = cred_stmt.where(CredentialRecord.id == norm_cred)
            cred_rows = session.execute(cred_stmt).scalars().all()
            cred_map = {c.id: c for c in cred_rows}

            evt_stmt = select(UsageEventRecord).order_by(
                desc(UsageEventRecord.occurred_at), desc(UsageEventRecord.id)
            )
            if norm_provider:
                evt_stmt = evt_stmt.where(UsageEventRecord.provider == norm_provider)
            if norm_cred:
                evt_stmt = evt_stmt.where(UsageEventRecord.credential_id == norm_cred)
            if cutoff:
                evt_stmt = evt_stmt.where(UsageEventRecord.occurred_at >= cutoff)

            events = session.execute(evt_stmt).scalars().all()

            rollups: dict[str, CredentialUsageRollup] = {
                c.id: CredentialUsageRollup(
                    credential_id=c.id,
                    credential_name=c.name,
                    provider=c.provider,
                    last_used_at=c.last_used_at,
                    last_successful_at=c.last_successful_at,
                    last_failure_at=c.last_failure_at,
                )
                for c in cred_rows
            }

            total_selections = 0
            total_executions = 0
            successful_executions = 0
            failed_executions = 0
            observed_429 = 0
            observed_tokens = 0
            has_provider_tokens = False
            by_provider: dict[str, dict[str, Any]] = {}

            for evt in events:
                prov = evt.provider
                if prov not in by_provider:
                    by_provider[prov] = {
                        "selections": 0,
                        "executions": 0,
                        "successful_executions": 0,
                        "failed_executions": 0,
                        "observed_429_events": 0,
                        "observed_tokens": 0,
                    }

                cid = evt.credential_id
                if cid and cid not in rollups:
                    rollups[cid] = CredentialUsageRollup(
                        credential_id=cid,
                        credential_name=cred_map[cid].name if cid in cred_map else "(deleted)",
                        provider=prov,
                    )

                if evt.event_type == "selection":
                    total_selections += 1
                    by_provider[prov]["selections"] += 1
                    if cid and cid in rollups:
                        rollups[cid].selections += 1
                elif (
                    evt.event_type in ("exec_success", "gateway_completion", "gateway_embeddings")
                    and evt.status == "success"
                ):
                    total_executions += 1
                    successful_executions += 1
                    by_provider[prov]["executions"] += 1
                    by_provider[prov]["successful_executions"] += 1
                    if cid and cid in rollups:
                        rollups[cid].executions += 1
                        rollups[cid].successful_executions += 1
                elif (
                    evt.event_type in ("exec_failure", "gateway_completion", "gateway_embeddings")
                    and evt.status != "success"
                ) or evt.event_type == "exec_failure":
                    total_executions += 1
                    failed_executions += 1
                    by_provider[prov]["executions"] += 1
                    by_provider[prov]["failed_executions"] += 1
                    if cid and cid in rollups:
                        rollups[cid].executions += 1
                        rollups[cid].failed_executions += 1

                if (
                    evt.is_rate_limit
                    or evt.http_status == 429
                    or evt.event_type == "rate_limit_429"
                ):
                    observed_429 += 1
                    by_provider[prov]["observed_429_events"] += 1
                    if cid and cid in rollups:
                        rollups[cid].observed_429_events += 1

                if evt.observed_tokens and evt.observed_tokens > 0:
                    observed_tokens += evt.observed_tokens
                    has_provider_tokens = True
                    by_provider[prov]["observed_tokens"] += evt.observed_tokens
                    if cid and cid in rollups:
                        rollups[cid].observed_tokens += evt.observed_tokens
                        rollups[cid].token_source = evt.token_source

            recent_list = [
                {
                    "occurred_at": e.occurred_at,
                    "provider": e.provider,
                    "credential_id": e.credential_id,
                    "profile": e.profile_name,
                    "event_type": e.event_type,
                    "status": e.status,
                    "exit_code": e.exit_code,
                    "http_status": e.http_status,
                    "model": e.model,
                    "observed_tokens": e.observed_tokens,
                    "token_source": e.token_source,
                    "duration_ms": e.duration_ms,
                    "summary": redact_text(e.summary or e.event_type),
                }
                for e in events[:25]
            ]

            return UsageSummaryReport(
                period=period,
                provider_filter=norm_provider,
                credential_filter=norm_cred,
                total_selections=total_selections,
                total_executions=total_executions,
                successful_executions=successful_executions,
                failed_executions=failed_executions,
                observed_429_events=observed_429,
                observed_tokens=observed_tokens,
                token_source=(
                    TokenSource.PROVIDER_REPORTED.value
                    if has_provider_tokens
                    else TokenSource.UNKNOWN.value
                ),
                by_provider=by_provider,
                by_credential=list(rollups.values()),
                recent_events=recent_list,
            )
