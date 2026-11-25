"""Instagram link ayrıştırıcı, metadata eşleyici ve hata sınıflandırıcı."""

from __future__ import annotations

import re
import socket
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from yt_dlp.networking.exceptions import TransportError

from instapuller.models import (
    TOOL_OUTDATED_DETAIL,
    ParsedLink,
    PullError,
    SizeLimitExceeded,
    SourceMeta,
)
from instapuller.sanitize import MAX_DETAIL_LEN, sanitize_detail
from instapuller.sites.urlcheck import authority_is_clean

_IG_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,}$")
_IG_HOSTS = frozenset({"instagram.com"})


_PREFIX_RE = re.compile(r"^\s*(?:ERROR:\s*|WARNING:\s*|\[[^\]]+\]\s*[^\s:]+:\s*)", re.I)
_HTTP_STATUS_RE = re.compile(r"HTTP Error (\d{3})")

_SIZE_KEYS = ("max-filesize", "max_filesize", "larger than max", "file is larger")
_RATE_LIMIT_KEYS = (
    "rate-limit",
    "rate limit",
    "too many requests",
    "wait a few minutes",
    "1675004",
)
_NO_VIDEO_KEYS = (
    "there is no video in this post",
    "no video formats found",
    "no video in this post",
    "requested format is not available",
    "unsupported url",
)
_INACCESSIBLE_KEYS = (
    "empty media response",
    "only available for registered users",
    "login required",
    "logged-in",
    "restricted video",
    "private",
    "does not exist",
    "not available",
    "unavailable",
)
_NETWORK_KEYS = (
    "unable to download",
    "urlopen error",
    "getaddrinfo",
    "name or service not known",
    "temporary failure in name resolution",
    "timed out",
    "connection reset",
    "connection refused",
    "connection aborted",
    "network is unreachable",
    "remote end closed",
    "incompleteread",
)
_PARSE_KEYS = (
    "unable to extract",
    "extractor",
    "parse",
    "regex",
    "failed to decode",
)
_FS_ERRORS = (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError, FileExistsError)


def _strip_prefixes(msg: str) -> str:
    """"ERROR: " ve "[Instagram] <id>: " öneklerini (yt-dlp'nin eklediği) atar."""
    prev = None
    while prev != msg:
        prev = msg
        msg = _PREFIX_RE.sub("", msg, count=1)
    return msg


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """DownloadError.exc_info, ExtractorError.cause/exc_info, __cause__ ve __context__ zincirini gezer."""
    seen: list[BaseException] = []
    stack: list[object] = [exc]
    while stack and len(seen) < 20:
        cur = stack.pop()
        if not isinstance(cur, BaseException) or any(cur is s for s in seen):
            continue
        seen.append(cur)
        info = getattr(cur, "exc_info", None)
        if isinstance(info, tuple) and len(info) > 1:
            stack.append(info[1])
        stack.append(getattr(cur, "cause", None))
        stack.append(cur.__cause__)
        stack.append(cur.__context__)
    return seen


def _http_status(chain: list[BaseException], raw_msg: str) -> int | None:
    for e in chain:
        status = getattr(e, "status", None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
    for e in chain:
        m = _HTTP_STATUS_RE.search(str(e))
        if m:
            return int(m.group(1))
    m = _HTTP_STATUS_RE.search(raw_msg)
    return int(m.group(1)) if m else None


def _is_network_exc(e: BaseException) -> bool:
    if isinstance(e, TransportError | TimeoutError | socket.timeout):
        return True
    return isinstance(e, OSError) and not isinstance(e, _FS_ERRORS)


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


def _outdated_detail(raw_msg: str, allowed_paths: Iterable[str | Path]) -> str:
    budget = MAX_DETAIL_LEN - len(TOOL_OUTDATED_DETAIL) - len(" Orijinal hata: ")
    original = sanitize_detail(raw_msg, max_len=max(budget, 0), allowed_paths=allowed_paths)
    return f"{TOOL_OUTDATED_DETAIL} Orijinal hata: {original}" if original else TOOL_OUTDATED_DETAIL


class InstagramAdapter:
    site_id = "instagram"
    hosts = _IG_HOSTS

    def parse_url(self, url: str) -> ParsedLink:
        raw = (url or "").strip()
        if not raw:
            raise PullError(
                "LINK_GECERSIZ",
                "Geçersiz Instagram linki. Bir Reel veya tekil video linki girin ya da videoyu dosya olarak yükleyin.",
            )
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parts = urlsplit(raw)
        except ValueError:
            raise PullError(
                "LINK_GECERSIZ",
                "Geçersiz Instagram linki. Bir Reel veya tekil video linki girin ya da videoyu dosya olarak yükleyin.",
            ) from None

        if (
            parts.scheme not in ("http", "https")
            or not authority_is_clean(parts)
            or _normalize_host(parts.hostname) not in self.hosts
        ):
            raise PullError(
                "LINK_GECERSIZ",
                "Geçersiz Instagram linki. Bir Reel veya tekil video linki girin ya da videoyu dosya olarak yükleyin.",
            )

        segs = [s for s in parts.path.split("/") if s]
        # /<kullanici>/reel/<kod> veya /<kullanici>/reels/<kod>
        if len(segs) == 3 and segs[1] in ("reel", "reels"):
            segs = segs[1:]

        if len(segs) == 2 and _IG_CODE_RE.fullmatch(segs[1]) and re.search(r"[A-Za-z0-9]", segs[1]):
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
            "Bu Instagram linki desteklenmiyor. Tekil bir Reel veya video linki girin ya da videoyu dosya olarak yükleyin.",
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

        chain = _exception_chain(exc)
        raw_msg = str(exc) or type(exc).__name__
        detail = sanitize_detail(raw_msg, allowed_paths=allowed_paths)
        text = _strip_prefixes(raw_msg).lower()

        # 1. İndirme sırasında boyut sınırı aşıldı (iç istisna) ya da yt-dlp'nin kendi mesajı
        if any(isinstance(e, SizeLimitExceeded) for e in chain) or any(
            k in text for k in _SIZE_KEYS
        ):
            return PullError(
                "DOSYA_BUYUK",
                "Video izin verilen dosya boyutu sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                detail=detail,
            )

        # 2. HTTP durumu: önce istisna türünden, yoksa yalnızca "HTTP Error NNN" kalıbından
        status = _http_status(chain, raw_msg)
        if status == 429:
            return self._rate_limit(detail)
        if status in (401, 403, 404):
            return self._inaccessible(detail)
        if status is not None:
            return self._transient(detail)  # 5xx ve diğer HTTP hataları

        # 3. Metne dayalı yt-dlp mesajları (önekler atılmış halde)
        if any(k in text for k in _RATE_LIMIT_KEYS):
            return self._rate_limit(detail)
        if any(k in text for k in _NO_VIDEO_KEYS):
            return PullError(
                "IG_VIDEO_DEGIL",
                "Bu gönderide indirilebilir tekil bir video bulunamadı. Videoyu dosya olarak yükleyin.",
                detail=detail,
            )
        if any(k in text for k in _INACCESSIBLE_KEYS):
            return self._inaccessible(detail)

        # 4. Ağ / DNS / zaman aşımı: tür ya da yt-dlp'nin ağ mesajı kalıpları
        if any(_is_network_exc(e) for e in chain) or any(k in text for k in _NETWORK_KEYS):
            return self._transient(detail)

        # 5. Ayrıştırma / yt-dlp sürüm uyumsuzluğu
        if isinstance(exc, (KeyError, TypeError, ValueError)) or any(
            k in text for k in _PARSE_KEYS
        ):
            return PullError(
                "IG_ARAC_GUNCEL_DEGIL",
                "Instagram'dan gelen yanıt okunamadı. Videoyu dosya olarak yükleyin.",
                detail=_outdated_detail(raw_msg, allowed_paths),
            )

        # 6. Diğer geçici hatalar
        return self._transient(detail)

    @staticmethod
    def _rate_limit(detail: str | None) -> PullError:
        return PullError(
            "IG_RATE_LIMIT",
            "Instagram bu IP adresinden gelen istekleri geçici olarak kısıtladı. "
            "Bir süre sonra tekrar deneyin veya videoyu dosya olarak yükleyin.",
            detail=detail,
        )

    @staticmethod
    def _inaccessible(detail: str | None) -> PullError:
        return PullError(
            "IG_ERISILEMEDI",
            "Bu gönderiye erişilemedi (gizli, silinmiş ya da giriş gerekiyor olabilir). "
            "Videoyu dosya olarak yükleyin.",
            detail=detail,
        )

    @staticmethod
    def _transient(detail: str | None) -> PullError:
        return PullError(
            "KAYNAK_GECICI",
            "Video şu anda indirilemedi. Biraz sonra tekrar deneyin veya videoyu dosya olarak yükleyin.",
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
