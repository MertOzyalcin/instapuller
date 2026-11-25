"""İndirilen geçici video dosyalarını güvenli silme."""

from __future__ import annotations

import re
from pathlib import Path

# instapuller'ın saklamayan modda ürettiği ad: <kod>_<8 hex>.<uzantı>
_GENERATED_NAME_RE = re.compile(r"[A-Za-z0-9_-]{5,}_[0-9a-f]{8}\.(?:mp4|mov|webm|mkv|m4v)", re.I)


def cleanup_file(path: Path | str) -> bool:
    """instapuller'ın ürettiği geçici video dosyasını siler.

    Silindiyse True, zaten yoksa False döner. Başka adlı dosyalar için ValueError fırlatır.
    """
    p = Path(path)
    if p.is_dir():
        raise IsADirectoryError("cleanup_file yalnızca tekil dosya siler, klasör silmez.")
    if not _GENERATED_NAME_RE.fullmatch(p.name):
        raise ValueError("Yalnızca instapuller'ın ürettiği geçici video dosyaları silinebilir.")
    if not p.exists():
        return False
    p.unlink(missing_ok=True)
    return True
