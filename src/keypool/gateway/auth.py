"""Local AI Gateway authentication and token management."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from fastapi import Header, HTTPException, status

from keypool.config.settings import AppPaths
from keypool.security.validation import ensure_secure_file


class GatewayAuth:
    """Manages secure bearer tokens for the local AI gateway."""

    def __init__(self, paths: AppPaths | None = None, token_override: str | None = None) -> None:
        self.paths = paths or AppPaths()
        self._token_override = token_override
        self._token_path = self.paths.gateway_token_path

    def get_or_create_token(self) -> str:
        """Retrieve existing gateway token or securely generate a new one."""
        if self._token_override:
            return self._token_override

        if self._token_path.exists():
            try:
                token = self._token_path.read_text(encoding="utf-8").strip()
                if token:
                    return token
            except OSError:
                pass

        return self.regenerate_token()

    def regenerate_token(self) -> str:
        """Generate a cryptographically secure gateway token and store it with 0600 perms."""
        token = f"kp_gw_{secrets.token_hex(24)}"
        self.paths.ensure_directories()
        self._token_path.write_text(token, encoding="utf-8")
        ensure_secure_file(self._token_path)
        return token

    def validate_token(self, provided_token: str) -> bool:
        """Constant-time token validation against current active token."""
        active_token = self.get_or_create_token()
        return secrets.compare_digest(active_token, provided_token)


def create_auth_dependency(auth: GatewayAuth, require_auth: bool = True) -> Callable[..., Any]:
    """FastAPI dependency to enforce Bearer token authentication."""

    async def verify_gateway_auth(authorization: str | None = Header(None)) -> str | None:
        if not require_auth:
            return None

        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header. Use 'Authorization: Bearer <token>'",
                headers={"WWW-Authenticate": "Bearer"},
            )

        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Authorization header format. Expected 'Bearer <token>'",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = parts[1]
        if not auth.validate_token(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid gateway token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token

    return verify_gateway_auth
