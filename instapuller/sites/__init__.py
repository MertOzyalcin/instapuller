"""Registry of allowed sites.

To add a platform (e.g. YouTube), write a new SiteAdapter and register it in the
ALLOWED_SITES dictionary; the public interface does not change.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from instapuller.models import ParsedLink, PullError
from instapuller.sites.base import SiteAdapter
from instapuller.sites.instagram import InstagramAdapter
from instapuller.sites.urlcheck import authority_is_clean

ALLOWED_SITES: dict[str, SiteAdapter] = {
    "instagram": InstagramAdapter(),
}


def allowed_hosts() -> frozenset[str]:
    hosts: set[str] = set()
    for adapter in ALLOWED_SITES.values():
        hosts.update(adapter.hosts)
    return frozenset(hosts)


def _normalize_host(raw_host: str | None) -> str:
    host = (raw_host or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def parse_url(url: str) -> tuple[SiteAdapter, ParsedLink]:
    """Validate the URL against the allowlist and return the matching site adapter."""
    raw = (url or "").strip()
    if not raw:
        raise PullError(
            "LINK_GECERSIZ",
            "The link cannot be empty. Enter an Instagram link or upload the video as a file.",
        )
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise PullError(
            "LINK_GECERSIZ",
            "The link format is invalid. Enter an Instagram link or upload the video as a file.",
        ) from None

    if parts.scheme not in ("http", "https"):
        raise PullError(
            "LINK_GECERSIZ",
            "Only web links are supported. Upload the video as a file instead.",
        )

    if not authority_is_clean(parts):
        raise PullError(
            "LINK_GECERSIZ",
            "The link format is invalid. Enter an Instagram link or upload the video as a file.",
        )

    host = _normalize_host(parts.hostname)
    for adapter in ALLOWED_SITES.values():
        if host in adapter.hosts:
            return adapter, adapter.parse_url(raw)

    supported = ", ".join(sorted(allowed_hosts()))
    raise PullError(
        "LINK_GECERSIZ",
        f"This site is not supported (allowed: {supported}). Upload the video as a file instead.",
    )
