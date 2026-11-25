"""Ana indirme akışı: URL doğrulama → cooldown → yt-dlp → boyut/süre denetimi → sözleşme."""

from __future__ import annotations

import re
import secrets
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp

from instapuller.models import PullError, PullResult
from instapuller.probe import probe_duration
from instapuller.rate_limit import DEFAULT_COOLDOWN_S, CooldownLimiter
from instapuller.sites import parse_url

DEFAULT_MAX_DURATION_S = 180.0
DEFAULT_MAX_FILESIZE_MB = 200
DEFAULT_STATE_FILE = Path(".state") / "rate_limit.json"

_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_slug(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _SAFE_SLUG_RE.sub("_", text.strip()).strip("._-")
    return cleaned[:40]


class _SilentLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def _default_ydl_factory(opts: dict[str, Any]) -> Any:
    return yt_dlp.YoutubeDL(opts)


def _check_carousel_or_non_video(info: Any) -> None:
    if not isinstance(info, dict):
        raise PullError(
            "IG_ARAC_GUNCEL_DEGIL",
            "Instagram yanıtı beklenmeyen bir biçimde geldi; yt-dlp güncel olmayabilir. "
            "README'deki güncelleme komutunu çalıştırın veya videoyu dosya olarak yükleyin.",
            upload_fallback=True,
        )

    if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
        raise PullError(
            "IG_VIDEO_DEGIL",
            "Çoklu gönderiler (carousel) desteklenmiyor; yalnızca tekil video veya Reel linki girin. "
            "Videoyu dosya olarak yükleyin.",
            upload_fallback=True,
        )

    vcodec = info.get("vcodec")
    if vcodec == "none" and not info.get("formats"):
        raise PullError(
            "IG_VIDEO_DEGIL",
            "Bu gönderide indirilebilir video bulunamadı. Videoyu dosya olarak yükleyin.",
            upload_fallback=True,
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
        Path(state_file) if state_file is not None else DEFAULT_STATE_FILE,
        cooldown_s=cooldown_s,
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
    allowed_paths = [target_dir]
    max_bytes = max(1, int(max_filesize_mb)) * 1024 * 1024

    # 4. Geçici çalışma klasörü içinde indirme (hata olursa temiz bırakır)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"ip_{parsed.platform_id}_", dir=target_dir))
    final_path: Path | None = None

    try:
        ydl_opts: dict[str, Any] = {
            "outtmpl": str(staging_dir / f"{parsed.platform_id}.%(ext)s"),
            "format": "mp4/best",
            "max_filesize": max_bytes,
            "noplaylist": True,
            "retries": 0,
            "fragment_retries": 0,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _SilentLogger(),
        }

        try:
            with ydl_factory(ydl_opts) as ydl:
                info = ydl.extract_info(parsed.canonical_url, download=False)
                _check_carousel_or_non_video(info)
                if _exceeds_precheck_filesize(info, max_bytes):
                    raise PullError(
                        "DOSYA_BUYUK",
                        f"Video {max_filesize_mb} MB sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                        upload_fallback=True,
                    )
                ydl.process_ie_result(info, download=True)
        except Exception as exc:
            raise adapter.map_error(exc, allowed_paths=allowed_paths) from None

        downloaded = [p for p in staging_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        if not downloaded:
            # yt-dlp max_filesize aşıldığında sessizce atlayabilir
            if _exceeds_precheck_filesize(info, max_bytes):
                raise PullError(
                    "DOSYA_BUYUK",
                    f"Video {max_filesize_mb} MB sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                    upload_fallback=True,
                )
            raise PullError(
                "IG_INDIRME_HATASI",
                "Video dosyası indirilemedi (boyut sınırına takılmış veya boş dönmüş olabilir). "
                "Videoyu dosya olarak yükleyin.",
                upload_fallback=True,
            )

        staged_file = downloaded[0]
        if staged_file.stat().st_size > max_bytes:
            raise PullError(
                "DOSYA_BUYUK",
                f"Video {max_filesize_mb} MB sınırını aşıyor. Videoyu dosya olarak yükleyin.",
                upload_fallback=True,
            )

        # 5. İndirme sonrası ffprobe ile kesin süre ölçümü
        duration_s = duration_prober(staged_file, ffprobe_bin=ffprobe_bin)
        if duration_s > max_duration_s:
            raise PullError(
                "VIDEO_UZUN",
                f"Video {int(max_duration_s)} saniyeyi aşıyor ({round(duration_s, 1)} sn). "
                "Bu sürüm en fazla 3 dakikalık videoları işler.",
                upload_fallback=True,
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
