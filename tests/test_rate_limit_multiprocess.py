"""Verify the cooldown with the real clock and real separate processes (takes ~30 s)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).with_name("_mp_worker.py")


def _spawn(state: Path, start_at: float, mode: str, site: str = "instagram") -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen(
        [sys.executable, str(WORKER), str(state), repr(start_at), mode, site],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=env,
    )


def _collect(proc: subprocess.Popen) -> dict:
    out, err = proc.communicate(timeout=90)
    assert proc.returncode == 0, err.decode("utf-8", "replace")
    return json.loads(out.decode("utf-8").strip().splitlines()[-1])


def test_three_processes_are_serialized_15s_apart_and_no_wait_is_side_effect_free(tmp_path):
    state = tmp_path / "rate_limit.json"
    start_at = time.time() + 4.0  # allowance for subprocess Python startup
    procs = [_spawn(state, start_at, "wait") for _ in range(3)]
    results = [_collect(p) for p in procs]

    for r in results:
        assert r["error"] is None, r
        assert r["code"] is None, r
        assert r["t"] is not None

    times = sorted(r["t"] for r in results)
    assert times[0] - start_at <= 1.0, "the first pass must not wait"
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 14.8 for g in gaps), gaps

    # wait=False: cooldown active -> IG_COOLDOWN, state must not change
    before = state.read_text(encoding="utf-8")
    probe = _collect(_spawn(state, time.time(), "nowait"))
    assert probe["code"] == "IG_COOLDOWN", probe
    assert probe["error"] is None
    assert 0 < probe["retry_after_s"] <= 15.0
    assert state.read_text(encoding="utf-8") == before
    assert json.loads(before)["last_request_at"] > 0
    # no leftover tmp files
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_instagram_queue_is_not_delayed_by_youtube(tmp_path):
    state = tmp_path / "rate_limit.json"
    start_at = time.time() + 4.0
    ig = [_spawn(state, start_at, "wait", "instagram") for _ in range(2)]
    yt = _spawn(state, start_at, "wait", "youtube")
    ig_times = sorted(_collect(p)["t"] for p in ig)
    yt_time = _collect(yt)["t"]

    assert yt_time - start_at <= 1.5, "youtube must not wait for instagram"
    assert ig_times[0] - start_at <= 1.5
    assert ig_times[1] - ig_times[0] >= 14.8

    data = json.loads(state.read_text(encoding="utf-8"))
    assert "last_request_at" in data and "last_request_at" in data["youtube"]
