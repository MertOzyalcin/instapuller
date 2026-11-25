"""CLI'ı sahte yt-dlp ile alt süreçte çalıştırır (test dosyası değil).

Kullanım: python tests/_cli_fake_run.py fetch <url> --out-dir <klasör> ...
"""

import sys
from functools import partial
from pathlib import Path

import instapuller.__main__ as cli
from instapuller.downloader import fetch_video


class _NoWait:
    def acquire(self, *, wait=True):
        return 0.0


class _FakeYDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return {
            "id": "Chunk8-jurw",
            "extractor_key": "Instagram",
            "title": "Türkçe başlık ığüşöç ĞÜŞİÖÇ",
            "description": "Türkçe başlık ığüşöç ĞÜŞİÖÇ",
            "channel": "instagram",
            "timestamp": 1661529231,
            "like_count": 7,
            "comment_count": 3,
            "view_count": None,
        }

    def process_ie_result(self, info, download=True):
        out = Path(self.opts["paths"]["home"]) / self.opts["outtmpl"].replace("%(ext)s", "mp4")
        out.write_bytes(b"\x00" * 2048)
        return info


cli.fetch_video = partial(
    fetch_video,
    ydl_factory=_FakeYDL,
    duration_prober=lambda path, ffprobe_bin=None: 12.5,
    limiter=_NoWait(),
)
raise SystemExit(cli.main(sys.argv[1:]))
