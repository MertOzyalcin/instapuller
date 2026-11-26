"""URL authority check shared by site adapters."""

from __future__ import annotations

from urllib.parse import SplitResult

_ALLOWED_PORTS = (None, 80, 443)


def authority_is_clean(parts: SplitResult) -> bool:
    """Reject links containing user info (@) or a port other than 80/443."""
    if "@" in parts.netloc:
        return False
    try:
        port = parts.port
    except ValueError:  # invalid port such as 99999
        return False
    return port in _ALLOWED_PORTS
