"""CLI with JSON output (for subprocess integration)."""

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
from instapuller.jsruntime import runtime_name_for
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
    """Internal exception used to turn argparse errors into JSON."""


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise _ArgError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        prog="instapuller",
        description="Anonymous Instagram and YouTube (Shorts) video download and metrics tool (JSON CLI).",
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_JsonArgumentParser)

    fetch_p = sub.add_parser("fetch", help="Download the video at the given link and return its metrics.")
    fetch_p.add_argument("url", help="Instagram Reel/video or YouTube Shorts/video link.")
    fetch_p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory to download the video into.",
    )
    fetch_p.add_argument(
        "--keep-video",
        action="store_true",
        default=False,
        help="Keep the video permanently (default: off / temporary).",
    )
    wait_group = fetch_p.add_mutually_exclusive_group()
    wait_group.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        default=True,
        help="Wait out the cooldown, then continue (default).",
    )
    wait_group.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="If the cooldown has not elapsed, return IG_COOLDOWN without waiting.",
    )
    fetch_p.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN_S,
        help=f"Delay between requests in seconds (default and minimum: {int(MIN_COOLDOWN_S)}).",
    )
    fetch_p.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION_S,
        help=f"Maximum video duration in seconds (default: {int(DEFAULT_MAX_DURATION_S)}).",
    )
    fetch_p.add_argument(
        "--max-size-mb",
        type=int,
        default=DEFAULT_MAX_FILESIZE_MB,
        help=f"Maximum file size in MB (default: {DEFAULT_MAX_FILESIZE_MB}).",
    )
    fetch_p.add_argument(
        "--ffprobe-bin",
        type=Path,
        default=None,
        help="Path to the ffprobe executable (optional).",
    )
    fetch_p.add_argument(
        "--js-runtime-bin",
        type=Path,
        default=None,
        help="Path to the JavaScript runtime (qjs or deno) used for YouTube (optional; "
        "env INSTAPULLER_JS_RUNTIME_BIN).",
    )
    fetch_p.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Path to the cooldown state file (default: a fixed per-user path).",
    )

    clean_p = sub.add_parser("cleanup", help="Safely delete a downloaded temporary video file.")
    clean_p.add_argument("path", type=Path, help="Path of the video file to delete.")

    return parser


def _arg_error(detail: str) -> int:
    err = PullError(
        "ARG_GECERSIZ",
        "Invalid command-line arguments.",
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
    except SystemExit as exc:  # -h/--help: the only case that does not produce JSON
        return int(exc.code or 0)

    if args.command == "fetch":
        if not (args.cooldown >= MIN_COOLDOWN_S):
            return _arg_error(f"--cooldown must be at least {int(MIN_COOLDOWN_S)} seconds.")
        if args.js_runtime_bin is not None and runtime_name_for(args.js_runtime_bin) is None:
            return _arg_error(
                "--js-runtime-bin must point to a file named qjs/quickjs or deno (e.g. qjs.exe, deno.exe)."
            )
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
                js_runtime_bin=args.js_runtime_bin,
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
            "The file could not be deleted.",
            detail=sanitize_detail(f"{type(exc).__name__}: {exc}"),
        )
        _emit(err.to_dict())
        return EXIT_ERROR
    _emit({"ok": True, "deleted": deleted})
    return EXIT_OK


def _unexpected(exc: BaseException) -> PullError:
    return PullError(
        "BEKLENMEYEN_HATA",
        "An unexpected error occurred. Please try again.",
        detail=sanitize_detail(f"{type(exc).__name__}: {exc}"),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:
        _emit(PullError("BEKLENMEYEN_HATA", "The operation was cancelled.").to_dict())
        return EXIT_INTERRUPTED
    except Exception as exc:  # last resort: output is always JSON
        _emit(_unexpected(exc).to_dict())
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
