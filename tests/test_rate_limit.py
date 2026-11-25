"""Disk tabanlı cooldown (bekleme ve --no-wait) birim testleri."""

import pytest

from instapuller.models import PullError
from instapuller.rate_limit import CooldownLimiter


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_first_request_does_not_wait(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "rate_limit.json"
    limiter = CooldownLimiter(state, cooldown_s=15.0, clock=clock, sleeper=clock.sleep)

    waited = limiter.acquire(wait=True)
    assert waited == 0.0
    assert clock.slept == []
    assert state.is_file()


def test_second_request_waits_remaining_when_wait_true(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "rate_limit.json"
    limiter = CooldownLimiter(state, cooldown_s=15.0, clock=clock, sleeper=clock.sleep)

    limiter.acquire(wait=True)
    clock.now += 5.0  # 5 sn geçti, 10 sn kalmalı

    waited = limiter.acquire(wait=True)
    assert waited == 10.0
    assert clock.slept == [10.0]


def test_second_request_raises_ig_cooldown_when_no_wait(tmp_path):
    clock = FakeClock(1000.0)
    state = tmp_path / "rate_limit.json"
    limiter = CooldownLimiter(state, cooldown_s=15.0, clock=clock, sleeper=clock.sleep)

    limiter.acquire(wait=True)
    clock.now += 4.25  # 10.75 sn kalmalı

    with pytest.raises(PullError) as exc_info:
        limiter.acquire(wait=False)

    err = exc_info.value
    assert err.code == "IG_COOLDOWN"
    assert err.upload_fallback is True
    assert err.retry_after_s == 10.75
    assert clock.slept == []


def test_state_persists_across_separate_instances(tmp_path):
    clock = FakeClock(2000.0)
    state = tmp_path / "rate_limit.json"

    l1 = CooldownLimiter(state, cooldown_s=15.0, clock=clock, sleeper=clock.sleep)
    l1.acquire(wait=True)

    clock.now += 3.0
    l2 = CooldownLimiter(state, cooldown_s=15.0, clock=clock, sleeper=clock.sleep)
    with pytest.raises(PullError) as exc_info:
        l2.acquire(wait=False)

    assert exc_info.value.code == "IG_COOLDOWN"
    assert exc_info.value.retry_after_s == 12.0
