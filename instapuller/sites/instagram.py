"""Instagram link ayrıştırıcı, metadata eşleyici ve hata sınıflandırıcı."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from instapuller.models import ParsedLink, PullError, SourceMeta
from instapuller.sanitize import sanitize_detail

_IG_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,}$")
_IG_HOSTS = frozenset({"instagram.com"})


def _normalize_host(raw_host: str | None) -> str:
    host = (raw_host or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def _int_or_none(val: Any) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int) and val >= 0:
        return val
    return None


def _iso_utc(ts: Any) -> str | None:
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


class InstagramAdapter:
    site_id = "instagram"
    hosts = _IG_HOSTS

    def parse_url(self, url: str) -> ParsedLink:
        raw = (url or "").strip()
        if not raw:
            raise PullError(
                "LINK_GECERSIZ",
                "Geçersiz Instagram linki. Bir Instagram Reel veya tekil video linki girin.",
                upload_fallback=True,
            )
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parts = urlsplit(raw)
        except ValueError:
            raise PullError(
                "LINK_GECERSIZ",
                "Geçersiz Instagram linki. Bir Instagram Reel veya tekil video linki girin.",
                upload_fallback=True,
            ) from None

        if parts.scheme not in ("http", "https") or _normalize_host(parts.hostname) not in self.hosts:
            raise PullError(
                "LINK_GECERSIZ",
                "Geçersiz Instagram linki. Bir Instagram Reel veya tekil video linki girin.",
                upload_fallback=True,
            )

        segs = [s for s in parts.path.split("/") if s]
        # /<kullanici>/reel/<kod> veya /<kullanici>/reels/<kod>
        if len(segs) == 3 and segs[1] in ("reel", "reels"):
            segs = segs[1:]

        if len(segs) == 2 and _IG_CODE_RE.fullmatch(segs[1]):
            kind_seg, code = segs[0], segs[1]
            if kind_seg in ("reel", "reels"):
                return ParsedLink(
                    site=self.site_id,
                    platform_id=code,
                    kind="reel",
                    canonical_url=f"https://www.instagram.com/reel/{code}/",
                )
            if kind_seg == "p":
                return ParsedLink(
                    site=self.site_id,
                    platform_id=code,
                    kind="post",
                    canonical_url=f"https://www.instagram.com/p/{code}/",
                )
            if kind_seg == "tv":
                return ParsedLink(
                    site=self.site_id,
                    platform_id=code,
                    kind="tv",
                    canonical_url=f"https://www.instagram.com/tv/{code}/",
                )

        raise PullError(
            "LINK_GECERSIZ",
            "Bu Instagram linki desteklenmiyor. Yalnızca tekil Reel veya video gönderisi linki girin.",
            upload_fallback=True,
        )

    def map_error(
        self,
        exc: Exception,
        *,
        allowed_paths: Iterable[str | Path] = (),
    ) -> PullError:
        if isinstance(exc, PullError):
            exc.detail = sanitize_detail(exc.detail, allowed_paths=allowed_paths)
            return exc

        raw_msg = str(exc) or type(exc).__name__
        low = raw_msg.lower()
        detail = sanitize_detail(raw_msg, allowed_paths=allowed_paths)

        # 1. Rate limit / geçici IP kısıtlaması
        if any(
            k in low
            for k in (
                "rate-limit",
                "rate limit",
                "429",
                "too many requests",
                "wait a few minutes",
                "1675004",
            )
        ):
            return PullError(
                "IG_RATE_LIMIT",
                "Instagram bu IP adresinden gelen istekleri geçici olarak kısıtladı. "
                "Bir süre sonra tekrar deneyin veya videoyu dosya olarak yükleyin.",
                upload_fallback=True,
                detail=detail,
            )

        # 2. Dosya boyutu sınırı
        if any(
            k in low
            for k in (
                "max-filesize",
                "max_filesize",
                "larger than max",
                "file is larger",
            )
        ):
            return PullError(
                "DOSYA_BUYUK",
                "Video izin verilen dosya boyutu sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                upload_fallback=True,
                detail=detail,
            )

        # 3. Video değil / çoklu gönderi / format yok
        if any(
            k in low
            for k in (
                "no video formats found",
                "no video in this post",
                "there is no video",
                "unsupported url",
            )
        ):
            return PullError(
                "IG_VIDEO_DEGIL",
                "Bu gönderide indirilebilir tekil bir video bulunamadı. Videoyu dosya olarak yükleyin.",
                upload_fallback=True,
                detail=detail,
            )

        # 4. Belirsiz erişim / boş medya yanıtı / gizli / silinmiş / giriş gerekli
        if any(
            k in low
            for k in (
                "empty media response",
                "login required",
                "logged-in",
                "cookies-from-browser",
                "private",
                "not available",
                "unavailable",
                "404",
                "401",
                "403",
                "does not exist",
                "restricted",
            )
        ):
            return PullError(
                "IG_ERISILEMEDI",
                "Bu gönderiye erişilemedi (gizli, silinmiş ya da giriş gerekiyor olabilir). "
                "Videoyu dosya olarak yükleyin.",
                upload_fallback=True,
                detail=detail,
            )

        # 5. Beklenmeyen ayrıştırma / yt-dlp sürüm uyumsuzluğu hataları
        if isinstance(exc, (KeyError, TypeError, ValueError)) or any(
            k in low
            for k in (
                "unable to extract",
                "extractor",
                "json",
                "parse",
                "regex",
                "failed to decode",
            )
        ):
            return PullError(
                "IG_ARAC_GUNCEL_DEGIL",
                "Instagram yanıtı ayrıştırılamadı; yt-dlp güncel olmayabilir. "
                "README'deki güncelleme komutunu çalıştırın veya videoyu dosya olarak yükleyin.",
                upload_fallback=True,
                detail=detail,
            )

        # 6. Diğer ağ / indirme hataları
        return PullError(
            "IG_INDIRME_HATASI",
            "Video indirilirken bir ağ veya aktarım hatası oluştu. "
            "Tekrar deneyin veya videoyu dosya olarak yükleyin.",
            upload_fallback=True,
            detail=detail,
        )

    def extract_meta(
        self,
        info: dict[str, Any],
        parsed: ParsedLink,
        *,
        duration_s: float | None,
    ) -> SourceMeta:
        caption = info.get("description") or info.get("title")
        if isinstance(caption, str):
            caption = caption.strip() or None
        else:
            caption = None

        author = info.get("channel") or info.get("uploader")
        if isinstance(author, str):
            author = author.strip() or None
        else:
            author = None

        return SourceMeta(
            kind=self.site_id,
            url=parsed.canonical_url,
            platform_id=parsed.platform_id,
            title=caption,
            author=author,
            published_at=_iso_utc(info.get("timestamp")),
            duration_s=round(duration_s, 2) if duration_s is not None else None,
            stats={
                "views": _int_or_none(info.get("view_count")),
                "likes": _int_or_none(info.get("like_count")),
                "comments": _int_or_none(info.get("comment_count")),
            },
        )
