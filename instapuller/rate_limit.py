"""Disk tabanlı cooldown (ardışık istek koruması)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path

from instapuller.models import PullError

DEFAULT_COOLDOWN_S = 15.0


class CooldownLimiter:
    """Farklı CLI çağrıları arasında bile son istek zamanını dosya üzerinden korur."""

    def __init__(
        self,
        state_file: Path,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        *,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.state_file = Path(state_file)
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._clock = clock
        self._sleeper = sleeper

    def _read_last_ts(self) -> float | None:
        if not self.state_file.is_file():
            return None
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            val = data.get("last_request_at")
            if isinstance(val, (int, float)):
                return float(val)
        except (OSError, ValueError, TypeError):
            return None
        return None

    def _write_last_ts(self, ts: float) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_request_at": ts}), encoding="utf-8")
        tmp.replace(self.state_file)

    def acquire(self, *, wait: bool = True) -> float:
        """Cooldown süresini denetler.

        - wait=True: kalan süre kadar bekler, yeni zamanı kaydeder ve beklenen süreyi döner.
        - wait=False: süre dolmadıysa IG_COOLDOWN + retry_after_s fırlatır.
        """
        if self.cooldown_s <= 0:
            self._write_last_ts(self._clock())
            return 0.0

        now = self._clock()
        last_ts = self._read_last_ts()
        waited = 0.0

        if last_ts is not None:
            elapsed = now - last_ts
            remaining = self.cooldown_s - elapsed
            if remaining > 0:
                if not wait:
                    raise PullError(
                        "IG_COOLDOWN",
                        f"Ardışık istek koruması aktif. {math.ceil(remaining)} saniye sonra tekrar deneyin.",
                        upload_fallback=True,
                        retry_after_s=round(remaining, 2),
                    )
                self._sleeper(remaining)
                waited = remaining
                now = self._clock()

        self._write_last_ts(now)
        return round(waited, 2)
