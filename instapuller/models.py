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


# Tek doğruluk kaynağı: hata kodu -> upload_fallback.
ERROR_CODES: dict[str, bool] = {
    "LINK_GECERSIZ": True,
    "IG_ERISILEMEDI": True,
    "IG_RATE_LIMIT": True,
    "IG_ARAC_GUNCEL_DEGIL": True,
    "IG_VIDEO_DEGIL": True,
    "IG_COOLDOWN": True,
    "DOSYA_BUYUK": True,
    "VIDEO_UZUN": True,
    "DOSYA_BOZUK": True,
    "KAYNAK_GECICI": True,
    "FFPROBE_YOK": False,
    "ARG_GECERSIZ": False,
    "SILME_HATASI": False,
    "BEKLENMEYEN_HATA": False,
}


# IG_ARAC_GUNCEL_DEGIL için teknik açıklama (kullanıcı mesajına girmez, detail'e gider).
TOOL_OUTDATED_DETAIL = "yt-dlp güncel olmayabilir; README'deki güncelleme adımına bakın."


class SizeLimitExceeded(Exception):
    """İndirme sırasında boyut sınırı aşıldığında progress hook'tan fırlatılan iç istisna."""


class PullError(Exception):
    """autocript SourceError sözleşmesine uyumlu yapılandırılmış hata.

    upload_fallback değeri ERROR_CODES tablosundan okunur; tabloda olmayan kod ValueError verir.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: str | None = None,
        retry_after_s: float | None = None,
    ):
        if code not in ERROR_CODES:
            raise ValueError(f"Bilinmeyen hata kodu: {code!r}")
        text = f"{code}: {message}" + (f" | {detail}" if detail else "")
        super().__init__(text)
        self.code = code
        self.message = message
        self.upload_fallback: bool = ERROR_CODES[code]
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
