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
from instapuller.rate_limit import DEFAULT_COOLDOWN_S, MIN_COOLDOWN_S
from instapuller.sanitize import sanitize_detail

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130


def _emit(payload: dict) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


class _ArgError(Exception):
    """argparse hatasını JSON'a çevirmek için iç istisna."""


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise _ArgError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        prog="instapuller",
        description="Anonim Instagram video/Reel indirme ve metrik aracı (JSON CLI).",
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_JsonArgumentParser)

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
        help=f"İstekler arası bekleme süresi, saniye (varsayılan ve alt sınır: {int(MIN_COOLDOWN_S)}).",
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
        help="Cooldown durum dosyasının yolu (varsayılan: kullanıcıya özgü sabit yol).",
    )

    clean_p = sub.add_parser("cleanup", help="İndirilen geçici video dosyasını güvenli biçimde siler.")
    clean_p.add_argument("path", type=Path, help="Silinecek video dosyasının yolu.")

    return parser


def _arg_error(detail: str) -> int:
    err = PullError(
        "ARG_GECERSIZ",
        "Komut satırı argümanları geçersiz.",
        detail=sanitize_detail(detail),
    )
    _emit(err.to_dict())
    return EXIT_ERROR


def _run(argv: list[str] | None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        return _arg_error(str(exc))
    except SystemExit as exc:  # -h/--help: JSON üretmeyen tek istisna
        return int(exc.code or 0)

    if args.command == "fetch":
        if not (args.cooldown >= MIN_COOLDOWN_S):
            return _arg_error(f"--cooldown en az {int(MIN_COOLDOWN_S)} saniye olmalı.")
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
        except PullError as err:
            _emit(err.to_dict())
            return EXIT_ERROR
        except Exception as exc:
            _emit(_unexpected(exc).to_dict())
            return EXIT_ERROR
        _emit(result.to_dict())
        return EXIT_OK

    # cleanup
    try:
        deleted = cleanup_file(args.path)
    except Exception as exc:
        err = PullError(
            "SILME_HATASI",
            "Dosya silinemedi.",
            detail=sanitize_detail(f"{type(exc).__name__}: {exc}"),
        )
        _emit(err.to_dict())
        return EXIT_ERROR
    _emit({"ok": True, "deleted": deleted})
    return EXIT_OK


def _unexpected(exc: BaseException) -> PullError:
    return PullError(
        "BEKLENMEYEN_HATA",
        "Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin.",
        detail=sanitize_detail(f"{type(exc).__name__}: {exc}"),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:
        _emit(PullError("BEKLENMEYEN_HATA", "İşlem iptal edildi.").to_dict())
        return EXIT_INTERRUPTED
    except Exception as exc:  # son çare: çıktı her zaman JSON
        _emit(_unexpected(exc).to_dict())
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
