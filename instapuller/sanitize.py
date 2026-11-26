"""Sanitize error details: hide local file paths and limit length."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

MAX_DETAIL_LEN = 500

# Windows drive paths (C:\..., D:/... but not http:// or https://) and UNC paths (\\server\...)
_WIN_PATH_RE = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:(?:\\|/(?!/))|\\\\)[^\s'\"<>|]+")
# POSIX local paths (/Users/..., /home/..., /tmp/..., /var/..., etc.)
_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:._-])/(?:Users|home|tmp|var|etc|opt|srv|root|mnt|media|private)/[^\s'\"<>|]+"
)


def sanitize_detail(
    raw: str | None,
    *,
    max_len: int = MAX_DETAIL_LEN,
    allowed_paths: Iterable[str | Path] = (),
) -> str | None:
    """Remove local paths from an error detail and return at most `max_len` characters."""
    if not raw:
        return None

    allowed_norm = {str(Path(p)).lower() for p in allowed_paths if p}

    def _mask(match: re.Match[str]) -> str:
        token = match.group(0).rstrip(".,;:)]}")
        if token.lower() in allowed_norm:
            return match.group(0)
        suffix = match.group(0)[len(token):]
        return "[path]" + suffix

    cleaned = _WIN_PATH_RE.sub(_mask, raw)
    cleaned = _POSIX_PATH_RE.sub(_mask, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return None
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3].rstrip() + "..."
    return cleaned
