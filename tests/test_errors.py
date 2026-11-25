"""Hata eşleme ve detail temizleme (yerel yol gizleme, <= 500 karakter) testleri."""

from instapuller.sanitize import MAX_DETAIL_LEN, sanitize_detail
from instapuller.sites.instagram import InstagramAdapter


def test_empty_media_maps_to_ig_erisilemedi():
    adapter = InstagramAdapter()
    exc = RuntimeError(
        "ERROR: [Instagram] CnojlJ2LR2w: Instagram sent an empty media response. "
        "Check if this post is accessible in your browser without being logged-in."
    )
    err = adapter.map_error(exc)
    assert err.code == "IG_ERISILEMEDI"
    assert (
        err.message
        == "Bu gönderiye erişilemedi (gizli, silinmiş ya da giriş gerekiyor olabilir). "
        "Videoyu dosya olarak yükleyin."
    )
    assert err.upload_fallback is True


def test_rate_limit_maps_to_ig_rate_limit():
    adapter = InstagramAdapter()
    err = adapter.map_error(RuntimeError("HTTP Error 429: Too Many Requests / rate-limit reached"))
    assert err.code == "IG_RATE_LIMIT"
    assert err.upload_fallback is True


def test_unexpected_parse_error_maps_to_ig_arac_guncel_degil():
    adapter = InstagramAdapter()
    err = adapter.map_error(RuntimeError("ERROR: Unable to extract video url from JSON"))
    assert err.code == "IG_ARAC_GUNCEL_DEGIL"
    assert err.upload_fallback is True


def test_key_error_maps_to_ig_arac_guncel_degil():
    adapter = InstagramAdapter()
    err = adapter.map_error(KeyError("xdt_shortcode_media"))
    assert err.code == "IG_ARAC_GUNCEL_DEGIL"
    assert err.upload_fallback is True


def test_filesize_error_maps_to_dosya_buyuk():
    adapter = InstagramAdapter()
    err = adapter.map_error(RuntimeError("File is larger than max-filesize (209715200 bytes)"))
    assert err.code == "DOSYA_BUYUK"
    assert err.upload_fallback is True


def test_sanitize_detail_redacts_local_paths_and_truncates_to_500():
    raw = (
        r"Failed reading C:\Users\mert0\AppData\Local\Temp\secret\file.mp4 and "
        r"/Users/alice/private/session.json and \\server\share\hidden.txt — "
        + ("X" * 900)
    )
    cleaned = sanitize_detail(raw)
    assert cleaned is not None
    assert len(cleaned) <= MAX_DETAIL_LEN
    assert "mert0" not in cleaned
    assert "alice" not in cleaned
    assert "server" not in cleaned
    assert "[path]" in cleaned


def test_sanitize_detail_keeps_explicit_allowed_user_path():
    user_dir = r"C:\my_videos"
    raw = rf"Saved to {user_dir} but failed at C:\Users\secret\internal.py"
    cleaned = sanitize_detail(raw, allowed_paths=[user_dir])
    assert cleaned is not None
    assert user_dir in cleaned
    assert "secret" not in cleaned


def test_sanitize_detail_preserves_http_urls():
    raw = "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ for help"
    cleaned = sanitize_detail(raw)
    assert cleaned == raw
