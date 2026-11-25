"""Ağsız entegrasyon testleri: gerçek yt-dlp, yerel sunucular, gerçek ffprobe ve alt süreç CLI.

Tur 2 bulguları: Ö2, Ö3, Ö4, Ö7, S3, S4, S5, S6, S7 (Ö9: kalıcı gerçek testler).
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError, ExtractorError

import instapuller.downloader as dl
from instapuller.cleanup import cleanup_file
from instapuller.downloader import fetch_video
from instapuller.models import SizeLimitExceeded
from instapuller.models import PullError
from instapuller.sites import parse_url
from instapuller.sites.instagram import InstagramAdapter
from tests._helpers import (
    NoWaitLimiter,
    find_ffprobe,
    instagram_info,
    make_video,
    real_ydl_factory,
    serve_chunked,
    serve_directory,
)

ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.instagram.com/reel/Chunk8-jurw/"
MB = 1024 * 1024


def _fake_probe(path, ffprobe_bin=None):
    return 10.0


def _fetch_real(out_dir: Path, info: dict, **kw):
    kw.setdefault("duration_prober", _fake_probe)
    return fetch_video(
        URL,
        out_dir=out_dir,
        limiter=NoWaitLimiter(),
        ydl_factory=real_ydl_factory(info),
        **kw,
    )


def _err(fn) -> PullError:
    with pytest.raises(PullError) as exc_info:
        fn()
    return exc_info.value


# --- Ö2 / Ö3: boyut sınırı indirme sırasında --------------------------------


def test_content_length_over_limit_is_dosya_buyuk_not_kaynak_gecici(tmp_path):
    """Ö3: yt-dlp Content-Length bilince istisnasız atlar; logger bayrağı DOSYA_BUYUK vermeli."""
    media = tmp_path / "srv"
    media.mkdir()
    (media / "v.mp4").write_bytes(os.urandom(3 * MB))
    server, base = serve_directory(media)
    try:
        out = tmp_path / "out"
        err = _err(lambda: _fetch_real(out, instagram_info(f"{base}/v.mp4"), max_filesize_mb=1))
    finally:
        server.shutdown()
    assert err.code == "DOSYA_BUYUK"
    assert list(out.iterdir()) == []


def test_chunked_stream_without_content_length_is_aborted_midway(tmp_path):
    """Ö2: Content-Length yok; sınır aşılınca indirme kesilmeli, tamamı inmemeli."""
    total = 40 * MB
    server, url, counter = serve_chunked(total)
    try:
        out = tmp_path / "out"
        err = _err(lambda: _fetch_real(out, instagram_info(url), max_filesize_mb=1))
        time.sleep(0.3)
    finally:
        server.shutdown()
    assert err.code == "DOSYA_BUYUK"
    assert counter.sent < total, "tamamı indirildi"
    assert counter.sent <= 1 * MB + 12 * MB, counter.sent  # soket tamponları için makul pay
    assert list(out.iterdir()) == []


# --- S5 / S6 / S7 ------------------------------------------------------------


def test_foreign_extractor_key_is_ig_arac_guncel_degil(tmp_path):
    info = instagram_info("http://127.0.0.1:9/x.mp4", extractor_key="Generic")
    err = _err(lambda: _fetch_real(tmp_path / "out", info))
    assert err.code == "IG_ARAC_GUNCEL_DEGIL"


def test_only_instagram_extractor_is_loaded_in_real_ytdlp():
    with yt_dlp.YoutubeDL({"allowed_extractors": ["Instagram"], "quiet": True}) as ydl:
        assert set(ydl._ies) == {"Instagram"}


def test_percent_in_out_dir_name_does_not_break_template_and_file_lands_in_staging(tmp_path):
    media = tmp_path / "srv"
    media.mkdir()
    (media / "v.mp4").write_bytes(os.urandom(4096))
    server, base = serve_directory(media)
    try:
        out = tmp_path / "%(title)s %(id)s çıktı"
        res = _fetch_real(out, instagram_info(f"{base}/v.mp4"))
    finally:
        server.shutdown()
    assert res.video_path.parent == out
    assert res.video_path.is_file() and res.video_path.stat().st_size == 4096
    assert [p.name for p in out.iterdir()] == [res.video_path.name]
    assert sorted(c.name for c in tmp_path.iterdir()) == sorted(["srv", out.name])
    assert not (ROOT / "%(title)s").exists()
    cleanup_file(res.video_path)


def test_opts_carry_paths_allowed_extractors_and_hook(tmp_path):
    seen: dict = {}

    class Spy:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return instagram_info("http://x/y.mp4")

        def process_ie_result(self, info, download=True):
            Path(seen["paths"]["home"], "Chunk8-jurw.mp4").write_bytes(b"1")
            return info

    fetch_video(
        URL,
        out_dir=tmp_path / "o",
        limiter=NoWaitLimiter(),
        ydl_factory=Spy,
        duration_prober=_fake_probe,
    )
    assert seen["allowed_extractors"] == ["Instagram"]
    assert seen["outtmpl"] == "Chunk8-jurw.%(ext)s"
    assert seen["paths"]["home"] == seen["paths"]["temp"]
    assert callable(seen["progress_hooks"][0])
    assert "/" not in seen["outtmpl"] and "\\" not in seen["outtmpl"]


def test_find_downloaded_file_prefers_reported_path_and_ignores_partials(tmp_path):
    staging = tmp_path / "ip_x"
    staging.mkdir()
    good = staging / "a.mp4"
    other = staging / "b.mp4"
    for p in (good, other, staging / "a.mp4.part", staging / "a.mp4.ytdl", staging / "a.mp4.temp",
              staging / "a.f1.mp4.part-Frag3"):
        p.write_bytes(b"x")
    assert dl._find_downloaded_file({"requested_downloads": [{"filepath": str(other)}]}, {}, staging) == other
    assert dl._find_downloaded_file({"filepath": str(good)}, {}, staging) == good
    assert dl._find_downloaded_file(None, {}, staging) == good  # sıralı ilk gerçek dosya

    only_partials = tmp_path / "ip_y"
    only_partials.mkdir()
    for n in ("a.mp4.part", "a.mp4.ytdl", "a.mp4.temp", "a.f1.mp4.part-Frag3"):
        (only_partials / n).write_bytes(b"x")
    assert dl._find_downloaded_file(None, {}, only_partials) is None


def test_reported_filepath_outside_staging_is_ignored(tmp_path):
    staging = tmp_path / "ip_x"
    staging.mkdir()
    outside = tmp_path / "evil.mp4"
    outside.write_bytes(b"x")
    assert dl._find_downloaded_file({"filepath": str(outside)}, {}, staging) is None


# --- süre: gerçek ffprobe -----------------------------------------------------


def test_video_uzun_with_real_ffprobe_and_file_removed(tmp_path):
    ffprobe = find_ffprobe()
    media = tmp_path / "srv"
    media.mkdir()
    make_video(media / "v.mp4", 200)
    server, base = serve_directory(media)
    try:
        out = tmp_path / "out"
        err = _err(
            lambda: fetch_video(
                URL,
                out_dir=out,
                limiter=NoWaitLimiter(),
                ydl_factory=real_ydl_factory(instagram_info(f"{base}/v.mp4")),
                ffprobe_bin=ffprobe,
            )
        )
    finally:
        server.shutdown()
    assert err.code == "VIDEO_UZUN"
    assert "185 saniyeyi" in err.message
    assert list(out.iterdir()) == []


def test_real_video_success_with_real_ffprobe(tmp_path):
    ffprobe = find_ffprobe()
    media = tmp_path / "srv"
    media.mkdir()
    make_video(media / "v.mp4", 3)
    server, base = serve_directory(media)
    try:
        res = fetch_video(
            URL,
            out_dir=tmp_path / "out",
            limiter=NoWaitLimiter(),
            ydl_factory=real_ydl_factory(instagram_info(f"{base}/v.mp4")),
            ffprobe_bin=ffprobe,
        )
    finally:
        server.shutdown()
    assert 2.5 <= res.meta.duration_s <= 3.6
    assert res.video_path.is_file()


# --- CLI başarı yolu (alt süreç, Türkçe yol) ---------------------------------


def test_cli_success_single_json_line_utf8_with_turkish_out_dir(tmp_path):
    out_dir = tmp_path / "çıktı ĞÜŞİÖÇ ığüşöç"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "_cli_fake_run.py"), "fetch", URL, "--out-dir", str(out_dir)],
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    text = proc.stdout.decode("utf-8")
    lines = text.splitlines()
    assert len(lines) == 1, text
    payload = json.loads(lines[0])
    assert payload["ok"] is True and payload["keep_video"] is False
    assert Path(payload["video_path"]).is_file()
    assert Path(payload["video_path"]).parent == out_dir
    meta = payload["meta"]
    assert meta["title"] == "Türkçe başlık ığüşöç ĞÜŞİÖÇ"
    assert meta["author"] == "instagram"
    assert meta["platform_id"] == "Chunk8-jurw"
    assert meta["duration_s"] == 12.5
    assert meta["published_at"] == "2022-08-26T15:53:51Z"
    assert meta["stats"] == {"views": None, "likes": 7, "comments": 3}
    assert proc.stderr == b""


# --- iptal / temizlik --------------------------------------------------------


def test_keyboard_interrupt_during_download_leaves_no_staging(tmp_path):
    class Boom:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return instagram_info("http://x/y.mp4")

        def process_ie_result(self, info, download=True):
            raise KeyboardInterrupt

    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        fetch_video(URL, out_dir=out, limiter=NoWaitLimiter(), ydl_factory=Boom, duration_prober=_fake_probe)
    assert list(out.iterdir()) == []


def test_stale_staging_dirs_are_purged_but_fresh_and_unprefixed_stay(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    old = out / "ip_x_old"
    fresh = out / "ip_x_fresh"
    other = out / "baska_klasor"
    for d in (old, fresh, other):
        d.mkdir()
        (d / "f.part").write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    os.utime(other, (two_hours_ago, two_hours_ago))
    plain_old_file = out / "ip_dosya.txt"
    plain_old_file.write_bytes(b"x")
    os.utime(plain_old_file, (two_hours_ago, two_hours_ago))

    media = tmp_path / "srv"
    media.mkdir()
    (media / "v.mp4").write_bytes(os.urandom(1024))
    server, base = serve_directory(media)
    try:
        res = _fetch_real(out, instagram_info(f"{base}/v.mp4"))
    finally:
        server.shutdown()
    assert not old.exists()
    assert fresh.exists() and other.exists() and plain_old_file.exists()
    assert res.video_path.exists()
    assert dl.STALE_STAGING_AGE_S == 3600.0


# --- S3: cleanup yalnızca üretilen adları siler ------------------------------


def test_cleanup_refuses_arbitrary_files_but_deletes_generated_names(tmp_path):
    random_file = tmp_path / "notlarim.txt"
    random_file.write_text("önemli", encoding="utf-8")
    with pytest.raises(ValueError):
        cleanup_file(random_file)
    assert random_file.exists()

    for bad in ("video.mp4", "Chunk8-jurw_zzzzzzzz.mp4", "Chunk8-jurw_a1b2c3d4.txt", "abc_a1b2c3d4.mp4"):
        p = tmp_path / bad
        p.write_bytes(b"x")
        with pytest.raises(ValueError):
            cleanup_file(p)
        assert p.exists(), bad

    good = tmp_path / "Chunk8-jurw_a1b2c3d4.mp4"
    good.write_bytes(b"x")
    assert cleanup_file(good) is True
    assert not good.exists()


def test_cli_cleanup_of_random_file_is_silme_hatasi(tmp_path):
    f = tmp_path / "onemli.docx"
    f.write_bytes(b"x")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-m", "instapuller", "cleanup", str(f)], capture_output=True, cwd=str(ROOT), env=env
    )
    payload = json.loads(proc.stdout.decode("utf-8"))
    assert proc.returncode == 1
    assert payload["error"]["code"] == "SILME_HATASI" and payload["error"]["upload_fallback"] is False
    assert f.exists()


# --- S4: site listesi -------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "https://instagram.com.kotu.com/reel/Chunk8-jurw/",
        "https://kotu.com/instagram.com/reel/Chunk8-jurw/",
        "https://instagram.com@kotu.com/reel/Chunk8-jurw/",
        "https://kullanici@instagram.com/reel/Chunk8-jurw/",
        "https://a:b@kotu.com@instagram.com/reel/Chunk8-jurw/",
        "https://instagram.com:8080/reel/Chunk8-jurw/",
        "https://instagram.com:99999/reel/Chunk8-jurw/",
        "https://www.instagram.com/reel/--------/",
        "https://www.instagram.com/reel/_____/",
    ],
)
def test_rejected_links(bad):
    err = _err(lambda: parse_url(bad))
    assert err.code == "LINK_GECERSIZ"
    assert err.upload_fallback is True


@pytest.mark.parametrize(
    "good",
    [
        "https://www.instagram.com:443/reel/Chunk8-jurw/",
        "http://instagram.com:80/reel/Chunk8-jurw/",
        "https://WWW.INSTAGRAM.COM/reel/Chunk8-jurw/",
        "https://www.instagram.com/reel/-abcd_e/",
    ],
)
def test_accepted_links(good):
    _, parsed = parse_url(good)
    assert parsed.canonical_url.startswith("https://www.instagram.com/reel/")


def test_adapter_parse_url_also_checks_authority():
    err = _err(lambda: InstagramAdapter().parse_url("https://u@instagram.com/reel/Chunk8-jurw/"))
    assert err.code == "LINK_GECERSIZ"


# --- Ö4: map_error gerçek yt-dlp mesajları ve istisna türleri ---------------


def _http_error(status: int, reason: str = "x") -> HTTPError:
    return HTTPError(Response(io.BytesIO(b""), "https://www.instagram.com/", {}, status=status, reason=reason))


def _download_error(inner: BaseException, msg: str | None = None) -> DownloadError:
    text = msg or f"ERROR: [Instagram] Chunk8-jurw: {inner}"
    return DownloadError(text, exc_info=(type(inner), inner, None))


_LOGIN_HINT = (
    "Use --cookies-from-browser or --cookies for the authentication. See  "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies"
)
_DNS_MSG = (
    "Unable to download JSON metadata: <urlopen error [Errno 11001] getaddrinfo failed> "
    "(caused by <urlopen error [Errno 11001] getaddrinfo failed>)"
)


def _cases():
    dns_cause = TransportError(cause="getaddrinfo failed")
    return [
        # (ad, istisna, beklenen kod)
        ("dns_text_only", RuntimeError(f"ERROR: [Instagram] Chunk8-jurw: {_DNS_MSG}"), "KAYNAK_GECICI"),
        (
            "dns_typed",
            _download_error(ExtractorError(_DNS_MSG, cause=dns_cause)),
            "KAYNAK_GECICI",
        ),
        ("urlerror", _download_error(urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))), "KAYNAK_GECICI"),
        ("timeout", _download_error(TimeoutError("timed out")), "KAYNAK_GECICI"),
        ("transport", _download_error(TransportError("Connection reset")), "KAYNAK_GECICI"),
        (
            "http500_typed",
            _download_error(ExtractorError("Unable to download JSON metadata: HTTP Error 500: Internal Server Error", cause=_http_error(500))),
            "KAYNAK_GECICI",
        ),
        (
            "http500_text",
            RuntimeError("ERROR: [Instagram] Chunk8-jurw: Unable to download JSON metadata: HTTP Error 500: Internal Server Error"),
            "KAYNAK_GECICI",
        ),
        ("http503_typed", _download_error(_http_error(503)), "KAYNAK_GECICI"),
        ("http429_typed", _download_error(_http_error(429, "Too Many Requests")), "IG_RATE_LIMIT"),
        ("http429_text", RuntimeError("Unable to download webpage: HTTP Error 429: Too Many Requests"), "IG_RATE_LIMIT"),
        ("http403_typed", _download_error(_http_error(403)), "IG_ERISILEMEDI"),
        ("http404_text", RuntimeError("Unable to download webpage: HTTP Error 404: Not Found"), "IG_ERISILEMEDI"),
        ("http401_typed", _download_error(_http_error(401)), "IG_ERISILEMEDI"),
        # kısa kodda 429/403 geçen gönderilerin ilgisiz hataları sayıya göre eşlenmemeli
        ("code_with_429", RuntimeError("ERROR: [Instagram] Cx429zAB: Unable to extract video url"), "IG_ARAC_GUNCEL_DEGIL"),
        ("code_with_403", RuntimeError("ERROR: [Instagram] Cx403zAB: Unable to extract video url"), "IG_ARAC_GUNCEL_DEGIL"),
        ("code_with_404", RuntimeError("ERROR: [Instagram] Cx404zAB: Something odd happened"), "KAYNAK_GECICI"),
        (
            "format_not_available",
            RuntimeError("ERROR: [Instagram] Chunk8-jurw: Requested format is not available. Use --list-formats for a list of available formats"),
            "IG_VIDEO_DEGIL",
        ),
        (
            "login_redirect_rate_limit",
            ExtractorError(
                "The webpage request was redirected to the login page. "
                f"You have exceeded the rate-limit for accessing posts anonymously. {_LOGIN_HINT}",
                expected=True,
            ),
            "IG_RATE_LIMIT",
        ),
        (
            "registered_users",
            ExtractorError(
                f"This content is only available for registered users who follow this account. {_LOGIN_HINT}",
                expected=True,
            ),
            "IG_ERISILEMEDI",
        ),
        (
            "empty_media",
            ExtractorError(
                "Instagram sent an empty media response. Check if this post is accessible in your browser "
                "without being logged-in. If it is not, then use --cookies-from-browser or --cookies for the "
                "authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
                "for how to manually pass cookies. Otherwise, if the post is accessible in browser without "
                "being logged-in, please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= ",
                expected=True,
            ),
            "IG_ERISILEMEDI",
        ),
        ("no_video", ExtractorError("There is no video in this post", expected=True), "IG_VIDEO_DEGIL"),
        ("unable_to_extract", ExtractorError("Unable to extract video url"), "IG_ARAC_GUNCEL_DEGIL"),
        ("key_error", KeyError("xdt_shortcode_media"), "IG_ARAC_GUNCEL_DEGIL"),
        ("size_internal", _download_error(SizeLimitExceeded("downloaded_bytes=9 > 8")), "DOSYA_BUYUK"),
        ("size_context", _with_context(), "DOSYA_BUYUK"),
        ("size_text", RuntimeError("[download] File is larger than max-filesize (9 bytes > 8 bytes). Aborting."), "DOSYA_BUYUK"),
    ]


def _with_context() -> BaseException:
    try:
        try:
            raise SizeLimitExceeded("x")
        except SizeLimitExceeded:
            raise RuntimeError("wrapped without cause")  # noqa: B904
    except RuntimeError as exc:
        return exc


@pytest.mark.parametrize("name, exc, code", _cases(), ids=[c[0] for c in _cases()])
def test_map_error_real_messages_and_exception_types(name, exc, code):
    err = InstagramAdapter().map_error(exc)
    assert err.code == code, (name, err.code, err.message, err.detail)
    assert err.upload_fallback is True


# --- yt-dlp uyarılarının tanı için saklanması --------------------------------


def _run_fake_cli(tmp_path, extra_env):
    env = {**os.environ, "PYTHONPATH": str(ROOT), **extra_env}
    env.pop("INSTAPULLER_QUIET", None) if "INSTAPULLER_QUIET" not in extra_env else None
    return subprocess.run(
        [sys.executable, str(ROOT / "tests" / "_cli_fake_run.py"), "fetch", URL, "--out-dir", str(tmp_path / "o")],
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )


_WARN = r"Unable to extract thing; C:\Users\mert\secret\dir\x.json is odd"


def test_cli_warning_goes_to_stderr_only_and_stdout_stays_single_json(tmp_path):
    proc = _run_fake_cli(tmp_path, {"INSTAPULLER_FAKE_WARN": _WARN})
    assert proc.returncode == 0
    lines = proc.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["ok"] is True
    err = proc.stderr.decode("utf-8", "replace")
    assert "instapuller: yt-dlp uyarısı: Unable to extract thing" in err
    assert "mert" not in err and "[path]" in err


def test_cli_quiet_env_silences_stderr(tmp_path):
    proc = _run_fake_cli(tmp_path, {"INSTAPULLER_FAKE_WARN": _WARN, "INSTAPULLER_QUIET": "1"})
    assert proc.returncode == 0
    assert proc.stderr == b""
    assert len(proc.stdout.decode("utf-8").splitlines()) == 1


def test_cli_failure_detail_has_warnings_even_when_quiet(tmp_path):
    proc = _run_fake_cli(
        tmp_path,
        {"INSTAPULLER_FAKE_WARN": _WARN, "INSTAPULLER_FAKE_FAIL": "1", "INSTAPULLER_QUIET": "1"},
    )
    assert proc.returncode == 1
    assert proc.stderr == b""
    lines = proc.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1
    detail = json.loads(lines[0])["error"]["detail"]
    assert "yt-dlp uyarıları:" in detail and "Unable to extract thing" in detail
    assert len(detail) <= 500 and "mert" not in detail and "[path]" in detail


class _WarnFailYDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        for i in range(30):
            self.opts["logger"].warning(f"uyari {i} " + "x" * 120)
        self.opts["logger"].error(r"ERROR: D:\gizli\klasor\a.mp4 okunamadi")
        raise RuntimeError("HTTP Error 500: Internal Server Error " + "y" * 300)


def test_fetch_video_failure_detail_capped_and_original_first(tmp_path, capsys):
    with pytest.raises(PullError) as ei:
        fetch_video(
            URL, out_dir=tmp_path / "o", limiter=NoWaitLimiter(), ydl_factory=_WarnFailYDL
        )
    detail = ei.value.detail
    assert len(detail) <= 500
    assert detail.startswith("HTTP Error 500")
    assert "yt-dlp uyarıları: uyari 0" in detail
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err.count("instapuller: yt-dlp uyarısı:") == 31  # stderr her satırı yazar; detail için yalnızca ilk 10 saklanır


def test_logger_keeps_at_most_ten_and_shortens_and_ignores_debug(capsys, monkeypatch):
    monkeypatch.setenv("INSTAPULLER_QUIET", "1")
    lg = dl._SilentLogger()
    lg.debug("d")
    lg.info("i")
    for i in range(25):
        lg.warning("w" * 1000)
    assert len(lg.warnings) == 10 and all(len(w) <= 200 for w in lg.warnings)
    assert capsys.readouterr().err == ""


def test_no_warnings_leaves_detail_untouched(tmp_path):
    class Y(_WarnFailYDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("HTTP Error 500: x")

    with pytest.raises(PullError) as ei:
        fetch_video(URL, out_dir=tmp_path / "o", limiter=NoWaitLimiter(), ydl_factory=Y)
    assert "uyarıları" not in (ei.value.detail or "")


def test_real_ytdlp_report_warning_reaches_logger(tmp_path, capsys):
    base = real_ydl_factory(instagram_info("http://127.0.0.1:9/none.mp4"))

    def factory(opts):
        ydl = base(opts)
        ydl.report_warning("gercek uyari 123")
        raise_after = ydl
        return raise_after

    with pytest.raises(PullError) as ei:
        fetch_video(URL, out_dir=tmp_path / "o", limiter=NoWaitLimiter(), ydl_factory=factory)
    # report_warning logger üzerinden gitti; hata sonradan (bağlantı) geldiyse detail'de olmalı
    assert "gercek uyari 123" in capsys.readouterr().err
    assert "gercek uyari 123" in (ei.value.detail or "")
