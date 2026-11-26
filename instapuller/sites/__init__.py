"""Registry of allowed sites.

To add a platform, write a new SiteAdapter and register it in the
ALLOWED_SITES dictionary; the public interface does not change.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from instapuller.models import ParsedLink, PullError
from instapuller.sites.base import SiteAdapter
from instapuller.sites._common import normalize_host
from instapuller.sites.instagram import InstagramAdapter
from instapuller.sites.urlcheck import authority_is_clean
from instapuller.sites.youtube import YouTubeAdapter

ALLOWED_SITES: dict[str, SiteAdapter] = {
    "instagram": InstagramAdapter(),
    "youtube": YouTubeAdapter(),
}


def allowed_hosts() -> frozenset[str]:
    hosts: set[str] = set()
    for adapter in ALLOWED_SITES.values():
        hosts.update(adapter.hosts)
    return frozenset(hosts)


def parse_url(url: str) -> tuple[SiteAdapter, ParsedLink]:
    """Validate the URL against the allowlist and return the matching site adapter."""
    raw = (url or "").strip()
    if not raw:
        raise PullError(
            "LINK_GECERSIZ",
            "The link cannot be empty. Enter an Instagram or YouTube link, or upload the video as a file.",
        )
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise PullError(
            "LINK_GECERSIZ",
            "The link format is invalid. Enter an Instagram or YouTube link, or upload the video as a file.",
        ) from None

    if parts.scheme not in ("http", "https"):
        raise PullError(
            "LINK_GECERSIZ",
            "Only web links are supported. Upload the video as a file instead.",
        )

    if not authority_is_clean(parts):
        raise PullError(
            "LINK_GECERSIZ",
            "The link format is invalid. Enter an Instagram or YouTube link, or upload the video as a file.",
        )

    host = normalize_host(parts.hostname)
    for adapter in ALLOWED_SITES.values():
        if host in adapter.hosts:
            return adapter, adapter.parse_url(raw)

    supported = ", ".join(sorted(allowed_hosts()))
    raise PullError(
        "LINK_GECERSIZ",
        f"This site is not supported (allowed: {supported}). Upload the video as a file instead.",
    )
