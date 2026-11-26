"""Instagram link parser, metadata mapper and error classifier."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from instapuller.models import (
    TOOL_OUTDATED_DETAIL,
    ParsedLink,
    PullError,
    SizeLimitExceeded,
    SourceMeta,
)
from instapuller.sanitize import sanitize_detail
from instapuller.sites._common import (
    exception_chain,
    http_status,
    int_or_none,
    is_network_exc,
    iso_utc,
    normalize_host,
    outdated_detail,
    strip_prefixes,
)
from instapuller.sites.urlcheck import authority_is_clean

_IG_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,}$")
_IG_HOSTS = frozenset({"instagram.com"})


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


class InstagramAdapter:
    site_id = "instagram"
    hosts = _IG_HOSTS
    allowed_extractors = ["Instagram"]
    format = "mp4/best"
    requires_js_runtime = False

    def extra_ydl_opts(self, js_runtime: Any = None) -> dict[str, Any]:
        return {}

    def check_info(self, info: Any, max_duration_s: float) -> None:
        """Reject non-dict responses, carousels, foreign extractors and posts without video."""
        if not isinstance(info, dict):
            raise PullError(
                "IG_ARAC_GUNCEL_DEGIL",
                "Instagram's response could not be read. Upload the video as a file instead.",
                detail=TOOL_OUTDATED_DETAIL,
            )

        if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
            raise PullError(
                "IG_VIDEO_DEGIL",
                "Carousel posts are not supported; enter a single video or Reel link. "
                "Upload the video as a file instead.",
            )

        if info.get("extractor_key") != "Instagram":
            raise PullError(
                "IG_ARAC_GUNCEL_DEGIL",
                "Instagram's response could not be read. Upload the video as a file instead.",
                detail=f"{TOOL_OUTDATED_DETAIL} Unexpected extractor: {str(info.get('extractor_key'))[:40]}",
            )

        vcodec = info.get("vcodec")
        if vcodec == "none" and not info.get("formats"):
            raise PullError(
                "IG_VIDEO_DEGIL",
                "No downloadable video was found in this post. Upload the video as a file instead.",
            )

    def parse_url(self, url: str) -> ParsedLink:
        raw = (url or "").strip()
        if not raw:
            raise PullError(
                "LINK_GECERSIZ",
                "Invalid Instagram link. Enter a Reel or single video link, or upload the video as a file.",
            )
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parts = urlsplit(raw)
        except ValueError:
            raise PullError(
                "LINK_GECERSIZ",
                "Invalid Instagram link. Enter a Reel or single video link, or upload the video as a file.",
            ) from None

        if (
            parts.scheme not in ("http", "https")
            or not authority_is_clean(parts)
            or normalize_host(parts.hostname) not in self.hosts
        ):
            raise PullError(
                "LINK_GECERSIZ",
                "Invalid Instagram link. Enter a Reel or single video link, or upload the video as a file.",
            )

        segs = [s for s in parts.path.split("/") if s]
        # /<username>/reel/<code> or /<username>/reels/<code>
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
            "This Instagram link is not supported. Enter a single Reel or video link, or upload the video as a file.",
        )

    def map_error(
        self,
        exc: Exception,
        *,
        allowed_paths: Iterable[str | Path] = (),
        warnings: Sequence[str] = (),  # accepted for the adapter protocol; Instagram ignores them
    ) -> PullError:
        if isinstance(exc, PullError):
            exc.detail = sanitize_detail(exc.detail, allowed_paths=allowed_paths)
            return exc

        chain = exception_chain(exc)
        raw_msg = str(exc) or type(exc).__name__
        detail = sanitize_detail(raw_msg, allowed_paths=allowed_paths)
        text = strip_prefixes(raw_msg).lower()

        # 1. Size limit exceeded mid-download (internal exception) or yt-dlp's own message
        if any(isinstance(e, SizeLimitExceeded) for e in chain) or any(
            k in text for k in _SIZE_KEYS
        ):
            return PullError(
                "DOSYA_BUYUK",
                "The video exceeds the allowed file size. Upload the video as a file instead.",
                detail=detail,
            )

        # 2. HTTP status: from the exception type first, else only from the "HTTP Error NNN" pattern
        status = http_status(chain, raw_msg)
        if status == 429:
            return self._rate_limit(detail)
        if status in (401, 403, 404):
            return self._inaccessible(detail)
        if status is not None:
            return self._transient(detail)  # 5xx and other HTTP errors

        # 3. Text-based yt-dlp messages (with prefixes stripped)
        if any(k in text for k in _RATE_LIMIT_KEYS):
            return self._rate_limit(detail)
        if any(k in text for k in _NO_VIDEO_KEYS):
            return PullError(
                "IG_VIDEO_DEGIL",
                "No downloadable single video was found in this post. Upload the video as a file instead.",
                detail=detail,
            )
        if any(k in text for k in _INACCESSIBLE_KEYS):
            return self._inaccessible(detail)

        # 4. Network / DNS / timeout: exception type or yt-dlp network message patterns
        if any(is_network_exc(e) for e in chain) or any(k in text for k in _NETWORK_KEYS):
            return self._transient(detail)

        # 5. Parsing failure / yt-dlp version mismatch
        if isinstance(exc, (KeyError, TypeError, ValueError)) or any(
            k in text for k in _PARSE_KEYS
        ):
            return PullError(
                "IG_ARAC_GUNCEL_DEGIL",
                "Instagram's response could not be read. Upload the video as a file instead.",
                detail=outdated_detail(raw_msg, allowed_paths),
            )

        # 6. Other temporary errors
        return self._transient(detail)

    @staticmethod
    def _rate_limit(detail: str | None) -> PullError:
        return PullError(
            "IG_RATE_LIMIT",
            "Instagram has temporarily limited requests from this IP address. "
            "Try again later or upload the video as a file.",
            detail=detail,
        )

    @staticmethod
    def _inaccessible(detail: str | None) -> PullError:
        return PullError(
            "IG_ERISILEMEDI",
            "This post could not be accessed (it may be private, deleted or require login). "
            "Upload the video as a file instead.",
            detail=detail,
        )

    @staticmethod
    def _transient(detail: str | None) -> PullError:
        return PullError(
            "KAYNAK_GECICI",
            "The video could not be downloaded right now. Try again shortly or upload the video as a file.",
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
            published_at=iso_utc(info.get("timestamp")),
            duration_s=round(duration_s, 2) if duration_s is not None else None,
            stats={
                "views": int_or_none(info.get("view_count")),
                "likes": int_or_none(info.get("like_count")),
                "comments": int_or_none(info.get("comment_count")),
            },
        )
