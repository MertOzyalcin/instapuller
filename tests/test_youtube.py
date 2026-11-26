"""YouTube support: parser, error mapping, contract, download flow with a fake yt-dlp, JS runtime, CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import instapuller.jsruntime as jsr
from instapuller.__main__ import main
from instapuller.cleanup import cleanup_file
from instapuller.downloader import fetch_video
from instapuller.jsruntime import resolve_js_runtime, runtime_name_for
from instapuller.models import ERROR_CODES, PullError
from instapuller.sites import parse_url
from instapuller.sites.youtube import YouTubeAdapter

VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"


class NoWaitLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self, *, wait: bool = True) -> float:
        self.acquired += 1
        return 0.0


class FakeYDL:
    instances: list[FakeYDL] = []

    def __init__(self, opts: dict[str, Any], info: dict[str, Any], ext: str = "m4a", size: int = 2048):
        self.opts, self.info, self.ext, self.size = opts, info, ext, size
        self.extract_calls = 0
        self.download_calls = 0
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        self.extract_calls += 1
        self.url = url
        return self.info

    def process_ie_result(self, info, download=True):
        self.download_calls += 1
        out = Path(self.opts["paths"]["home"]) / self.opts["outtmpl"].replace("%(ext)s", self.ext)
        out.write_bytes(b"\x00" * self.size)
        return info


def yt_info(**extra: Any) -> dict[str, Any]:
    info = {
        "id": VID,
        "extractor_key": "Youtube",
        "title": " Sample Video Title ",
        "channel": "Sample Channel",
        "uploader": "Sample Uploader",
        "timestamp": 1661529231,
        "duration": 30,
        "view_count": 1000,
        "like_count": 50,
        "comment_count": 7,
        "ext": "m4a",
    }
    info.update(extra)
    return info


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    FakeYDL.instances.clear()
    monkeypatch.delenv("INSTAPULLER_JS_RUNTIME_BIN", raising=False)


@pytest.fixture
def qjs(tmp_path):
    p = tmp_path / "bin" / "qjs.exe"
    p.parent.mkdir()
    p.write_bytes(b"")
    return p


def _fetch(tmp_path, info, qjs, url=CANON, **kw):
    kw.setdefault("duration_prober", lambda path, ffprobe_bin=None: 12.0)
    kw.setdefault("limiter", NoWaitLimiter())
    return fetch_video(
        url,
        out_dir=tmp_path / "out",
        js_runtime_bin=qjs,
        ydl_factory=lambda opts: FakeYDL(opts, info),
        **kw,
    )


# --- parser ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, kind",
    [
        (f"https://www.youtube.com/shorts/{VID}", "short"),
        (f"https://youtube.com/shorts/{VID}/", "short"),
        (f"http://m.youtube.com/shorts/{VID}?si=abc&feature=share", "short"),
        (f"youtube.com/shorts/{VID}", "short"),
        (f"https://www.youtube.com/watch?v={VID}", "watch"),
        (f"https://m.youtube.com/watch?v={VID}&t=42s&list=PL1&index=3&si=x", "watch"),
        (f"https://youtube.com/watch/?feature=share&v={VID}", "watch"),
        (f"https://youtu.be/{VID}", "watch"),
        (f"youtu.be/{VID}/?si=abc&t=10", "watch"),
        (f"http://www.youtube.com/watch?v={VID}", "watch"),
    ],
)
def test_parse_valid_youtube_links(raw, kind):
    adapter, parsed = parse_url(raw)
    assert adapter.site_id == "youtube"
    assert parsed.site == "youtube"
    assert parsed.platform_id == VID
    assert parsed.kind == kind
    assert parsed.canonical_url == CANON


@pytest.mark.parametrize(
    "bad",
    [
        "https://www.youtube.com/playlist?list=PL12345",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/channel/UC1234567890",
        f"https://www.youtube.com/live/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=short",
        f"https://www.youtube.com/watch?v={VID}x",
        f"https://www.youtube.com/watch?v={VID}&v={VID}",
        f"https://www.youtube.com/shorts/{VID[:10]}",
        f"https://www.youtube.com/shorts/{VID}/extra",
        f"https://youtu.be/{VID[:5]}",
        f"https://youtu.be/{VID}/more",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://www.youtube-nocookie.com/embed/{VID}",
        f"https://user@www.youtube.com/watch?v={VID}",
        f"https://www.youtube.com:8080/watch?v={VID}",
        f"ftp://www.youtube.com/watch?v={VID}",
        f"https://notyoutube.com/watch?v={VID}",
    ],
)
def test_reject_invalid_youtube_links(bad):
    with pytest.raises(PullError) as exc_info:
        parse_url(bad)
    assert exc_info.value.code == "LINK_GECERSIZ"
    assert exc_info.value.upload_fallback is True


def test_generic_messages_are_site_neutral():
    with pytest.raises(PullError) as exc_info:
        parse_url("")
    assert "Instagram or YouTube" in exc_info.value.message


# --- error mapping --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg, code",
    [
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "IG_ERISILEMEDI"),
        ("ERROR: [youtube] abc: Video unavailable", "IG_ERISILEMEDI"),
        ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate", "IG_ERISILEMEDI"),
        ("ERROR: [youtube] abc: Join this channel to get access to members-only content", "IG_ERISILEMEDI"),
        ("HTTP Error 404: Not Found", "IG_ERISILEMEDI"),
        ("HTTP Error 429: Too Many Requests", "IG_RATE_LIMIT"),
        ("ERROR: [youtube] abc: Sign in to confirm you\u2019re not a bot", "IG_RATE_LIMIT"),
        ("ERROR: [youtube] abc: This live event will begin in 2 hours.", "IG_VIDEO_DEGIL"),
        ("ERROR: [youtube] abc: Premieres in 3 days", "IG_VIDEO_DEGIL"),
        ("ERROR: Requested format is not available. Use --list-formats", "IG_VIDEO_DEGIL"),
        ("ERROR: [youtube] abc: Unable to extract initial data", "IG_ARAC_GUNCEL_DEGIL"),
        ("ERROR: [youtube] Unable to download webpage: <urlopen error timed out>", "KAYNAK_GECICI"),
        ("HTTP Error 503: Service Unavailable", "KAYNAK_GECICI"),
        ("File is larger than max-filesize (209715200 bytes)", "DOSYA_BUYUK"),
        ("WARNING: [youtube] No supported JavaScript runtime could be found.", "JS_ORTAMI_YOK"),
        ("ERROR: [youtube] abc: n challenge solving failed: Some formats may be missing", "JS_ORTAMI_YOK"),
    ],
)
def test_map_error_categories(msg, code):
    err = YouTubeAdapter().map_error(RuntimeError(msg))
    assert err.code == code
    assert err.upload_fallback is True
    assert "Instagram" not in err.message


def test_key_error_maps_to_tool_outdated():
    assert YouTubeAdapter().map_error(KeyError("x")).code == "IG_ARAC_GUNCEL_DEGIL"


def test_js_ortami_yok_in_table_with_upload_fallback():
    assert ERROR_CODES["JS_ORTAMI_YOK"] is True
    assert PullError("JS_ORTAMI_YOK", "x").to_dict()["error"]["upload_fallback"] is True


# --- contract / download flow ---------------------------------------------------------------------------


def test_contract_shape_and_audio_only_options(tmp_path, qjs):
    limiter = NoWaitLimiter()
    res = _fetch(tmp_path, yt_info(), qjs, url=f"https://youtu.be/{VID}?si=x", limiter=limiter)
    payload = res.to_dict()
    assert payload["ok"] is True and payload["keep_video"] is False
    meta = payload["meta"]
    assert set(meta) == {"kind", "url", "platform_id", "title", "author", "published_at", "duration_s", "stats"}
    assert meta["kind"] == "youtube"
    assert meta["platform_id"] == VID
    assert meta["url"] == CANON
    assert meta["title"] == "Sample Video Title"
    assert meta["author"] == "Sample Channel"
    assert meta["published_at"] == "2022-08-26T15:53:51Z"
    assert meta["duration_s"] == 12.0
    assert meta["stats"] == {"views": 1000, "likes": 50, "comments": 7}
    assert res.video_path.suffix == ".m4a"
    assert res.video_path.name.startswith(f"{VID}_")
    assert limiter.acquired == 1

    ydl = FakeYDL.instances[0]
    assert ydl.url == CANON
    assert ydl.opts["format"] == "bestaudio[ext=m4a]/bestaudio"
    assert ydl.opts["allowed_extractors"] == ["Youtube"]
    assert ydl.opts["js_runtimes"] == {"quickjs": {"path": str(qjs)}}
    assert ydl.opts["remote_components"] == set()
    assert "postprocessors" not in ydl.opts
    assert cleanup_file(res.video_path) is True


def test_published_at_falls_back_to_upload_date(tmp_path, qjs):
    info = yt_info(timestamp=None, upload_date="20240102")
    assert _fetch(tmp_path, info, qjs).meta.published_at == "2024-01-02T00:00:00Z"


def test_deno_runtime_name_in_opts(tmp_path):
    deno = tmp_path / "deno.exe"
    deno.write_bytes(b"")
    _fetch(tmp_path, yt_info(), deno)
    assert FakeYDL.instances[0].opts["js_runtimes"] == {"deno": {"path": str(deno)}}


def test_duration_precheck_rejects_before_download(tmp_path, qjs):
    with pytest.raises(PullError) as exc_info:
        _fetch(tmp_path, yt_info(duration=500), qjs, max_duration_s=185.0)
    assert exc_info.value.code == "VIDEO_UZUN"
    ydl = FakeYDL.instances[0]
    assert ydl.extract_calls == 1 and ydl.download_calls == 0


def test_ffprobe_duration_check_still_runs_after_download(tmp_path, qjs):
    with pytest.raises(PullError) as exc_info:
        _fetch(tmp_path, yt_info(duration=None), qjs, duration_prober=lambda p, ffprobe_bin=None: 999.0)
    assert exc_info.value.code == "VIDEO_UZUN"
    assert FakeYDL.instances[0].download_calls == 1
    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.parametrize("extra", [{"is_live": True}, {"live_status": "is_upcoming"}, {"live_status": "is_live"}])
def test_live_is_ig_video_degil(tmp_path, qjs, extra):
    with pytest.raises(PullError) as exc_info:
        _fetch(tmp_path, yt_info(**extra), qjs)
    assert exc_info.value.code == "IG_VIDEO_DEGIL"
    assert FakeYDL.instances[0].download_calls == 0


def test_playlist_and_foreign_extractor_and_non_dict():
    a = YouTubeAdapter()
    for info, code in [
        ({"_type": "playlist", "entries": []}, "IG_VIDEO_DEGIL"),
        (yt_info(extractor_key="Generic"), "IG_ARAC_GUNCEL_DEGIL"),
        (None, "IG_ARAC_GUNCEL_DEGIL"),
    ]:
        with pytest.raises(PullError) as exc_info:
            a.check_info(info, 185.0, 1024)
        assert exc_info.value.code == code


def test_ytdlp_exception_is_mapped_through_youtube_adapter(tmp_path, qjs):
    class Boom(FakeYDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("ERROR: [youtube] x: Private video")

    with pytest.raises(PullError) as exc_info:
        fetch_video(
            CANON,
            out_dir=tmp_path / "out",
            js_runtime_bin=qjs,
            limiter=NoWaitLimiter(),
            ydl_factory=lambda opts: Boom(opts, {}),
        )
    assert exc_info.value.code == "IG_ERISILEMEDI"
    assert "YouTube" in exc_info.value.message


# --- JS runtime resolution ------------------------------------------------------------------------------


def _no_runtime(monkeypatch):
    def no_ffprobe(_=None):
        raise PullError("FFPROBE_YOK", "x")

    monkeypatch.setattr(jsr, "resolve_ffprobe", no_ffprobe)
    monkeypatch.setattr(jsr.shutil, "which", lambda name: None)


def test_missing_runtime_raises_before_limiter_and_network(tmp_path, monkeypatch):
    _no_runtime(monkeypatch)
    limiter = NoWaitLimiter()
    with pytest.raises(PullError) as exc_info:
        fetch_video(
            CANON,
            out_dir=tmp_path / "out",
            limiter=limiter,
            ydl_factory=lambda opts: pytest.fail("yt-dlp must not be created"),
        )
    assert exc_info.value.code == "JS_ORTAMI_YOK"
    assert exc_info.value.upload_fallback is True
    assert limiter.acquired == 0
    assert not (tmp_path / "out").exists()


def test_invalid_url_error_wins_over_missing_runtime(monkeypatch):
    _no_runtime(monkeypatch)
    with pytest.raises(PullError) as exc_info:
        fetch_video("https://www.youtube.com/@chan", limiter=NoWaitLimiter())
    assert exc_info.value.code == "LINK_GECERSIZ"


def test_explicit_path_not_a_file_or_bad_name(tmp_path, monkeypatch):
    _no_runtime(monkeypatch)
    with pytest.raises(PullError) as e1:
        resolve_js_runtime(tmp_path / "nope" / "qjs.exe")
    assert e1.value.code == "JS_ORTAMI_YOK"
    node = tmp_path / "node.exe"
    node.write_bytes(b"")
    with pytest.raises(PullError) as e2:
        resolve_js_runtime(node)
    assert e2.value.code == "JS_ORTAMI_YOK"


def test_runtime_name_inference():
    assert runtime_name_for("C:/x/qjs.exe") == "quickjs"
    assert runtime_name_for("quickjs-ng") == "quickjs"
    assert runtime_name_for("/usr/bin/deno") == "deno"
    assert runtime_name_for("node") is None


def test_resolution_order(tmp_path, monkeypatch):
    explicit = tmp_path / "a" / "deno.exe"
    env = tmp_path / "b" / "qjs.exe"
    nextto = tmp_path / "c" / "qjs.exe"
    onpath = tmp_path / "d" / "deno.exe"
    for p in (explicit, env, nextto, onpath):
        p.parent.mkdir()
        p.write_bytes(b"")
    ffprobe = nextto.parent / "ffprobe.exe"
    ffprobe.write_bytes(b"")
    monkeypatch.setattr(jsr, "resolve_ffprobe", lambda b=None: str(ffprobe))
    monkeypatch.setattr(jsr.shutil, "which", lambda name: str(onpath) if name == "deno" else None)

    monkeypatch.setenv("INSTAPULLER_JS_RUNTIME_BIN", str(env))
    assert resolve_js_runtime(explicit).path == explicit
    assert resolve_js_runtime().path == env
    monkeypatch.delenv("INSTAPULLER_JS_RUNTIME_BIN")
    assert resolve_js_runtime().path.resolve() == nextto.resolve()
    nextto.unlink()
    assert resolve_js_runtime().path == onpath


def test_instagram_never_resolves_js_runtime(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("JS runtime resolved for Instagram")

    import instapuller.downloader as dl

    monkeypatch.setattr(dl, "resolve_js_runtime", boom)
    info = {
        "id": "Chunk8-jurw",
        "extractor_key": "Instagram",
        "channel": "instagram",
        "description": "cap",
        "timestamp": 1661529231,
    }
    res = fetch_video(
        "https://www.instagram.com/reel/Chunk8-jurw/",
        out_dir=tmp_path / "out",
        limiter=NoWaitLimiter(),
        ydl_factory=lambda opts: FakeYDL(opts, info, ext="mp4"),
        duration_prober=lambda p, ffprobe_bin=None: 5.0,
    )
    opts = FakeYDL.instances[0].opts
    assert opts["format"] == "mp4/best"
    assert opts["allowed_extractors"] == ["Instagram"]
    assert "js_runtimes" not in opts and "remote_components" not in opts
    assert res.meta.kind == "instagram"


# --- cleanup and CLI ------------------------------------------------------------------------------------


@pytest.mark.parametrize("ext", ["m4a", "opus", "mp3", "ogg", "aac", "webm", "mp4"])
def test_cleanup_accepts_audio_extensions(tmp_path, ext):
    f = tmp_path / f"{VID}_0123abcd.{ext}"
    f.write_bytes(b"x")
    assert cleanup_file(f) is True


def test_cli_rejects_unknown_runtime_name(capsys, tmp_path):
    rc = main(["fetch", CANON, "--js-runtime-bin", str(tmp_path / "node.exe")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "ARG_GECERSIZ"


def test_cli_passes_js_runtime_bin(monkeypatch, capsys, tmp_path):
    import instapuller.__main__ as cli

    seen = {}

    def fake_fetch(url, **kw):
        seen.update(kw)
        raise PullError("JS_ORTAMI_YOK", "nope")

    monkeypatch.setattr(cli, "fetch_video", fake_fetch)
    rc = main(["fetch", CANON, "--js-runtime-bin", str(tmp_path / "qjs.exe")])
    assert rc == 1
    assert seen["js_runtime_bin"] == tmp_path / "qjs.exe"
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "JS_ORTAMI_YOK"
    assert out["error"]["upload_fallback"] is True


# --- per-site cooldown through fetch_video (real CooldownLimiter, shared state file) ---------------------


def _ig_info() -> dict[str, Any]:
    return {"id": "Chunk8-jurw", "extractor_key": "Instagram", "ext": "mp4", "duration": 10}


def test_fetch_video_uses_separate_cooldown_per_site(tmp_path, qjs):
    state = tmp_path / "state.json"
    common = dict(
        out_dir=tmp_path / "out",
        state_file=state,
        wait=False,
        duration_prober=lambda path, ffprobe_bin=None: 10.0,
    )
    ig_url = "https://www.instagram.com/reel/Chunk8-jurw/"

    fetch_video(ig_url, ydl_factory=lambda o: FakeYDL(o, _ig_info(), ext="mp4"), **common)
    # YouTube right after Instagram: no IG_COOLDOWN, the Instagram slot does not block it.
    fetch_video(CANON, js_runtime_bin=qjs, ydl_factory=lambda o: FakeYDL(o, yt_info()), **common)

    data = json.loads(state.read_text(encoding="utf-8"))
    assert set(data) == {"last_request_at", "written_at", "youtube"}
    ig_slot = data["last_request_at"]

    # Second request of each site inside 15 s is still refused, and refusals write nothing.
    with pytest.raises(PullError) as ig:
        fetch_video(ig_url, ydl_factory=lambda o: FakeYDL(o, _ig_info(), ext="mp4"), **common)
    assert ig.value.code == "IG_COOLDOWN" and ig.value.retry_after_s > 14
    with pytest.raises(PullError) as yt:
        fetch_video(CANON, js_runtime_bin=qjs, ydl_factory=lambda o: FakeYDL(o, yt_info()), **common)
    assert yt.value.code == "IG_COOLDOWN"
    assert json.loads(state.read_text(encoding="utf-8"))["last_request_at"] == ig_slot


def test_fetch_video_youtube_only_state_file(tmp_path, qjs):
    """autocript passes a separate state file per site; a youtube-only file must work."""
    state = tmp_path / "instapuller-youtube.json"
    common = dict(
        out_dir=tmp_path / "out",
        state_file=state,
        wait=False,
        js_runtime_bin=qjs,
        duration_prober=lambda path, ffprobe_bin=None: 10.0,
    )
    fetch_video(CANON, ydl_factory=lambda o: FakeYDL(o, yt_info()), **common)
    data = json.loads(state.read_text(encoding="utf-8"))
    assert set(data) == {"youtube"}
    with pytest.raises(PullError) as yt:
        fetch_video(CANON, ydl_factory=lambda o: FakeYDL(o, yt_info()), **common)
    assert yt.value.code == "IG_COOLDOWN"
