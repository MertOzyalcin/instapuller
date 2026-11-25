"""Kullanıcı mesajları, süre sınırı, cooldown alt sınırı (S1/S2) ve varsayılan state yolu (Ö1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import instapuller.probe as probe_mod
from instapuller.downloader import DEFAULT_MAX_DURATION_S, fetch_video
from instapuller.models import ERROR_CODES, PullError
from instapuller.rate_limit import MIN_COOLDOWN_S, CooldownLimiter, default_state_file
from instapuller.sites import parse_url
from instapuller.sites.instagram import InstagramAdapter
from tests.test_contract import FakeYDL, NoWaitLimiter, _sample_info
from tests.test_rate_limit import FakeClock

FORBIDDEN = ("yt-dlp", "ffprobe", "README", "--", "PATH")


def _fetch(tmp_path: Path, *, info: Any = None, write_bytes: int | None = 1024, duration=10.0, **kw):
    return fetch_video(
        "https://www.instagram.com/reel/Chunk8-jurw/",
        out_dir=tmp_path / "out",
        limiter=NoWaitLimiter(),
        ydl_factory=lambda opts: FakeYDL(opts, _sample_info() if info is None else info, write_bytes),
        duration_prober=lambda path, ffprobe_bin=None: duration,
        **kw,
    )


def _error_from(fn) -> PullError:
    with pytest.raises(PullError) as exc_info:
        fn()
    return exc_info.value


def _collect_errors(tmp_path: Path, monkeypatch) -> list[PullError]:
    errs: list[PullError] = []
    adapter = InstagramAdapter()

    # map_error dalları
    for exc in (
        RuntimeError("HTTP Error 429: Too Many Requests"),
        RuntimeError("File is larger than max-filesize"),
        RuntimeError("no video formats found"),
        RuntimeError("Instagram sent an empty media response"),
        KeyError("x"),
        RuntimeError("connection reset"),
    ):
        errs.append(adapter.map_error(exc))

    # downloader yolları
    errs.append(_error_from(lambda: _fetch(tmp_path, info={"_type": "playlist", "entries": []})))
    errs.append(_error_from(lambda: _fetch(tmp_path, info=["bozuk"])))
    errs.append(_error_from(lambda: _fetch(tmp_path, write_bytes=None)))  # dosya inmedi
    errs.append(_error_from(lambda: _fetch(tmp_path, duration=500.0)))
    errs.append(_error_from(lambda: _fetch(tmp_path, write_bytes=2 * 1024 * 1024, max_filesize_mb=1)))

    # link ayrıştırma
    for bad in ("", "ftp://instagram.com/reel/abcde/", "https://example.com/x", "https://instagram.com/", "%%%"):
        errs.append(_error_from(lambda bad=bad: parse_url(bad)))

    # probe yolları
    monkeypatch.delenv("INSTAPULLER_FFPROBE_BIN", raising=False)
    monkeypatch.delenv("FFPROBE_BIN", raising=False)
    monkeypatch.setattr(probe_mod.shutil, "which", lambda *_a, **_k: None)
    errs.append(_error_from(lambda: probe_mod.resolve_ffprobe(None)))

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(probe_mod, "resolve_ffprobe", lambda _b=None: "ffprobe")
    monkeypatch.setattr(probe_mod.subprocess, "run", lambda *a, **k: _Proc())
    errs.append(_error_from(lambda: probe_mod.probe_duration(Path("x.mp4"))))

    # cooldown
    clock = FakeClock()
    lim = CooldownLimiter(tmp_path / "s.json", clock=clock, sleeper=clock.sleep)
    lim.acquire()
    errs.append(_error_from(lambda: lim.acquire(wait=False)))
    return errs


def test_user_messages_have_no_technical_terms_and_fallback_ones_point_to_upload(tmp_path, monkeypatch):
    errs = _collect_errors(tmp_path, monkeypatch)
    codes = {e.code for e in errs}
    assert {
        "IG_RATE_LIMIT",
        "DOSYA_BUYUK",
        "IG_VIDEO_DEGIL",
        "IG_ERISILEMEDI",
        "IG_ARAC_GUNCEL_DEGIL",
        "KAYNAK_GECICI",
        "VIDEO_UZUN",
        "LINK_GECERSIZ",
        "FFPROBE_YOK",
        "DOSYA_BOZUK",
        "IG_COOLDOWN",
    } <= codes
    for e in errs:
        assert e.code in ERROR_CODES
        for word in FORBIDDEN:
            assert word not in e.message, (e.code, e.message)
        if e.upload_fallback:
            assert "yükleyin" in e.message, (e.code, e.message)


def test_tool_outdated_message_and_detail(tmp_path):
    err = InstagramAdapter().map_error(RuntimeError("Unable to extract video url"))
    assert err.code == "IG_ARAC_GUNCEL_DEGIL"
    assert err.message == "Instagram'dan gelen yanıt okunamadı. Videoyu dosya olarak yükleyin."
    assert err.detail.startswith("yt-dlp güncel olmayabilir; README'deki güncelleme adımına bakın.")
    assert "Unable to extract video url" in err.detail


def test_ffprobe_missing_details_go_to_detail(monkeypatch):
    monkeypatch.delenv("INSTAPULLER_FFPROBE_BIN", raising=False)
    monkeypatch.delenv("FFPROBE_BIN", raising=False)
    monkeypatch.setattr(probe_mod.shutil, "which", lambda *_a, **_k: None)
    err = _error_from(lambda: probe_mod.resolve_ffprobe(None))
    assert err.code == "FFPROBE_YOK"
    assert err.upload_fallback is False
    assert "--ffprobe-bin" in err.detail and "INSTAPULLER_FFPROBE_BIN" in err.detail


# --- süre sınırı -------------------------------------------------------------


def test_default_max_duration_is_185():
    assert DEFAULT_MAX_DURATION_S == 185.0


def test_duration_184_9_passes_and_185_2_rejected_with_number_from_limit(tmp_path):
    ok = _fetch(tmp_path, duration=184.9)
    assert ok.meta.duration_s == 184.9

    err = _error_from(lambda: _fetch(tmp_path, duration=185.2))
    assert err.code == "VIDEO_UZUN"
    assert "185 saniyeyi" in err.message

    err = _error_from(lambda: _fetch(tmp_path, duration=61.0, max_duration_s=60.0))
    assert "60 saniyeyi" in err.message and "185" not in err.message


# --- S1 / S2 -----------------------------------------------------------------


def test_cooldown_cannot_go_below_15(tmp_path):
    assert CooldownLimiter(tmp_path / "s.json", cooldown_s=0).cooldown_s == 15
    assert CooldownLimiter(tmp_path / "s.json", cooldown_s=-3).cooldown_s == MIN_COOLDOWN_S
    assert CooldownLimiter(tmp_path / "s.json", cooldown_s=40).cooldown_s == 40


def test_fetch_video_clamps_cooldown_to_15(tmp_path):
    state = tmp_path / "s.json"
    CooldownLimiter(state).acquire()  # gerçek saatle az önce istek yapılmış
    err = _error_from(
        lambda: fetch_video(
            "https://www.instagram.com/reel/Chunk8-jurw/",
            out_dir=tmp_path / "out",
            state_file=state,
            cooldown_s=0,
            wait=False,
        )
    )
    assert err.code == "IG_COOLDOWN"
    assert 14 < err.retry_after_s <= 15


def test_future_state_never_sleeps_longer_than_cooldown(tmp_path):
    import json

    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"last_request_at": clock.now + 10000}), encoding="utf-8")
    lim = CooldownLimiter(state, cooldown_s=15.0, clock=clock, sleeper=clock.sleep)
    waited = lim.acquire(wait=True)
    assert waited <= 15.0
    assert clock.slept == [15.0]
    assert json.loads(state.read_text(encoding="utf-8"))["last_request_at"] == 1015.0


def test_no_wait_does_not_write_state(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    lim = CooldownLimiter(state, clock=clock, sleeper=clock.sleep)
    lim.acquire()
    before = state.read_text(encoding="utf-8")
    clock.now += 1
    with pytest.raises(PullError):
        lim.acquire(wait=False)
    assert state.read_text(encoding="utf-8") == before


# --- Ö1 ----------------------------------------------------------------------


def test_default_state_file_env_override(monkeypatch, tmp_path):
    target = tmp_path / "x" / "rl.json"
    monkeypatch.setenv("INSTAPULLER_STATE_FILE", str(target))
    assert default_state_file() == target


def test_default_state_file_windows_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("INSTAPULLER_STATE_FILE", raising=False)
    monkeypatch.setattr("instapuller.rate_limit.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_state_file() == tmp_path / "instapuller" / "rate_limit.json"


def test_default_state_file_posix_uses_home_state(monkeypatch, tmp_path):
    monkeypatch.delenv("INSTAPULLER_STATE_FILE", raising=False)
    monkeypatch.setattr("instapuller.rate_limit.sys.platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_state_file() == tmp_path / ".local" / "state" / "instapuller" / "rate_limit.json"


def test_default_state_file_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("INSTAPULLER_STATE_FILE", raising=False)
    before = default_state_file()
    monkeypatch.chdir(tmp_path)
    assert default_state_file() == before
    assert before.is_absolute()


def test_parallel_queue_slots_advance_15s_apart(tmp_path):
    """Aynı anda gelen istekler (sleeper atlanarak) 0/15/30 sn yuvalara dizilir."""
    now = 5000.0
    slept: list[float] = []
    state = tmp_path / "s.json"
    delays = []
    for _ in range(3):
        lim = CooldownLimiter(state, clock=lambda: now, sleeper=slept.append)
        delays.append(lim.acquire(wait=True))
    assert delays == [0.0, 15.0, 30.0]
    assert slept == [15.0, 30.0]
