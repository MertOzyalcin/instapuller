"""YouTube (Shorts and short videos) link parser, metadata mapper and error classifier."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

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
    fmt_seconds,
    http_status,
    int_or_none,
    is_network_exc,
    iso_utc,
    normalize_host,
    outdated_detail,
    strip_prefixes,
)
from instapuller.sites.urlcheck import authority_is_clean

_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_YT_HOSTS = frozenset({"youtube.com", "youtu.be"})

_SIZE_KEYS = ("max-filesize", "max_filesize", "larger than max", "file is larger")
_JS_KEYS = (
    "no supported javascript runtime",
    "javascript runtime",
    "js runtime",
    "n challenge",
    "nsig extraction",
    "challenge solving failed",
    "signature solving failed",
    "failed to solve",
)
_RATE_LIMIT_KEYS = (
    "rate-limit",
    "rate limit",
    "too many requests",
    "not a bot",
)
_NOT_AUDIO_VIDEO_KEYS = (
    "live event",
    "premieres in",
    "is_live",
    "is_upcoming",
    "this live stream",
    "no video formats found",
    "requested format is not available",
    "no audio",
    "unsupported url",
)
_INACCESSIBLE_KEYS = (
    "private video",
    "video unavailable",
    "this video is unavailable",
    "this video is not available",
    "has been removed",
    "deleted",
    "terminated",
    "confirm your age",
    "age-restricted",
    "inappropriate",
    "join this channel",
    "members-only",
    "members only",
    "login required",
    "copyright",
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
_LIVE_STATUSES = ("is_live", "is_upcoming", "post_live")

_INVALID_LINK = "Invalid YouTube link. Enter a Shorts or single video link, or upload the video as a file."


def _invalid(message: str = _INVALID_LINK) -> PullError:
    return PullError("LINK_GECERSIZ", message)


def _published_at(info: dict[str, Any]) -> str | None:
    for key in ("timestamp", "release_timestamp"):
        iso = iso_utc(info.get(key))
        if iso:
            return iso
    date = info.get("upload_date")
    if isinstance(date, str) and re.fullmatch(r"\d{8}", date):
        try:
            return (
                datetime.strptime(date, "%Y%m%d")
                .replace(tzinfo=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
        except ValueError:
            return None
    return None


def _clean_str(val: Any) -> str | None:
    return (val.strip() or None) if isinstance(val, str) else None


class YouTubeAdapter:
    site_id = "youtube"
    hosts = _YT_HOSTS
    allowed_extractors = ["Youtube"]
    # Audio only: no video download and no ffmpeg merge/postprocessing.
    format = "bestaudio[ext=m4a]/bestaudio"
    requires_js_runtime = True

    def parse_url(self, url: str) -> ParsedLink:
        raw = (url or "").strip()
        if not raw:
            raise _invalid()
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parts = urlsplit(raw)
        except ValueError:
            raise _invalid() from None

        host = normalize_host(parts.hostname)
        if parts.scheme not in ("http", "https") or not authority_is_clean(parts) or host not in self.hosts:
            raise _invalid()

        segs = [s for s in parts.path.split("/") if s]
        kind = "watch"
        video_id: str | None = None
        if host == "youtu.be":
            if len(segs) == 1:
                video_id = segs[0]
        elif len(segs) == 2 and segs[0] == "shorts":
            kind, video_id = "short", segs[1]
        elif len(segs) == 1 and segs[0] == "watch":
            try:
                values = parse_qs(parts.query).get("v", [])
            except ValueError:
                values = []
            if len(values) == 1:
                video_id = values[0]

        if video_id is None or not _YT_ID_RE.fullmatch(video_id):
            raise _invalid(
                "This YouTube link is not supported. Enter a Shorts or single video link, "
                "or upload the video as a file."
            )
        return ParsedLink(
            site=self.site_id,
            platform_id=video_id,
            kind=kind,
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        )

    def extra_ydl_opts(self, js_runtime: Any = None) -> dict[str, Any]:
        """Enable only the resolved JS runtime; remote components stay off (scripts come from yt-dlp-ejs)."""
        opts: dict[str, Any] = {"remote_components": set()}
        if js_runtime is not None:
            opts["js_runtimes"] = {js_runtime.name: {"path": str(js_runtime.path)}}
        return opts

    def check_info(self, info: Any, max_duration_s: float, max_bytes: int) -> None:
        if not isinstance(info, dict):
            raise self._unreadable(TOOL_OUTDATED_DETAIL)
        if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
            raise PullError(
                "IG_VIDEO_DEGIL",
                "Playlists are not supported; enter a single YouTube video or Shorts link. "
                "Upload the video as a file instead.",
            )
        if info.get("extractor_key") != "Youtube":
            raise self._unreadable(
                f"{TOOL_OUTDATED_DETAIL} Unexpected extractor: {str(info.get('extractor_key'))[:40]}"
            )
        if info.get("is_live") is True or info.get("live_status") in _LIVE_STATUSES:
            raise PullError(
                "IG_VIDEO_DEGIL",
                "Live streams and upcoming premieres are not supported. "
                "Upload the video as a file instead.",
            )
        duration = info.get("duration")
        if (
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and duration > max_duration_s
        ):
            raise PullError(
                "VIDEO_UZUN",
                f"The video is longer than {fmt_seconds(max_duration_s)} seconds ({round(duration, 1)} s). "
                "Shorten it and upload it as a file.",
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

        chain = exception_chain(exc)
        raw_msg = str(exc) or type(exc).__name__
        detail = sanitize_detail(raw_msg, allowed_paths=allowed_paths)
        text = strip_prefixes(raw_msg).lower().replace("’", "'")

        # 1. Size limit exceeded mid-download (internal exception) or yt-dlp's own message
        if any(isinstance(e, SizeLimitExceeded) for e in chain) or any(k in text for k in _SIZE_KEYS):
            return PullError(
                "DOSYA_BUYUK",
                "The video exceeds the allowed file size. Upload the video as a file instead.",
                detail=detail,
            )

        # 2. JavaScript runtime / challenge solving problems
        if any(k in text for k in _JS_KEYS):
            return PullError(
                "JS_ORTAMI_YOK",
                "Setup is incomplete: YouTube downloads need a working JavaScript runtime. "
                "Finish the setup or upload the video as a file instead.",
                detail=detail,
            )

        # 3. HTTP status: from the exception type first, else only from the "HTTP Error NNN" pattern
        status = http_status(chain, raw_msg)
        if status == 429:
            return self._rate_limit(detail)
        if status in (401, 403, 404):
            return self._inaccessible(detail)
        if status is not None:
            return self._transient(detail)  # 5xx and other HTTP errors

        # 4. Text-based yt-dlp messages (with prefixes stripped)
        if any(k in text for k in _RATE_LIMIT_KEYS):
            return self._rate_limit(detail)
        if any(k in text for k in _NOT_AUDIO_VIDEO_KEYS):
            return PullError(
                "IG_VIDEO_DEGIL",
                "This YouTube video cannot be downloaded (live stream, upcoming premiere or no audio "
                "available). Upload the video as a file instead.",
                detail=detail,
            )
        if any(k in text for k in _INACCESSIBLE_KEYS):
            return self._inaccessible(detail)

        # 5. Network / DNS / timeout
        if any(is_network_exc(e) for e in chain) or any(k in text for k in _NETWORK_KEYS):
            return self._transient(detail)

        # 6. Parsing failure / yt-dlp version mismatch
        if isinstance(exc, (KeyError, TypeError, ValueError)) or any(k in text for k in _PARSE_KEYS):
            return self._unreadable(outdated_detail(raw_msg, allowed_paths))

        # 7. Other temporary errors
        return self._transient(detail)

    @staticmethod
    def _unreadable(detail: str | None) -> PullError:
        return PullError(
            "IG_ARAC_GUNCEL_DEGIL",
            "YouTube's response could not be read. Upload the video as a file instead.",
            detail=detail,
        )

    @staticmethod
    def _rate_limit(detail: str | None) -> PullError:
        return PullError(
            "IG_RATE_LIMIT",
            "YouTube has temporarily limited requests from this IP address. "
            "Try again later or upload the video as a file.",
            detail=detail,
        )

    @staticmethod
    def _inaccessible(detail: str | None) -> PullError:
        return PullError(
            "IG_ERISILEMEDI",
            "This YouTube video could not be accessed (it may be private, deleted, age-restricted "
            "or members-only). Upload the video as a file instead.",
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
        return SourceMeta(
            kind=self.site_id,
            url=parsed.canonical_url,
            platform_id=parsed.platform_id,
            title=_clean_str(info.get("title")),
            author=_clean_str(info.get("channel")) or _clean_str(info.get("uploader")),
            published_at=_published_at(info),
            duration_s=round(duration_s, 2) if duration_s is not None else None,
            stats={
                "views": int_or_none(info.get("view_count")),
                "likes": int_or_none(info.get("like_count")),
                "comments": int_or_none(info.get("comment_count")),
            },
        )
