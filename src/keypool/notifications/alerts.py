"""macOS desktop notifications and intelligent alerting engine."""

from __future__ import annotations

import contextlib
import datetime
import shutil
import subprocess
import uuid
from typing import Any

from sqlalchemy import desc, select

from keypool.cli.formatting import RuntimeContext
from keypool.core.budget_manager import BudgetManager
from keypool.storage.models import AlertEventRecord, AlertRuleRecord, HealthState, utc_now_iso


class AlertManager:
    """Monitors system health, budget burn, and expirations, dispatching macOS notifications."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self.ctx = ctx
        self.db = ctx.db
        self.budgets = BudgetManager(ctx.db)

    def send_macos_notification(self, title: str, message: str) -> bool:
        """Deliver native notification to macOS Notification Center via osascript."""
        osascript_bin = shutil.which("osascript") or "/usr/bin/osascript"
        if not shutil.which(osascript_bin):
            return False

        safe_title = title.replace('"', '\\"').replace("'", "")
        safe_msg = message.replace('"', '\\"').replace("'", "")
        script = f'display notification "{safe_msg}" with title "KEYPOOL: {safe_title}" sound name "default"'

        with contextlib.suppress(Exception):
            subprocess.run(
                [osascript_bin, "-e", script], check=False, capture_output=True, timeout=2.0
            )
            return True
        return False

    def evaluate_alerts(self, *, notify: bool = True) -> list[dict[str, Any]]:
        """Run all alert checks and record new alert events."""
        triggered_alerts: list[dict[str, Any]] = []

        # 1. Evaluate Budget Rules
        for b in self.budgets.list_budgets():
            pct = b.percent_used
            spent = b.current_spend
            limit = b.amount_limit
            name = b.name

            if b.is_exceeded:
                triggered_alerts.append(
                    {
                        "rule_type": "budget_threshold",
                        "severity": "critical",
                        "title": f"Budget Exceeded: {name}",
                        "message": f"Budget '{name}' reached {pct:.1f}% (${spent:.2f} of ${limit:.2f})",
                    }
                )
            elif b.is_warning:
                triggered_alerts.append(
                    {
                        "rule_type": "budget_threshold",
                        "severity": "warning",
                        "title": f"Budget Warning: {name}",
                        "message": f"Budget '{name}' reached {pct:.1f}% (${spent:.2f} of ${limit:.2f})",
                    }
                )

        # 2. Evaluate Credential Health
        creds = self.ctx.credential_manager.list_credentials()
        for c in creds:
            if not c.enabled:
                continue

            if c.status in (
                HealthState.AUTH_FAILED.value,
                HealthState.PERMISSION_DENIED.value,
                HealthState.QUOTA_EXHAUSTED.value,
            ):
                triggered_alerts.append(
                    {
                        "rule_type": "credential_health",
                        "severity": "error",
                        "title": f"Credential Failed: {c.provider.capitalize()}",
                        "message": f"Credential '{c.name}' [{c.id}] is degraded: {c.status}",
                    }
                )
            elif c.status in (
                HealthState.RATE_LIMITED.value,
                HealthState.PROVIDER_UNAVAILABLE.value,
            ):
                triggered_alerts.append(
                    {
                        "rule_type": "credential_health",
                        "severity": "warning",
                        "title": f"Credential Degradation: {c.provider.capitalize()}",
                        "message": f"Credential '{c.name}' [{c.id}] is temporarily degraded: {c.status}",
                    }
                )

            # 3. Evaluate Credential Expiry
            if c.expires_at:
                with contextlib.suppress(Exception):
                    exp_dt = datetime.datetime.fromisoformat(c.expires_at.replace("Z", "+00:00"))
                    now_dt = datetime.datetime.now(datetime.UTC)
                    days_left = (exp_dt - now_dt).total_seconds() / 86400.0
                    if 0 < days_left <= 7.0:
                        triggered_alerts.append(
                            {
                                "rule_type": "credential_expiry",
                                "severity": "warning",
                                "title": f"Credential Expiring Soon: {c.provider.capitalize()}",
                                "message": f"Credential '{c.name}' [{c.id}] expires in {days_left:.1f} days",
                            }
                        )

        # Persist and dispatch notifications
        saved_alerts: list[dict[str, Any]] = []
        now = utc_now_iso()

        with self.db.session_scope() as session:
            for alert in triggered_alerts:
                alert_id = f"alt_{uuid.uuid4().hex[:10]}"
                rec = AlertEventRecord(
                    id=alert_id,
                    rule_type=alert["rule_type"],
                    severity=alert["severity"],
                    title=alert["title"],
                    message=alert["message"],
                    occurred_at=now,
                    acknowledged=False,
                )
                session.add(rec)
                saved_alerts.append(
                    {
                        "id": alert_id,
                        "severity": alert["severity"],
                        "title": alert["title"],
                        "message": alert["message"],
                        "occurred_at": now,
                    }
                )

                if notify:
                    self.send_macos_notification(alert["title"], alert["message"])

        return saved_alerts

    def list_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        """List historical alert events ordered newest first."""
        with self.db.session_scope() as session:
            stmt = (
                select(AlertEventRecord).order_by(desc(AlertEventRecord.occurred_at)).limit(limit)
            )
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "rule_type": r.rule_type,
                    "severity": r.severity,
                    "title": r.title,
                    "message": r.message,
                    "occurred_at": r.occurred_at,
                    "acknowledged": r.acknowledged,
                }
                for r in rows
            ]

    def list_rules(self) -> list[dict[str, Any]]:
        """List configured automated alert rules."""
        with self.db.session_scope() as session:
            rows = (
                session.execute(select(AlertRuleRecord).order_by(AlertRuleRecord.id))
                .scalars()
                .all()
            )
            return [
                {
                    "id": r.id,
                    "rule_type": r.rule_type,
                    "target_id": r.target_id,
                    "threshold": r.threshold,
                    "enabled": r.enabled,
                    "last_triggered_at": r.last_triggered_at,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
