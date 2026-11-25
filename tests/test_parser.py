"""Link ayrıştırma ve site izin listesi birim testleri."""

import pytest

from instapuller.models import PullError
from instapuller.sites import ALLOWED_SITES, allowed_hosts, parse_url


def test_allowlist_contains_only_instagram():
    assert list(ALLOWED_SITES.keys()) == ["instagram"]
    assert allowed_hosts() == frozenset({"instagram.com"})


@pytest.mark.parametrize(
    "raw_url, expected_id, expected_kind, expected_canonical",
    [
        (
            "https://www.instagram.com/reel/Chunk8-jurw/",
            "Chunk8-jurw",
            "reel",
            "https://www.instagram.com/reel/Chunk8-jurw/",
        ),
        (
            "https://instagram.com/reels/CDUMkliABpa/?igsh=abc",
            "CDUMkliABpa",
            "reel",
            "https://www.instagram.com/reel/CDUMkliABpa/",
        ),
        (
            "https://m.instagram.com/nasa/reel/Cop84x6u7CP/",
            "Cop84x6u7CP",
            "reel",
            "https://www.instagram.com/reel/Cop84x6u7CP/",
        ),
        (
            "instagram.com/p/ABC123xyz/",
            "ABC123xyz",
            "post",
            "https://www.instagram.com/p/ABC123xyz/",
        ),
        (
            "https://www.instagram.com/tv/TVcode9988/",
            "TVcode9988",
            "tv",
            "https://www.instagram.com/tv/TVcode9988/",
        ),
    ],
)
def test_parse_valid_instagram_links(raw_url, expected_id, expected_kind, expected_canonical):
    adapter, parsed = parse_url(raw_url)
    assert adapter.site_id == "instagram"
    assert parsed.site == "instagram"
    assert parsed.platform_id == expected_id
    assert parsed.kind == expected_kind
    assert parsed.canonical_url == expected_canonical


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.tiktok.com/@user/video/123456",
        "https://example.com/reel/Chunk8-jurw/",
        "ftp://instagram.com/reel/Chunk8-jurw/",
        "https://www.instagram.com/nasa/",
        "https://www.instagram.com/stories/nasa/12345/",
        "https://www.instagram.com/share/BA-Zo3dPT0",
        "https://www.instagram.com/reel/ab/",  # çok kısa shortcode
    ],
)
def test_reject_non_allowlisted_and_invalid_links(bad_url):
    with pytest.raises(PullError) as exc_info:
        parse_url(bad_url)
    assert exc_info.value.code == "LINK_GECERSIZ"
    assert exc_info.value.upload_fallback is True
