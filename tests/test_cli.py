"""CLI: a single JSON line in every case, exit codes and the error code table."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import instapuller.__main__ as cli
from instapuller.models import ERROR_CODES, PullError

ROOT = Path(__file__).resolve().parent.parent
GOOD_URL = "https://www.instagram.com/reel/Chunk8-jurw/"


def _run(*args: str) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-m", "instapuller", *args],
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        timeout=60,
    )
    text = proc.stdout.decode("utf-8")
    lines = text.splitlines()
    assert len(lines) == 1, text
    return proc.returncode, json.loads(lines[0])


def _assert_error(payload: dict, code: str) -> None:
    assert payload["ok"] is False
    assert payload["error"]["code"] == code
    assert payload["error"]["upload_fallback"] is ERROR_CODES[code]


def test_invalid_cooldown_value_is_json_arg_error():
    rc, payload = _run("fetch", GOOD_URL, "--cooldown", "abc")
    assert rc == 1
    _assert_error(payload, "ARG_GECERSIZ")
    assert payload["error"]["upload_fallback"] is False


def test_cooldown_below_15_is_rejected():
    rc, payload = _run("fetch", GOOD_URL, "--cooldown", "5")
    assert rc == 1
    _assert_error(payload, "ARG_GECERSIZ")


def test_missing_subcommand_is_json_arg_error():
    rc, payload = _run()
    assert rc == 1
    _assert_error(payload, "ARG_GECERSIZ")


def test_unknown_option_is_json_arg_error():
    rc, payload = _run("fetch", GOOD_URL, "--no-such-option")
    assert rc == 1
    _assert_error(payload, "ARG_GECERSIZ")


def test_double_dash_protects_dash_prefixed_link():
    rc, payload = _run("fetch", "--", "-h")
    assert rc == 1
    _assert_error(payload, "LINK_GECERSIZ")


def test_disallowed_site_is_link_gecersiz():
    rc, payload = _run("fetch", "https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert rc == 1
    _assert_error(payload, "LINK_GECERSIZ")


def test_help_exits_zero_with_text_not_json():
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-m", "instapuller", "-h"], capture_output=True, cwd=str(ROOT), env=env
    )
    assert proc.returncode == 0
    assert b"usage" in proc.stdout.lower()


def test_cleanup_failure_is_silme_hatasi_not_fallback(tmp_path):
    rc, payload = _run("cleanup", str(tmp_path))  # directory -> error
    assert rc == 1
    _assert_error(payload, "SILME_HATASI")
    assert payload["error"]["upload_fallback"] is False


def test_keyboard_interrupt_gives_json_and_130(monkeypatch, capsys):
    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "fetch_video", boom)
    rc = cli.main(["fetch", GOOD_URL])
    assert rc == 130
    payload = json.loads(capsys.readouterr().out)
    _assert_error(payload, "BEKLENMEYEN_HATA")
    assert payload["error"]["message"] == "The operation was cancelled."


def test_unexpected_exception_is_beklenmeyen_hata_without_fallback(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError(r"broken C:\Users\secretuser\x.txt")

    monkeypatch.setattr(cli, "fetch_video", boom)
    rc = cli.main(["fetch", GOOD_URL])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    _assert_error(payload, "BEKLENMEYEN_HATA")
    assert payload["error"]["upload_fallback"] is False
    assert payload["error"]["detail"].startswith("RuntimeError: broken")
    assert "secretuser" not in payload["error"]["detail"]


def test_cooldown_below_min_in_process(capsys):
    assert cli.main(["fetch", GOOD_URL, "--cooldown", "14.9"]) == 1
    _assert_error(json.loads(capsys.readouterr().out), "ARG_GECERSIZ")


# --- error code table ---------------------------------------------------------

_TRUE_CODES = [
    "LINK_GECERSIZ",
    "IG_ERISILEMEDI",
    "IG_RATE_LIMIT",
    "IG_ARAC_GUNCEL_DEGIL",
    "IG_VIDEO_DEGIL",
    "IG_COOLDOWN",
    "DOSYA_BUYUK",
    "VIDEO_UZUN",
    "DOSYA_BOZUK",
    "KAYNAK_GECICI",
]
_FALSE_CODES = ["FFPROBE_YOK", "ARG_GECERSIZ", "SILME_HATASI", "BEKLENMEYEN_HATA"]


def test_error_code_table_is_exactly_the_agreed_fourteen():
    expected = {**dict.fromkeys(_TRUE_CODES, True), **dict.fromkeys(_FALSE_CODES, False)}
    assert ERROR_CODES == expected
    assert len(ERROR_CODES) == 14


def test_every_pullerror_code_in_source_is_in_table_and_old_code_is_gone():
    used: set[str] = set()
    for py in (ROOT / "instapuller").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "IG_INDIRME_HATASI" not in src, py
        used.update(re.findall(r'PullError\(\s*"([A-Z0-9_]+)"', src))
    assert used, "the scan found no codes"
    assert used <= set(ERROR_CODES), used - set(ERROR_CODES)
    assert "IG_INDIRME_HATASI" not in (ROOT / "README.md").read_text(encoding="utf-8")


def test_unknown_code_raises_value_error_and_fallback_param_is_gone():
    with pytest.raises(ValueError):
        PullError("TYPO_CODE", "x")
    with pytest.raises(TypeError):
        PullError("LINK_GECERSIZ", "x", upload_fallback=False)  # type: ignore[call-arg]
    assert PullError("FFPROBE_YOK", "x").upload_fallback is False
    assert PullError("KAYNAK_GECICI", "x").upload_fallback is True
