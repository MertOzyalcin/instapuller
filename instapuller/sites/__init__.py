"""İzin verilen siteler kayıt defteri.

Yeni bir platform (ör. YouTube) eklendiğinde yalnızca yeni bir SiteAdapter yazılıp
ALLOWED_SITES sözlüğüne kaydedilir; dış arayüz değişmez.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from instapuller.models import ParsedLink, PullError
from instapuller.sites.base import SiteAdapter
from instapuller.sites.instagram import InstagramAdapter

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
    """URL'yi izin listesine göre doğrular ve ilgili site bağdaştırıcısını döndürür."""
    raw = (url or "").strip()
    if not raw:
        raise PullError(
            "LINK_GECERSIZ",
            "Link boş olamaz. Instagram linki girin veya videoyu dosya olarak yükleyin.",
        )
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise PullError(
            "LINK_GECERSIZ",
            "Link biçimi geçersiz. Instagram linki girin veya videoyu dosya olarak yükleyin.",
        ) from None

    if parts.scheme not in ("http", "https"):
        raise PullError(
            "LINK_GECERSIZ",
            "Yalnızca web linkleri desteklenir. Videoyu dosya olarak yükleyin.",
        )

    host = _normalize_host(parts.hostname)
    for adapter in ALLOWED_SITES.values():
        if host in adapter.hosts:
            return adapter, adapter.parse_url(raw)

    supported = ", ".join(sorted(allowed_hosts()))
    raise PullError(
        "LINK_GECERSIZ",
        f"Bu site desteklenmiyor (izin verilen: {supported}). Videoyu dosya olarak yükleyin.",
    )
