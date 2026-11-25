"""Veri modelleri ve autocript uyumlu sözleşme tipleri."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ParsedLink:
    site: str
    platform_id: str
    kind: str
    canonical_url: str


@dataclass
class SourceMeta:
    kind: str
    url: str | None
    platform_id: str | None
    title: str | None
    author: str | None
    published_at: str | None  # ISO 8601 UTC
    duration_s: float | None
    stats: dict[str, int | None] = field(
        default_factory=lambda: {"views": None, "likes": None, "comments": None}
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "platform_id": self.platform_id,
            "title": self.title,
            "author": self.author,
            "published_at": self.published_at,
            "duration_s": self.duration_s,
            "stats": {
                "views": self.stats.get("views"),
                "likes": self.stats.get("likes"),
                "comments": self.stats.get("comments"),
            },
        }


class PullError(Exception):
    """autocript SourceError sözleşmesine uyumlu yapılandırılmış hata."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        upload_fallback: bool = True,
        detail: str | None = None,
        retry_after_s: float | None = None,
    ):
        text = f"{code}: {message}" + (f" | {detail}" if detail else "")
        super().__init__(text)
        self.code = code
        self.message = message
        self.upload_fallback = upload_fallback
        self.detail = detail
        self.retry_after_s = retry_after_s

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "upload_fallback": self.upload_fallback,
        }
        if self.retry_after_s is not None:
            err["retry_after_s"] = self.retry_after_s
        if self.detail is not None:
            err["detail"] = self.detail
        return {"ok": False, "error": err}


@dataclass
class PullResult:
    video_path: Path
    meta: SourceMeta
    keep_video: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "video_path": str(self.video_path),
            "keep_video": self.keep_video,
            "meta": self.meta.to_dict(),
        }
