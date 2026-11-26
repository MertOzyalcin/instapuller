"""Subprocess worker for test_rate_limit_multiprocess (not a test file)."""

import json
import sys
import time

from instapuller.models import PullError
from instapuller.rate_limit import CooldownLimiter

state, start_at, wait = sys.argv[1], float(sys.argv[2]), sys.argv[3] == "wait"
site = sys.argv[4] if len(sys.argv) > 4 else "instagram"
while time.time() < start_at:
    time.sleep(0.005)

out = {"t": None, "code": None, "error": None, "retry_after_s": None}
try:
    CooldownLimiter(state, cooldown_s=15.0, site=site).acquire(wait=wait)
    out["t"] = time.time()
except PullError as err:
    out["code"] = err.code
    out["retry_after_s"] = err.retry_after_s
except Exception as exc:  # noqa: BLE001
    out["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
