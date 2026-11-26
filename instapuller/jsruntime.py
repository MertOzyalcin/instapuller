"""Resolve the JavaScript runtime yt-dlp needs to solve YouTube challenges (YouTube only)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from instapuller.models import PullError
from instapuller.probe import resolve_ffprobe
from instapuller.sanitize import sanitize_detail

ENV_VAR = "INSTAPULLER_JS_RUNTIME_BIN"
# Names searched next to ffprobe and on PATH, in this order.
_SEARCH_NAMES = ("qjs", "quickjs", "deno")


@dataclass(frozen=True)
class JsRuntime:
    name: str  # yt-dlp runtime key: "quickjs" or "deno"
    path: Path


def runtime_name_for(path: str | Path) -> str | None:
    """Infer the yt-dlp runtime key from the executable's file stem (qjs*/quickjs* or deno*)."""
    stem = Path(path).stem.lower()
    if stem.startswith(("qjs", "quickjs")):
        return "quickjs"
    if stem.startswith("deno"):
        return "deno"
    return None


def _missing(detail: str) -> PullError:
    return PullError(
        "JS_ORTAMI_YOK",
        "Setup is incomplete: YouTube downloads need a JavaScript runtime. "
        "Finish the setup or upload the video as a file instead.",
        detail=detail,
    )


def _build(candidate: Path) -> JsRuntime:
    name = runtime_name_for(candidate)
    if name is None:
        raise _missing(
            f"Unsupported JavaScript runtime file name {sanitize_detail(candidate.name, max_len=60)!r}; "
            "expected a name starting with qjs, quickjs or deno."
        )
    return JsRuntime(name=name, path=candidate)


def _resolve_given(value: str | Path, origin: str) -> JsRuntime:
    p = Path(value)
    if p.is_file():
        return _build(p)
    found = shutil.which(str(value)) if not p.is_absolute() and p.parent == Path(".") else None
    if found:
        return _build(Path(found))
    raise _missing(f"JavaScript runtime from {origin} was not found or is not a file.")


def resolve_js_runtime(
    js_runtime_bin: str | Path | None = None,
    *,
    ffprobe_bin: str | Path | None = None,
) -> JsRuntime:
    """Order: explicit argument > INSTAPULLER_JS_RUNTIME_BIN > next to ffprobe > PATH (qjs, then deno)."""
    if js_runtime_bin is not None:  # set but empty/blank is invalid (JS_ORTAMI_YOK), not "unset"
        return _resolve_given(js_runtime_bin, "the js_runtime_bin argument")
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return _resolve_given(env, ENV_VAR)

    try:
        probe_dir: Path | None = Path(resolve_ffprobe(ffprobe_bin)).resolve().parent
    except (PullError, OSError):
        probe_dir = None
    if probe_dir is not None:
        for name in _SEARCH_NAMES:
            for suffix in (".exe", ""):
                cand = probe_dir / f"{name}{suffix}"
                if cand.is_file():
                    return _build(cand)

    for name in _SEARCH_NAMES:
        found = shutil.which(name)
        if found:
            return _build(Path(found))

    raise _missing(
        "No JavaScript runtime found; pass --js-runtime-bin / js_runtime_bin, set "
        f"{ENV_VAR}, or put qjs or deno next to ffprobe or on PATH."
    )
