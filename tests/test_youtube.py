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
            a.check_info(info, 185.0)
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


@pytest.mark.parametrize("bad", ["node.exe", ""])
def test_cli_bad_runtime_name_for_youtube_is_js_ortami_yok(capsys, tmp_path, bad):
    arg = str(tmp_path / bad) if bad else bad
    rc = main(["fetch", CANON, "--js-runtime-bin", arg, "--out-dir", str(tmp_path / "o")])
    assert rc == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "JS_ORTAMI_YOK"
    assert err["upload_fallback"] is True


def test_cli_bad_runtime_does_not_mask_invalid_youtube_url(capsys):
    rc = main(["fetch", "https://www.youtube.com/@chan", "--js-runtime-bin", "node.exe"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "LINK_GECERSIZ"


@pytest.mark.parametrize("bad", ["node.exe", ""])
def test_cli_instagram_ignores_bad_js_runtime_bin(monkeypatch, capsys, tmp_path, bad):
    from functools import partial

    import instapuller.__main__ as cli

    info = {"id": "Chunk8-jurw", "extractor_key": "Instagram", "ext": "mp4", "duration": 10}
    monkeypatch.setattr(
        cli,
        "fetch_video",
        partial(
            fetch_video,
            ydl_factory=lambda o: FakeYDL(o, info, ext="mp4"),
            duration_prober=lambda path, ffprobe_bin=None: 10.0,
            limiter=NoWaitLimiter(),
        ),
    )
    url = "https://www.instagram.com/reel/Chunk8-jurw/"
    rc = main(["fetch", url, "--js-runtime-bin", bad, "--out-dir", str(tmp_path / "o")])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


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


# --- JS challenge failures reported only as yt-dlp warnings ---------------------------------------------
#
# yt-dlp 2026.11.23 reports a missing JS runtime and failed n/sig challenges only via report_warning
# (-> logger.warning) and then fails with a generic error. The tests below use the REAL texts:
# the "no runtime" warning is produced by running yt-dlp's own YoutubeIE code with our logger; the
# challenge-remaining templates (nested in a long extraction method) are copied from _video.py and
# a guard test checks they still exist in the installed yt-dlp source.

import yt_dlp  # noqa: E402
from yt_dlp.utils import DownloadError, ExtractorError  # noqa: E402

from instapuller.downloader import _SilentLogger  # noqa: E402

_YT_DIR = Path(yt_dlp.__file__).parent / "extractor" / "youtube"
_EJS_HELP = (
    "Ensure you have a supported JavaScript runtime and challenge solver script distribution installed. "
    "Review any warnings presented before this message. "
    "For more details, refer to  https://github.com/yt-dlp/yt-dlp/wiki/EJS"
)
# yt-dlp 2026.11.23, extractor/youtube/_video.py:3364 and :3368 (via IE.report_warning with video_id)
SIG_SOLVING_WARNING = f"[youtube] {VID}: Signature solving failed: Some formats may be missing. {_EJS_HELP}"
N_SOLVING_WARNING = f"[youtube] {VID}: n challenge solving failed: Some formats may be missing. {_EJS_HELP}"
# yt-dlp 2026.11.23, extractor/youtube/jsc/_builtin/ejs.py:~236 and :~242 (self.logger.warning)
EJS_VERSION_WARNING = (
    "Challenge solver lib script version 0.0.1 is not supported "
    "(source: python package, variant: minified, supported version: 0.4.0)"
)
EJS_HASH_WARNING = (
    "Hash mismatch on challenge solver lib script (source: python package, variant: minified, hash: abc)!"
)
# yt-dlp 2026.11.23, extractor/youtube/jsc/_director.py:~170-195 (self.logger.warning)
EJS_SKIPPED_WARNING = (
    "Remote component challenge solver script (deno) was skipped. It may be required to solve JS challenges. "
    "You can enable the download with  --remote-components ejs:github  (recommended). "
    "For more information and alternatives, refer to  https://github.com/yt-dlp/yt-dlp/wiki/EJS"
)
# yt-dlp 2026.11.23, extractor/youtube/_video.py:4095 ("reason += ...")
CAPTCHA_MESSAGE = "[youtube] abc: Sign in to confirm. YouTube is requiring a captcha challenge before playback"
FORMAT_UNAVAILABLE = "Requested format is not available. Use --list-formats for a list of available formats"


def _real_no_runtime_warning() -> str:
    """Run yt-dlp's real client-selection code without any JS runtime and capture its warning."""
    logger = _SilentLogger()
    opts = {
        "logger": logger,
        "js_runtimes": {"quickjs": {"path": "does/not/exist/qjs.exe"}},
        "remote_components": set(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ie = ydl.get_info_extractor("Youtube")
        ie.set_downloader(ydl)
        ie.initialize()
        ie._get_requested_clients(CANON, {}, False)
    assert logger.warnings, "yt-dlp did not warn about the missing JS runtime"
    return logger.warnings[0]


def test_real_ytdlp_no_runtime_warning_is_classified():
    warning = _real_no_runtime_warning()
    assert "No supported JavaScript runtime" in warning
    exc = DownloadError(f"ERROR: [youtube] {VID}: {FORMAT_UNAVAILABLE}")
    assert YouTubeAdapter().map_error(exc, warnings=[warning]).code == "JS_ORTAMI_YOK"
    # without the warning the same exception is just "not an audio/video"
    assert YouTubeAdapter().map_error(exc).code == "IG_VIDEO_DEGIL"


def test_ytdlp_warning_templates_still_exist_in_installed_source():
    """Guard: the texts the classification relies on must still be in yt-dlp (breaks if they change)."""
    video = (_YT_DIR / "_video.py").read_text(encoding="utf-8")
    ejs = (_YT_DIR / "jsc" / "_builtin" / "ejs.py").read_text(encoding="utf-8")
    director = (_YT_DIR / "jsc" / "_director.py").read_text(encoding="utf-8")
    common = (_YT_DIR.parent / "common.py").read_text(encoding="utf-8")
    assert "No supported JavaScript runtime could be found." in video
    assert "Signature solving failed: Some formats may be missing." in video
    assert "n challenge solving failed: Some formats may be missing." in video
    assert "Challenge solver {script_type.value} script version" in ejs and "is not supported (source:" in ejs
    assert "Hash mismatch on challenge solver" in ejs
    assert "It may be required to solve JS challenges." in director
    assert "YouTube is requiring a captcha challenge before playback" in video
    assert "The uploader has not made this video available in your country" in video
    assert "This video is DRM protected" in common
    assert "due to geo restriction" in common


@pytest.mark.parametrize(
    "warning",
    [SIG_SOLVING_WARNING, N_SOLVING_WARNING, EJS_VERSION_WARNING, EJS_HASH_WARNING, EJS_SKIPPED_WARNING],
)
@pytest.mark.parametrize(
    "failure",
    [
        DownloadError(f"ERROR: [youtube] {VID}: {FORMAT_UNAVAILABLE}"),
        DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden"),
    ],
    ids=["format-unavailable", "http-403"],
)
def test_js_warning_wins_over_generic_failure(warning, failure):
    err = YouTubeAdapter().map_error(failure, warnings=[warning])
    assert err.code == "JS_ORTAMI_YOK"
    assert err.upload_fallback is True


def test_unrelated_warnings_do_not_change_classification():
    noise = [
        "[youtube] Falling back to generic n function search",
        f"[youtube] {VID}: Some web client https formats have been skipped as they are missing a url.",
        '[youtube] Skipping unsupported client "foo"',
    ]
    a = YouTubeAdapter()
    assert a.map_error(ExtractorError(FORMAT_UNAVAILABLE, expected=True), warnings=noise).code == "IG_VIDEO_DEGIL"
    assert a.map_error(DownloadError("HTTP Error 403: Forbidden"), warnings=noise).code == "IG_ERISILEMEDI"
    assert a.map_error(RuntimeError("HTTP Error 503"), warnings=noise).code == "KAYNAK_GECICI"


def test_size_limit_wins_over_js_warning():
    err = YouTubeAdapter().map_error(
        RuntimeError("File is larger than max-filesize (1 bytes)"), warnings=[N_SOLVING_WARNING]
    )
    assert err.code == "DOSYA_BUYUK"


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: DownloadError(
            f"ERROR: [youtube] {VID}: {FORMAT_UNAVAILABLE}", ExtractorError(FORMAT_UNAVAILABLE, expected=True)
        ),
        lambda: DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden"),
    ],
    ids=["format-unavailable", "http-403"],
)
def test_fetch_video_js_warnings_then_generic_error(tmp_path, qjs, make_exc):
    class Warns(FakeYDL):
        def extract_info(self, url, download=False):
            for w in (_real_no_runtime_warning(), SIG_SOLVING_WARNING, N_SOLVING_WARNING):
                self.opts["logger"].warning(w)
            raise make_exc()

    with pytest.raises(PullError) as exc_info:
        fetch_video(
            CANON,
            out_dir=tmp_path / "out",
            js_runtime_bin=qjs,
            limiter=NoWaitLimiter(),
            ydl_factory=lambda opts: Warns(opts, {}),
        )
    err = exc_info.value
    assert err.code == "JS_ORTAMI_YOK"
    assert err.upload_fallback is True
    assert "n challenge solving failed" in err.detail or "Signature solving failed" in err.detail


# --- network precedence and newly recognised yt-dlp messages --------------------------------------------


def test_network_exception_beats_inaccessible_text():
    import socket

    a = YouTubeAdapter()
    assert a.map_error(OSError("Video unavailable: connection broke")).code == "KAYNAK_GECICI"
    assert a.map_error(socket.timeout("service unavailable")).code == "KAYNAK_GECICI"
    # plain text with a non-network exception type is still "inaccessible"
    assert a.map_error(RuntimeError("ERROR: [youtube] abc: Video unavailable")).code == "IG_ERISILEMEDI"


@pytest.mark.parametrize(
    "msg, code",
    [
        # _video.py:4082 (raised as GeoRestrictedError with this text)
        ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "IG_ERISILEMEDI"),
        # common.py:1256 default geo message
        ("ERROR: [youtube] abc: This video is not available from your location due to geo restriction", "IG_ERISILEMEDI"),
        # common.py:1227
        ("ERROR: [youtube] abc: This video is DRM protected", "IG_ERISILEMEDI"),
        # _video.py:4095
        (f"ERROR: {CAPTCHA_MESSAGE}", "IG_RATE_LIMIT"),
    ],
)
def test_real_ytdlp_inaccessible_and_captcha_messages(msg, code):
    assert YouTubeAdapter().map_error(ExtractorError(msg, expected=True)).code == code


# --- JS runtime argument edge cases ---------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", Path("")])
def test_blank_explicit_runtime_is_an_error_not_unset(blank, tmp_path, monkeypatch):
    qjs = tmp_path / "qjs.exe"
    qjs.write_bytes(b"")
    monkeypatch.setenv("INSTAPULLER_JS_RUNTIME_BIN", str(qjs))  # must NOT be used
    with pytest.raises(PullError) as exc_info:
        resolve_js_runtime(blank)
    assert exc_info.value.code == "JS_ORTAMI_YOK"


def test_empty_env_var_still_means_unset(monkeypatch):
    _no_runtime(monkeypatch)
    monkeypatch.setenv("INSTAPULLER_JS_RUNTIME_BIN", "  ")
    with pytest.raises(PullError) as exc_info:
        resolve_js_runtime()
    assert "No JavaScript runtime found" in exc_info.value.detail  # fell through to the search


def test_ffprobe_oserror_does_not_mask_path_runtime(tmp_path, monkeypatch):
    onpath = tmp_path / "qjs.exe"
    onpath.write_bytes(b"")

    def bad_ffprobe(_=None):
        raise OSError("broken symlink")

    monkeypatch.setattr(jsr, "resolve_ffprobe", bad_ffprobe)
    monkeypatch.setattr(jsr.shutil, "which", lambda name: str(onpath) if name == "qjs" else None)
    assert resolve_js_runtime().path == onpath
