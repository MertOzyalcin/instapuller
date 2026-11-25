"""İndirilen videonun süresini ffprobe ile ölçme."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from instapuller.models import PullError
from instapuller.sanitize import sanitize_detail


def resolve_ffprobe(ffprobe_bin: str | Path | None = None) -> str:
    candidates: list[str] = []
    if ffprobe_bin:
        candidates.append(str(ffprobe_bin))
    for env_key in ("INSTAPULLER_FFPROBE_BIN", "FFPROBE_BIN"):
        val = os.environ.get(env_key)
        if val:
            candidates.append(val)

    for cand in candidates:
        p = Path(cand)
        if p.is_file():
            return str(p)
        found = shutil.which(cand)
        if found:
            return found

    found = shutil.which("ffprobe")
    if found:
        return found

    raise PullError(
        "FFPROBE_YOK",
        "Kurulum eksik: videonun süresi ölçülemiyor. Kurulumu tamamlayıp tekrar deneyin.",
        detail="ffprobe bulunamadı; --ffprobe-bin veya INSTAPULLER_FFPROBE_BIN ile yolunu verin ya da PATH'e ekleyin.",
    )


def probe_duration(
    video_path: Path,
    *,
    ffprobe_bin: str | Path | None = None,
) -> float:
    """ffprobe ile videonun süresini saniye cinsinden döndürür."""
    exe = resolve_ffprobe(ffprobe_bin)
    try:
        proc = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PullError(
            "DOSYA_BOZUK",
            "İndirilen video okunamadı. Videoyu dosya olarak yükleyin.",
            detail=sanitize_detail(str(exc)),
        ) from None

    if proc.returncode != 0:
        raise PullError(
            "DOSYA_BOZUK",
            "İndirilen video bozuk görünüyor veya okunamadı. Videoyu dosya olarak yükleyin.",
            detail=sanitize_detail(proc.stderr),
        )

    raw_out = (proc.stdout or "").strip()
    try:
        duration = float(raw_out)
    except ValueError:
        raise PullError(
            "DOSYA_BOZUK",
            "İndirilen videonun süresi belirlenemedi. Videoyu dosya olarak yükleyin.",
            detail=sanitize_detail(f"ffprobe çıktısı geçersiz: {raw_out}"),
        ) from None

    if duration <= 0:
        raise PullError(
            "DOSYA_BOZUK",
            "İndirilen videonun süresi geçersiz. Videoyu dosya olarak yükleyin.",
        )

    return duration
