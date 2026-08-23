"""Network helpers for local service connectivity."""

from ipaddress import ip_address
from urllib.parse import urlparse


def should_trust_proxy_environment(url: str) -> bool:
    """Honor proxy env vars for remote URLs, but never for loopback services."""
    hostname = urlparse(url).hostname
    if not hostname:
        return True

    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return False

    try:
        return not ip_address(normalized).is_loopback
    except ValueError:
        return True
