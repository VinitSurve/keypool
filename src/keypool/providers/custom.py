"""Secure custom provider framework with SSRF protections."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from keypool.core.exceptions import InvalidArgumentError, SecurityError
from keypool.providers.openai_compatible import OpenAICompatibleProvider

# Prohibited dangerous network destinations
_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "instance-data",
}

_BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / Cloud metadata (AWS, Azure, GCP, DigitalOcean)
    ipaddress.ip_network("100.100.100.200/32"), # Alibaba Cloud metadata
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),          # Current network
]


def validate_provider_endpoint_url(raw_url: str, *, allow_localhost: bool = True) -> str:
    """Validate a custom provider URL to protect against SSRF, file access, and malformed targets."""
    cleaned = raw_url.strip()
    if not cleaned:
        raise InvalidArgumentError("Provider endpoint URL cannot be empty.")

    try:
        parsed = urlparse(cleaned)
    except Exception as exc:
        raise InvalidArgumentError(f"Malformed endpoint URL '{raw_url}': {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise SecurityError(
            f"Unsafe URL scheme '{parsed.scheme}'. Only 'https' and 'http' protocols are permitted.",
            hint="Ensure the URL starts with https:// or http://",
        )

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise InvalidArgumentError(f"URL '{raw_url}' has no valid hostname.")

    if hostname in _BLOCKED_HOSTNAMES:
        raise SecurityError(
            f"Access to cloud metadata hostname '{hostname}' is blocked for security.",
            hint="Specify an authorized AI provider API endpoint.",
        )

    # Check IP literal destinations
    try:
        ip_obj = ipaddress.ip_address(hostname)
        for net in _BLOCKED_IP_NETWORKS:
            if ip_obj in net:
                raise SecurityError(
                    f"Access to IP address '{hostname}' in network '{net}' is blocked for security.",
                    hint="Cloud metadata and link-local IP addresses are prohibited.",
                )
        if not allow_localhost and (ip_obj.is_loopback or ip_obj.is_private):
            raise SecurityError(
                f"Access to private/loopback IP '{hostname}' is restricted.",
                hint="Enable allow_localhost or use an external HTTPS endpoint.",
            )
    except ValueError:
        # Hostname is a domain name, not an IP literal
        pass

    # Basic normalization: strip trailing slash
    return cleaned.rstrip("/")


class CustomProviderConfig(BaseModel):
    """Configuration model for user-defined custom provider endpoints.

    NOTE: Never contains secret API keys.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    base_url: str
    env_var_name: str
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    default_test_model: str = "default"
    timeout_seconds: float = 15.0
    requires_key: bool = True
    custom_headers: dict[str, str] = Field(default_factory=dict)


class CustomProvider(OpenAICompatibleProvider):
    """Custom provider instance instantiated from validated user configuration."""

    def __init__(
        self,
        config: CustomProviderConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        validated_url = validate_provider_endpoint_url(config.base_url)
        super().__init__(
            name=config.name,
            display_name=config.display_name,
            base_url=validated_url,
            env_var_name=config.env_var_name,
            default_test_model=config.default_test_model,
            auth_header=config.auth_header,
            auth_scheme=config.auth_scheme,
            timeout_seconds=config.timeout_seconds,
            http_client=http_client,
            requires_key=config.requires_key,
        )
        self.custom_headers = dict(config.custom_headers)

    def _build_headers(self, credential: str) -> dict[str, str]:
        headers = super()._build_headers(credential)
        headers.update(self.custom_headers)
        return headers
