"""Disk-based, cross-process safe cooldown (protection against back-to-back requests)."""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import secrets
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from instapuller.models import PullError

DEFAULT_COOLDOWN_S = 15.0
MIN_COOLDOWN_S = 15.0
MAX_QUEUE_SLOTS = 20  # in a trusted queue a slot may be at most this many cooldowns ahead
LOCK_TIMEOUT_S = 10.0
_LOCK_POLL_S = 0.05
_REPLACE_ATTEMPTS = 20
_REPLACE_POLL_S = 0.05
DEFAULT_SITE = "instagram"
_SITE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def default_state_file() -> Path:
    """Default per-user state file path, independent of the working directory."""
    env = os.environ.get("INSTAPULLER_STATE_FILE")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "instapuller" / "rate_limit.json"
    return Path.home() / ".local" / "state" / "instapuller" / "rate_limit.json"


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


class CooldownLimiter:
    """Space requests at least `cooldown_s` apart, even across separate processes."""

    def __init__(
        self,
        state_file: Path,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        *,
        site: str = DEFAULT_SITE,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not isinstance(site, str) or not _SITE_RE.fullmatch(site):
            raise ValueError(f"Invalid site id: {site!r}")
        self.site = site
        self.state_file = Path(state_file)
        # Safety: cannot go below 15 s (silently raised).
        self.cooldown_s = max(MIN_COOLDOWN_S, float(cooldown_s))
        self._clock = clock
        self._sleeper = sleeper

    @property
    def lock_file(self) -> Path:
        return self.state_file.with_name(self.state_file.name + ".lock")

    @contextlib.contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT, 0o666)
        except OSError as exc:
            raise PullError(
                "BEKLENMEYEN_HATA",
                "The cooldown record could not be opened. Try again shortly.",
                detail=f"{type(exc).__name__}: {exc}"[:300],
            ) from None
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_S
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise PullError(
                        "BEKLENMEYEN_HATA",
                        "The cooldown record is in use by another process. Try again shortly.",
                        detail=f"Could not acquire the lock within {LOCK_TIMEOUT_S:g} s.",
                    )
                time.sleep(_LOCK_POLL_S)
            try:
                yield
            finally:
                _unlock(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _num(val: object) -> float | None:
        if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val):
            return float(val)
        return None

    def _read_root(self) -> dict:
        """Return the whole JSON state; unreadable or non-dict content counts as empty."""
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _read_state(self) -> tuple[float | None, float | None]:
        """Return (last_request_at, written_at) for this site; unreadable values are None.

        Instagram's record lives at the top level (compatible with older versions); every
        other site is a nested record keyed by its site id.
        """
        root = self._read_root()
        rec = root if self.site == DEFAULT_SITE else root.get(self.site)
        if not isinstance(rec, dict):
            return None, None
        return self._num(rec.get("last_request_at")), self._num(rec.get("written_at"))

    def _write_state(self, slot: float, written_at: float) -> None:
        record = {"last_request_at": slot, "written_at": written_at}
        root = self._read_root()  # preserve every other site's record
        if self.site == DEFAULT_SITE:
            root.update(record)
        else:
            root[self.site] = record
        tmp = self.state_file.with_name(
            f"{self.state_file.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            tmp.write_text(json.dumps(root), encoding="utf-8")
            for attempt in range(_REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, self.state_file)
                    return
                except PermissionError:
                    if attempt == _REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(_REPLACE_POLL_S)
        finally:
            tmp.unlink(missing_ok=True)

    def acquire(self, *, wait: bool = True) -> float:
        """Reserve a request slot.

        - Under the lock: slot = max(now, last + cooldown), capped at now + cooldown, or at
          now + MAX_QUEUE_SLOTS * cooldown when the stored state is trusted.
        - wait=False and slot > now: raise IG_COOLDOWN (with retry_after_s), writing nothing.
        - Otherwise save the slot, release the lock and sleep until the slot outside the lock.
        Return the time waited, in seconds.
        """
        with self._exclusive_lock():
            now = self._clock()
            last_ts, written_at = self._read_state()
            slot = now
            if last_ts is not None:
                slot = max(now, last_ts + self.cooldown_s)
                # Avoid long sleeps when the stored time is untrustworthy (no write time, or one
                # in the future = the clock was set back). In a trusted queue (parallel processes)
                # slots advance 15 s apart, so only a reasonable queue depth limits them.
                trusted = written_at is not None and written_at <= now + 1.0
                horizon = now + self.cooldown_s * (MAX_QUEUE_SLOTS if trusted else 1)
                slot = min(slot, horizon)

            delay = slot - now
            if not wait and delay > 0:
                raise PullError(
                    "IG_COOLDOWN",
                    f"Too many requests. Try again in {math.ceil(delay)} seconds "
                    "or upload the video as a file.",
                    retry_after_s=round(delay, 2),
                )
            self._write_state(slot, now)

        if delay > 0:
            self._sleeper(delay)
        return round(max(delay, 0.0), 2)
