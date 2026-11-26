"""Helpers shared by site adapters (error-chain inspection, host normalisation, value coercion)."""

from __future__ import annotations

import re
import socket
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yt_dlp.networking.exceptions import TransportError

from instapuller.models import TOOL_OUTDATED_DETAIL
from instapuller.sanitize import MAX_DETAIL_LEN, sanitize_detail

PREFIX_RE = re.compile(r"^\s*(?:ERROR:\s*|WARNING:\s*|\[[^\]]+\]\s*[^\s:]+:\s*)", re.I)
HTTP_STATUS_RE = re.compile(r"HTTP Error (\d{3})")

FS_ERRORS = (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError, FileExistsError)


def strip_prefixes(msg: str) -> str:
    """Strip the "ERROR: " and "[Extractor] <id>: " prefixes added by yt-dlp."""
    prev = None
    while prev != msg:
        prev = msg
        msg = PREFIX_RE.sub("", msg, count=1)
    return msg


def exception_chain(exc: BaseException) -> list[BaseException]:
    """Walk the DownloadError.exc_info, ExtractorError.cause/exc_info, __cause__ and __context__ chain."""
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


def http_status(chain: list[BaseException], raw_msg: str) -> int | None:
    for e in chain:
        status = getattr(e, "status", None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
    for e in chain:
        m = HTTP_STATUS_RE.search(str(e))
        if m:
            return int(m.group(1))
    m = HTTP_STATUS_RE.search(raw_msg)
    return int(m.group(1)) if m else None


def is_network_exc(e: BaseException) -> bool:
    if isinstance(e, TransportError | TimeoutError | socket.timeout):
        return True
    return isinstance(e, OSError) and not isinstance(e, FS_ERRORS)


def normalize_host(raw_host: str | None) -> str:
    host = (raw_host or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def int_or_none(val: Any) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int) and val >= 0:
        return val
    return None


def iso_utc(ts: Any) -> str | None:
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def fmt_seconds(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(value, 1))


def outdated_detail(raw_msg: str, allowed_paths: Iterable[str | Path]) -> str:
    budget = MAX_DETAIL_LEN - len(TOOL_OUTDATED_DETAIL) - len(" Original error: ")
    original = sanitize_detail(raw_msg, max_len=max(budget, 0), allowed_paths=allowed_paths)
    return f"{TOOL_OUTDATED_DETAIL} Original error: {original}" if original else TOOL_OUTDATED_DETAIL
