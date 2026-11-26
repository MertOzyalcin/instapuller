"""Per-site cooldown: separate records in one state file."""

import json

import pytest

from instapuller.models import PullError
from instapuller.rate_limit import CooldownLimiter
from tests.test_rate_limit import FakeClock


def _lim(state, clock, site):
    return CooldownLimiter(state, cooldown_s=15.0, site=site, clock=clock, sleeper=clock.sleep)


def test_ig_yt_ig_sequence(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    assert _lim(state, clock, "instagram").acquire() == 0.0
    clock.now += 4.0
    assert _lim(state, clock, "youtube").acquire() == 0.0
    assert clock.slept == []
    assert _lim(state, clock, "instagram").acquire() == 11.0
    assert clock.slept == [11.0]


def test_yt_yt_waits_and_no_wait_raises(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    yt = _lim(state, clock, "youtube")
    yt.acquire()
    clock.now += 5.0
    with pytest.raises(PullError) as ei:
        yt.acquire(wait=False)
    assert ei.value.code == "IG_COOLDOWN"
    assert ei.value.retry_after_s == 10.0
    assert yt.acquire() == 10.0


def test_youtube_only_file(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    rec = {"last_request_at": 1000.0, "written_at": 1000.0}
    state.write_text(json.dumps({"youtube": rec}), encoding="utf-8")
    clock.now = 1005.0
    assert _lim(state, clock, "instagram").acquire() == 0.0
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["youtube"] == rec
    assert data["last_request_at"] == 1005.0
    assert _lim(state, clock, "youtube").acquire() == 10.0


def test_old_format_is_instagram_and_preserved_by_youtube_write(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    state.write_text(
        json.dumps({"last_request_at": 995.0, "written_at": 995.0}), encoding="utf-8"
    )
    assert _lim(state, clock, "youtube").acquire() == 0.0
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["last_request_at"] == 995.0 and data["written_at"] == 995.0
    assert _lim(state, clock, "instagram").acquire() == 10.0


@pytest.mark.parametrize(
    "content",
    ["{not json", "[1, 2]", "42", '{"youtube": "x"}', '{"youtube": {"last_request_at": "a"}}'],
)
def test_corrupt_state_is_treated_as_empty(tmp_path, content):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    state.write_text(content, encoding="utf-8")
    assert _lim(state, clock, "youtube").acquire() == 0.0
    assert json.loads(state.read_text(encoding="utf-8"))["youtube"]["last_request_at"] == 1000.0


def test_corrupt_state_instagram(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "s.json"
    state.write_text("[1]", encoding="utf-8")
    assert _lim(state, clock, "instagram").acquire() == 0.0


@pytest.mark.parametrize("site", ["", "YouTube", "1abc", "a-b", "a" * 33, "x y"])
def test_invalid_site(tmp_path, site):
    with pytest.raises(ValueError):
        CooldownLimiter(tmp_path / "s.json", site=site)
