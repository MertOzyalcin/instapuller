"""Ağsız entegrasyon testleri için ortak yardımcılar (test dosyası değil)."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yt_dlp


class NoWaitLimiter:
    """Hiç beklemeyen sahte limiter (üretim kodunda cooldown kapatılamaz)."""

    def acquire(self, *, wait: bool = True) -> float:
        return 0.0


def instagram_info(media_url: str, *, video_id: str = "Chunk8-jurw", **extra: Any) -> dict[str, Any]:
    info = {
        "id": video_id,
        "extractor": "instagram",
        "extractor_key": "Instagram",
        "title": "Türkçe başlık ığüşöç ĞÜŞİÖÇ",
        "description": "Türkçe başlık ığüşöç ĞÜŞİÖÇ",
        "uploader": "Instagram",
        "channel": "instagram",
        "timestamp": 1661529231,
        "like_count": 5,
        "comment_count": 2,
        "view_count": None,
        "ext": "mp4",
        "formats": [
            {
                "format_id": "v1",
                "url": media_url,
                "ext": "mp4",
                "protocol": "http",
                "vcodec": "h264",
                "acodec": "aac",
            }
        ],
    }
    info.update(extra)
    return info


def real_ydl_factory(info: dict[str, Any]):
    """Gerçek YoutubeDL; yalnızca extract_info sahte (ağsız). process_ie_result gerçek."""

    class _YDL(yt_dlp.YoutubeDL):
        def extract_info(self, url, download=False, *a, **k):  # noqa: ANN001
            return copy.deepcopy(info)

    def factory(opts: dict[str, Any]):
        return _YDL({**opts, "enable_file_urls": True})

    return factory


class _Counter:
    def __init__(self) -> None:
        self.sent = 0


def serve_directory(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):  # noqa: ANN002, ANN003
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def serve_chunked(total_bytes: int, chunk: int = 64 * 1024) -> tuple[ThreadingHTTPServer, str, _Counter]:
    """Content-Length göndermeyen (chunked) sunucu; gönderilen baytı sayar."""
    counter = _Counter()
    payload = os.urandom(chunk)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a, **k):  # noqa: ANN002, ANN003
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while counter.sent < total_bytes:
                    self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                    self.wfile.flush()
                    counter.sent += len(payload)
                    time.sleep(0.002)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/video.mp4", counter


def find_tool(env_names: tuple[str, ...], exe: str) -> str:
    for name in env_names:
        val = os.environ.get(name)
        if val and Path(val).is_file():
            return val
    found = shutil.which(exe)
    if found:
        return found
    pytest.skip(f"{exe} bulunamadı ({'/'.join(env_names)} ya da PATH)")


def find_ffmpeg() -> str:
    return find_tool(("INSTAPULLER_TEST_FFMPEG",), "ffmpeg")


def find_ffprobe() -> str:
    return find_tool(("INSTAPULLER_FFPROBE_BIN",), "ffprobe")


def make_video(path: Path, seconds: int) -> None:
    """Test anında küçük H.264 video üretir (repoya medya girmez)."""
    ffmpeg = find_ffmpeg()
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1",
            "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "51", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
