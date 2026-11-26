"""Measure the duration of a downloaded video with ffprobe."""

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
        "Setup is incomplete: the video duration cannot be measured. Finish the setup and try again.",
        detail="ffprobe not found; pass its path with --ffprobe-bin or INSTAPULLER_FFPROBE_BIN, or add it to PATH.",
    )


def probe_duration(
    video_path: Path,
    *,
    ffprobe_bin: str | Path | None = None,
) -> float:
    """Return the video duration in seconds using ffprobe."""
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
            "The downloaded video could not be read. Upload the video as a file instead.",
            detail=sanitize_detail(str(exc)),
        ) from None

    if proc.returncode != 0:
        raise PullError(
            "DOSYA_BOZUK",
            "The downloaded video appears to be corrupt or unreadable. Upload the video as a file instead.",
            detail=sanitize_detail(proc.stderr),
        )

    raw_out = (proc.stdout or "").strip()
    try:
        duration = float(raw_out)
    except ValueError:
        raise PullError(
            "DOSYA_BOZUK",
            "The duration of the downloaded video could not be determined. Upload the video as a file instead.",
            detail=sanitize_detail(f"Invalid ffprobe output: {raw_out}"),
        ) from None

    if duration <= 0:
        raise PullError(
            "DOSYA_BOZUK",
            "The downloaded video has an invalid duration. Upload the video as a file instead.",
        )

    return duration
