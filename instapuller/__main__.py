"""JSON çıktılı CLI arayüzü (subprocess entegrasyonu için)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from instapuller.cleanup import cleanup_file
from instapuller.downloader import (
    DEFAULT_MAX_DURATION_S,
    DEFAULT_MAX_FILESIZE_MB,
    fetch_video,
)
from instapuller.models import PullError
from instapuller.rate_limit import DEFAULT_COOLDOWN_S
from instapuller.sanitize import sanitize_detail


def _emit(payload: dict) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instapuller",
        description="Anonim Instagram video/Reel indirme ve metrik aracı (JSON CLI).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="Verilen linkteki videoyu indirir ve metrikleri döner.")
    fetch_p.add_argument("url", help="Instagram Reel veya tekil video linki.")
    fetch_p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Videonun indirileceği klasör.",
    )
    fetch_p.add_argument(
        "--keep-video",
        action="store_true",
        default=False,
        help="Videoyu kalıcı olarak sakla (varsayılan: kapalı / geçici).",
    )
    wait_group = fetch_p.add_mutually_exclusive_group()
    wait_group.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        default=True,
        help="Cooldown süresini bekleyip devam et (varsayılan).",
    )
    wait_group.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="Cooldown dolmadıysa beklemeden IG_COOLDOWN hatası döndür.",
    )
    fetch_p.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN_S,
        help=f"İstekler arası bekleme süresi, saniye (varsayılan: {int(DEFAULT_COOLDOWN_S)}).",
    )
    fetch_p.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION_S,
        help=f"Azami video süresi, saniye (varsayılan: {int(DEFAULT_MAX_DURATION_S)}).",
    )
    fetch_p.add_argument(
        "--max-size-mb",
        type=int,
        default=DEFAULT_MAX_FILESIZE_MB,
        help=f"Azami dosya boyutu, MB (varsayılan: {DEFAULT_MAX_FILESIZE_MB}).",
    )
    fetch_p.add_argument(
        "--ffprobe-bin",
        type=Path,
        default=None,
        help="ffprobe yürütülebilir dosyasının yolu (opsiyonel).",
    )
    fetch_p.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Cooldown durum dosyasının yolu (opsiyonel).",
    )

    clean_p = sub.add_parser("cleanup", help="İndirilen geçici video dosyasını güvenli biçimde siler.")
    clean_p.add_argument("path", type=Path, help="Silinecek video dosyasının yolu.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "fetch":
        try:
            result = fetch_video(
                args.url,
                out_dir=args.out_dir,
                keep_video=args.keep_video,
                wait=args.wait,
                cooldown_s=args.cooldown,
                max_duration_s=args.max_duration,
                max_filesize_mb=args.max_size_mb,
                state_file=args.state_file,
                ffprobe_bin=args.ffprobe_bin,
            )
            _emit(result.to_dict())
            return 0
        except PullError as err:
            _emit(err.to_dict())
            return 1
        except Exception as exc:
            err = PullError(
                "IG_INDIRME_HATASI",
                "Beklenmeyen bir hata oluştu. Videoyu dosya olarak yükleyin.",
                upload_fallback=True,
                detail=sanitize_detail(str(exc)),
            )
            _emit(err.to_dict())
            return 1

    if args.command == "cleanup":
        try:
            deleted = cleanup_file(args.path)
            _emit({"ok": True, "deleted": deleted})
            return 0
        except Exception as exc:
            err = PullError(
                "SILME_HATASI",
                "Dosya silinemedi.",
                upload_fallback=False,
                detail=sanitize_detail(str(exc)),
            )
            _emit(err.to_dict())
            return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
