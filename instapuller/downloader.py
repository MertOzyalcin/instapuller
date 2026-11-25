"""Ana indirme akışı: URL doğrulama → cooldown → yt-dlp → boyut/süre denetimi → sözleşme."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp

from instapuller.models import TOOL_OUTDATED_DETAIL, PullError, PullResult, SizeLimitExceeded
from instapuller.probe import probe_duration
from instapuller.sanitize import MAX_DETAIL_LEN, sanitize_detail
from instapuller.rate_limit import (
    DEFAULT_COOLDOWN_S,
    MIN_COOLDOWN_S,
    CooldownLimiter,
    default_state_file,
)
from instapuller.sites import parse_url

DEFAULT_MAX_DURATION_S = 185.0
DEFAULT_MAX_FILESIZE_MB = 200
STAGING_PREFIX = "ip_"
STALE_STAGING_AGE_S = 3600.0  # bundan eski ip_* staging klasörleri silinir
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp")

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _fmt_seconds(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(value, 1))


def _safe_slug(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _SAFE_SLUG_RE.sub("_", text.strip()).strip("._-")
    return cleaned[:40]


_ANSI_RE = re.compile(r"\[[0-9;]*[A-Za-z]")
MAX_WARNING_LINES = 10
MAX_WARNING_LEN = 200
WARNINGS_LABEL = " | yt-dlp uyarıları: "


def _quiet_stderr() -> bool:
    return os.environ.get("INSTAPULLER_QUIET", "").strip() == "1"


class _SilentLogger:
    """stdout'a hiçbir şey basmaz. warning/error satırlarını tanı için biriktirir ve stderr'e yazar.

    "max-filesize" atlama satırını ayrıca bayrakla kaydeder.
    """

    def __init__(self) -> None:
        self.size_exceeded = False
        self.warnings: list[str] = []

    def _note(self, msg: str) -> None:
        if "larger than max-filesize" in str(msg):
            self.size_exceeded = True

    def _diag(self, msg: object) -> None:
        text = _ANSI_RE.sub("", str(msg))
        clean = sanitize_detail(text, max_len=MAX_WARNING_LEN)
        if not clean:
            return
        if len(self.warnings) < MAX_WARNING_LINES:
            self.warnings.append(clean)
        if _quiet_stderr():
            return
        try:
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            print(f"instapuller: yt-dlp uyarısı: {clean}", file=sys.stderr, flush=True)
        except Exception:
            pass

    def debug(self, msg: str) -> None:
        self._note(msg)

    def info(self, msg: str) -> None:
        self._note(msg)

    def warning(self, msg: str) -> None:
        self._note(msg)
        self._diag(msg)

    def error(self, msg: str) -> None:
        self._note(msg)
        self._diag(msg)


def _with_warnings(err: PullError, logger: _SilentLogger, allowed_paths: list[Path]) -> PullError:
    """Biriken yt-dlp uyarılarını detail sonuna ekler (orijinal metin öncelikli, toplam <= 500)."""
    if not logger.warnings:
        return err
    base = err.detail or ""
    prefix = base + WARNINGS_LABEL if base else WARNINGS_LABEL.lstrip(" |").lstrip()
    room = MAX_DETAIL_LEN - len(prefix)
    if room < 10:
        return err
    extra = " ; ".join(logger.warnings)
    if len(extra) > room:
        extra = extra[: room - 3].rstrip() + "..."
    err.detail = sanitize_detail(prefix + extra, allowed_paths=allowed_paths)
    return err


def _make_size_hook(max_bytes: int) -> Callable[[dict[str, Any]], None]:
    def hook(status: dict[str, Any]) -> None:
        for key in ("downloaded_bytes", "total_bytes", "total_bytes_estimate"):
            val = status.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val > max_bytes:
                raise SizeLimitExceeded(f"{key}={int(val)} > {max_bytes}")

    return hook


def _purge_stale_staging(target_dir: Path) -> None:
    """Önceki (öldürülmüş) çağrılardan kalan eski ip_* staging klasörlerini sessizce siler."""
    cutoff = time.time() - STALE_STAGING_AGE_S
    try:
        entries = list(target_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if (
                entry.name.startswith(STAGING_PREFIX)
                and entry.is_dir()
                and not entry.is_symlink()
                and entry.stat().st_mtime < cutoff
            ):
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _find_downloaded_file(result: Any, info: Any, staging_dir: Path) -> Path | None:
    """yt-dlp'nin bildirdiği dosya yolunu (requested_downloads) kullanır; yoksa klasörü tarar."""
    for source in (result, info):
        if not isinstance(source, dict):
            continue
        candidates: list[Any] = []
        reqs = source.get("requested_downloads")
        if isinstance(reqs, list):
            candidates.extend(r.get("filepath") for r in reqs if isinstance(r, dict))
        candidates.append(source.get("filepath"))
        for cand in candidates:
            if not cand:
                continue
            p = Path(cand)
            try:
                if p.is_file() and p.resolve().parent == staging_dir.resolve():
                    return p
            except OSError:
                continue
    files = sorted(
        p
        for p in staging_dir.iterdir()
        if p.is_file() and not p.name.endswith(_PARTIAL_SUFFIXES) and ".part-Frag" not in p.name
    )
    return files[0] if files else None


def _default_ydl_factory(opts: dict[str, Any]) -> Any:
    return yt_dlp.YoutubeDL(opts)


def _check_carousel_or_non_video(info: Any) -> None:
    if not isinstance(info, dict):
        raise PullError(
            "IG_ARAC_GUNCEL_DEGIL",
            "Instagram'dan gelen yanıt okunamadı. Videoyu dosya olarak yükleyin.",
            detail=TOOL_OUTDATED_DETAIL,
        )

    if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
        raise PullError(
            "IG_VIDEO_DEGIL",
            "Çoklu gönderiler (carousel) desteklenmiyor; yalnızca tekil video veya Reel linki girin. "
            "Videoyu dosya olarak yükleyin.",
        )

    if info.get("extractor_key") != "Instagram":
        raise PullError(
            "IG_ARAC_GUNCEL_DEGIL",
            "Instagram'dan gelen yanıt okunamadı. Videoyu dosya olarak yükleyin.",
            detail=f"{TOOL_OUTDATED_DETAIL} Beklenmeyen çıkarıcı: {str(info.get('extractor_key'))[:40]}",
        )

    vcodec = info.get("vcodec")
    if vcodec == "none" and not info.get("formats"):
        raise PullError(
            "IG_VIDEO_DEGIL",
            "Bu gönderide indirilebilir video bulunamadı. Videoyu dosya olarak yükleyin.",
        )


def _exceeds_precheck_filesize(info: dict[str, Any], max_bytes: int) -> bool:
    for key in ("filesize", "filesize_approx"):
        val = info.get(key)
        if isinstance(val, (int, float)) and val > max_bytes:
            return True
    return False


def fetch_video(
    url: str,
    *,
    out_dir: Path | str | None = None,
    keep_video: bool = False,
    wait: bool = True,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
    max_filesize_mb: int = DEFAULT_MAX_FILESIZE_MB,
    state_file: Path | str | None = None,
    ffprobe_bin: str | Path | None = None,
    ydl_factory: Callable[[dict[str, Any]], Any] = _default_ydl_factory,
    duration_prober: Callable[..., float] = probe_duration,
    limiter: CooldownLimiter | None = None,
) -> PullResult:
    """İzinli siteden tekil videoyu anonim olarak indirir ve autocript uyumlu sonuç döner."""
    # 1. URL doğrulama ve site izin listesi kontrolü (ağ isteğinden ve cooldown'dan önce)
    adapter, parsed = parse_url(url)

    # 2. Ardışık istek koruması (cooldown)
    active_limiter = limiter or CooldownLimiter(
        Path(state_file) if state_file is not None else default_state_file(),
        cooldown_s=max(float(cooldown_s), MIN_COOLDOWN_S),
    )
    active_limiter.acquire(wait=wait)

    # 3. Hedef klasör belirleme (saklama varsayılan olarak kapalı)
    if out_dir is not None:
        target_dir = Path(out_dir)
    elif keep_video:
        target_dir = Path.cwd() / "downloads"
    else:
        target_dir = Path(tempfile.gettempdir()) / "instapuller"

    target_dir.mkdir(parents=True, exist_ok=True)
    _purge_stale_staging(target_dir)
    allowed_paths = [target_dir]
    max_bytes = max(1, int(max_filesize_mb)) * 1024 * 1024

    # 4. Geçici çalışma klasörü içinde indirme (hata olursa temiz bırakır)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{STAGING_PREFIX}{parsed.platform_id}_", dir=target_dir))
    final_path: Path | None = None

    try:
        logger = _SilentLogger()
        ydl_opts: dict[str, Any] = {
            # Klasör outtmpl'e girmez (klasör adındaki % karakterleri şablon sayılmasın)
            "paths": {"home": str(staging_dir), "temp": str(staging_dir)},
            "outtmpl": f"{parsed.platform_id}.%(ext)s",
            "allowed_extractors": ["Instagram"],
            "progress_hooks": [_make_size_hook(max_bytes)],
            "format": "mp4/best",
            "max_filesize": max_bytes,
            "noplaylist": True,
            "retries": 0,
            "fragment_retries": 0,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": logger,
        }

        try:
            with ydl_factory(ydl_opts) as ydl:
                info = ydl.extract_info(parsed.canonical_url, download=False)
                _check_carousel_or_non_video(info)
                if _exceeds_precheck_filesize(info, max_bytes):
                    raise PullError(
                        "DOSYA_BUYUK",
                        f"Video {max_filesize_mb} MB sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                    )
                result = ydl.process_ie_result(info, download=True)
        except Exception as exc:
            raise _with_warnings(
                adapter.map_error(exc, allowed_paths=allowed_paths), logger, allowed_paths
            ) from None

        staged_file = _find_downloaded_file(result, info, staging_dir)
        if staged_file is None:
            # yt-dlp max_filesize aşıldığında istisna fırlatmadan dosyayı atlayabilir
            if logger.size_exceeded or _exceeds_precheck_filesize(info, max_bytes):
                raise _with_warnings(
                    PullError(
                        "DOSYA_BUYUK",
                        f"Video {max_filesize_mb} MB sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                    ),
                    logger,
                    allowed_paths,
                )
            raise _with_warnings(PullError(
                "KAYNAK_GECICI",
                "Video şu anda indirilemedi. Biraz sonra tekrar deneyin veya videoyu dosya olarak yükleyin.",
                detail="İndirme tamamlandı ama klasörde video dosyası oluşmadı (boyut sınırına takılmış veya boş yanıt olabilir).",
            ), logger, allowed_paths)

        if staged_file.stat().st_size > max_bytes:
            raise PullError(
                "DOSYA_BUYUK",
                f"Video {max_filesize_mb} MB sınırını aşıyor. Videoyu dosya olarak yükleyin.",
            )

        # 5. İndirme sonrası ffprobe ile kesin süre ölçümü
        duration_s = duration_prober(staged_file, ffprobe_bin=ffprobe_bin)
        if duration_s > max_duration_s:
            raise PullError(
                "VIDEO_UZUN",
                f"Video {_fmt_seconds(max_duration_s)} saniyeyi aşıyor ({round(duration_s, 1)} sn). "
                "Videoyu kısaltıp dosya olarak yükleyin.",
            )

        # 6. Dosyayı hedef klasöre taşı
        ext = staged_file.suffix or ".mp4"
        author_slug = _safe_slug(info.get("channel") or info.get("uploader"))
        if keep_video:
            base = f"{author_slug}_{parsed.platform_id}" if author_slug else parsed.platform_id
            candidate = target_dir / f"{base}{ext}"
            if candidate.exists():
                candidate = target_dir / f"{base}_{secrets.token_hex(3)}{ext}"
        else:
            candidate = target_dir / f"{parsed.platform_id}_{secrets.token_hex(4)}{ext}"

        shutil.move(str(staged_file), str(candidate))
        final_path = candidate

        meta = adapter.extract_meta(info, parsed, duration_s=duration_s)
        return PullResult(video_path=final_path, meta=meta, keep_video=keep_video)

    except BaseException:
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
