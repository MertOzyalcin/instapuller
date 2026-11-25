"""İndirilen geçici video dosyalarını güvenli silme."""

from __future__ import annotations

from pathlib import Path


def cleanup_file(path: Path | str) -> bool:
    """Belirtilen dosyayı siler. Dosya silindiyse True, zaten yoksa False döner."""
    p = Path(path)
    if not p.exists():
        return False
    if p.is_dir():
        raise IsADirectoryError("cleanup_file yalnızca tekil dosya siler, klasör silmez.")
    p.unlink(missing_ok=True)
    return True
