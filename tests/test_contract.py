"""Contract shape, carousel rejection, duration/size checks and CLI JSON tests (mocked network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from instapuller.__main__ import main
from instapuller.cleanup import cleanup_file
from instapuller.downloader import fetch_video
from instapuller.models import PullError


class NoWaitLimiter:
    """Fake limiter that never waits (the cooldown cannot be disabled in production code)."""

    def acquire(self, *, wait: bool = True) -> float:
        return 0.0


class FakeYDL:
    def __init__(self, opts: dict[str, Any], info: dict[str, Any], write_bytes: int | None = 1024):
        self.opts = opts
        self.info = info
        self.write_bytes = write_bytes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url: str, download: bool = False):
        return self.info

    def process_ie_result(self, info: dict[str, Any], download: bool = True):
        if download and self.write_bytes is not None:
            out_path = Path(self.opts["paths"]["home"]) / self.opts["outtmpl"].replace("%(ext)s", "mp4")
            out_path.write_bytes(b"\x00" * self.write_bytes)
        return info


def _sample_info() -> dict[str, Any]:
    return {
        "id": "Chunk8-jurw",
        "extractor_key": "Instagram",
        "uploader": "Instagram",
        "channel": "instagram",
        "title": "Video by instagram",
        "description": "Short test caption",
        "timestamp": 1661529231,
        "view_count": None,
        "like_count": 972107,
        "comment_count": 15704,
        "ext": "mp4",
    }


def test_fetch_video_contract_matches_autocript(tmp_path):
    state = tmp_path / "state.json"
    out_dir = tmp_path / "out"

    res = fetch_video(
        "https://www.instagram.com/reel/Chunk8-jurw/",
        out_dir=out_dir,
        state_file=state,
        limiter=NoWaitLimiter(),
        ydl_factory=lambda opts: FakeYDL(opts, _sample_info(), write_bytes=2048),
        duration_prober=lambda path, ffprobe_bin=None: 42.346,
    )

    payload = res.to_dict()
    assert payload["ok"] is True
    assert payload["keep_video"] is False  # Off by default
    assert Path(payload["video_path"]).is_file()

    meta = payload["meta"]
    assert set(meta.keys()) == {
        "kind",
        "url",
        "platform_id",
        "title",
        "author",
        "published_at",
        "duration_s",
        "stats",
    }
    assert meta["kind"] == "instagram"
    assert meta["platform_id"] == "Chunk8-jurw"
    assert meta["url"] == "https://www.instagram.com/reel/Chunk8-jurw/"
    assert meta["author"] == "instagram"
    assert meta["title"] == "Short test caption"
    assert meta["published_at"] == "2022-08-26T15:53:51Z"
    assert meta["duration_s"] == 42.35
    assert set(meta["stats"].keys()) == {"views", "likes", "comments"}
    assert meta["stats"] == {"views": None, "likes": 972107, "comments": 15704}

    # cleanup_file must delete the file
    assert cleanup_file(res.video_path) is True
    assert not res.video_path.exists()
    assert cleanup_file(res.video_path) is False


def test_carousel_rejected_with_ig_video_degil(tmp_path):
    carousel_info = {
        "_type": "playlist",
        "entries": [{"id": "1"}, {"id": "2"}],
    }
    with pytest.raises(PullError) as exc_info:
        fetch_video(
            "https://www.instagram.com/p/MultiPost123/",
            out_dir=tmp_path / "out",
            state_file=tmp_path / "state.json",
            limiter=NoWaitLimiter(),
            ydl_factory=lambda opts: FakeYDL(opts, carousel_info),
            duration_prober=lambda path, ffprobe_bin=None: 10.0,
        )
    assert exc_info.value.code == "IG_VIDEO_DEGIL"
    assert exc_info.value.upload_fallback is True


def test_video_over_180s_rejected_and_file_deleted(tmp_path):
    out_dir = tmp_path / "out"
    with pytest.raises(PullError) as exc_info:
        fetch_video(
            "https://www.instagram.com/reel/LongReel123/",
            out_dir=out_dir,
            state_file=tmp_path / "state.json",
            limiter=NoWaitLimiter(),
            max_duration_s=180.0,
            ydl_factory=lambda opts: FakeYDL(opts, _sample_info(), write_bytes=1024),
            duration_prober=lambda path, ffprobe_bin=None: 185.2,
        )
    assert exc_info.value.code == "VIDEO_UZUN"
    assert exc_info.value.upload_fallback is True
    # The downloaded file must be gone
    assert list(out_dir.glob("*")) == []


def test_file_exceeding_max_size_rejected_and_deleted(tmp_path):
    out_dir = tmp_path / "out"
    with pytest.raises(PullError) as exc_info:
        fetch_video(
            "https://www.instagram.com/reel/BigReel123/",
            out_dir=out_dir,
            state_file=tmp_path / "state.json",
            limiter=NoWaitLimiter(),
            max_filesize_mb=1,  # 1 MB limit
            ydl_factory=lambda opts: FakeYDL(opts, _sample_info(), write_bytes=2 * 1024 * 1024),
            duration_prober=lambda path, ffprobe_bin=None: 10.0,
        )
    assert exc_info.value.code == "DOSYA_BUYUK"
    assert exc_info.value.upload_fallback is True
    assert list(out_dir.glob("*")) == []


def test_cli_returns_json_error_for_disallowed_site(capsys):
    rc = main(["fetch", "https://www.youtube.com/shorts/dQw4w9WgXcQ"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "LINK_GECERSIZ"
    assert out["error"]["upload_fallback"] is True
