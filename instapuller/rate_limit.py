"""Disk tabanlı, süreçler arası güvenli cooldown (ardışık istek koruması)."""

from __future__ import annotations

import contextlib
import json
import math
import os
import secrets
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from instapuller.models import PullError

DEFAULT_COOLDOWN_S = 15.0
MIN_COOLDOWN_S = 15.0
MAX_QUEUE_SLOTS = 20  # güvenilir kuyrukta bir yuva en fazla bu kadar cooldown ilerde olabilir
LOCK_TIMEOUT_S = 10.0
_LOCK_POLL_S = 0.05
_REPLACE_ATTEMPTS = 20
_REPLACE_POLL_S = 0.05


def default_state_file() -> Path:
    """Çalışma klasöründen bağımsız, kullanıcıya özgü varsayılan state dosyası yolu."""
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
    """Farklı süreçler arasında bile istekleri en az `cooldown_s` arayla sıraya dizer."""

    def __init__(
        self,
        state_file: Path,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        *,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.state_file = Path(state_file)
        # Güvenlik: 15 sn altına inilemez (sessizce yükseltilir).
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
                "Bekleme kaydı açılamadı. Birazdan tekrar deneyin.",
                detail=f"{type(exc).__name__}: {exc}"[:300],
            ) from None
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_S
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise PullError(
                        "BEKLENMEYEN_HATA",
                        "Bekleme kaydı başka bir işlem tarafından kullanılıyor. Birazdan tekrar deneyin.",
                        detail=f"Kilit {LOCK_TIMEOUT_S:g} sn içinde alınamadı.",
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

    def _read_state(self) -> tuple[float | None, float | None]:
        """(last_request_at, written_at) döner; okunamayanlar None."""
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return self._num(data.get("last_request_at")), self._num(data.get("written_at"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None, None

    def _write_state(self, slot: float, written_at: float) -> None:
        tmp = self.state_file.with_name(
            f"{self.state_file.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            tmp.write_text(
                json.dumps({"last_request_at": slot, "written_at": written_at}), encoding="utf-8"
            )
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
        """Bir istek yuvası ayırır.

        - Kilit altında: slot = max(now, last + cooldown), en fazla now + cooldown.
        - wait=False ve slot > now: hiçbir şey yazmadan IG_COOLDOWN (retry_after_s ile).
        - Aksi halde slot kaydedilir, kilit bırakılır ve slot anına kadar kilit dışında uyunur.
        Beklenen süreyi (saniye) döner.
        """
        with self._exclusive_lock():
            now = self._clock()
            last_ts, written_at = self._read_state()
            slot = now
            if last_ts is not None:
                slot = max(now, last_ts + self.cooldown_s)
                # S2: state'in zamanı güvenilir değilse (yazma anı yok ya da şimdiden ilerde = saat
                # geri alınmış) uzun uyku olmasın. Güvenilir kuyrukta (paralel süreçler) yuvalar
                # 15 sn arayla ilerler, bu yüzden yalnızca makul bir kuyruk derinliğiyle sınırlanır.
                trusted = written_at is not None and written_at <= now + 1.0
                horizon = now + self.cooldown_s * (MAX_QUEUE_SLOTS if trusted else 1)
                slot = min(slot, horizon)

            delay = slot - now
            if not wait and delay > 0:
                raise PullError(
                    "IG_COOLDOWN",
                    f"Çok sık istek gönderildi. {math.ceil(delay)} saniye sonra tekrar deneyin "
                    "veya videoyu dosya olarak yükleyin.",
                    retry_after_s=round(delay, 2),
                )
            self._write_state(slot, now)

        if delay > 0:
            self._sleeper(delay)
        return round(max(delay, 0.0), 2)
