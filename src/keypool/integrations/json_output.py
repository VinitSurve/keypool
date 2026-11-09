"""Standardized, secret-redacted JSON serialization for KEYPOOL CLI automation (Section 19)."""

from __future__ import annotations

import json
import sys
from typing import Any

from keypool.security.redaction import redact_data, redact_text
from keypool.storage.models import utc_now_iso
from keypool.version import __version__


def format_json_envelope(
    command: str,
    data: Any,
    *,
    ok: bool = True,
    warnings: list[str] | None = None,
) -> str:
    """Wrap command output in a predictable, redacted JSON envelope with ISO 8601 timestamp."""
    envelope: dict[str, Any] = {
        "ok": ok,
        "command": command,
        "version": __version__,
        "generated_at": utc_now_iso(),
        "data": redact_data(data),
    }
    if warnings:
        envelope["warnings"] = [redact_text(w) for w in warnings]
    raw_json = json.dumps(envelope, indent=2, sort_keys=False)
    return redact_text(raw_json)


def emit_json(
    command: str,
    data: Any,
    *,
    ok: bool = True,
    warnings: list[str] | None = None,
) -> None:
    """Write machine-readable JSON directly to stdout without decorative terminal characters."""
    rendered = format_json_envelope(command, data, ok=ok, warnings=warnings)
    sys.stdout.write(rendered + "\n")
    sys.stdout.flush()


def emit_json_error(
    command: str,
    *,
    error_code: str,
    message: str,
    exit_code: int,
    hint: str | None = None,
) -> None:
    """Write a structured JSON error payload to stderr."""
    payload: dict[str, Any] = {
        "ok": False,
        "command": command,
        "version": __version__,
        "generated_at": utc_now_iso(),
        "error": {
            "code": error_code,
            "message": redact_text(message),
            "exit_code": exit_code,
            "hint": redact_text(hint) if hint else None,
        },
    }
    sys.stderr.write(redact_text(json.dumps(payload, indent=2)) + "\n")
    sys.stderr.flush()
