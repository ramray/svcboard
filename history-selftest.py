# -*- coding: utf-8 -*-
"""Self-test for the svcboard history/uptime engine.

Isolated: redirects storage to a temp file and wipes loaded state first,
so the real history.json is never touched. Synthetic probe results drive
baseline -> failed -> active and the uptime math is checked (~50%%).
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

SB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "svcboard.py")
spec = importlib.util.spec_from_file_location("svcboard_under_test", SB)
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

# isolate: redirect storage, wipe whatever was loaded from the real file
tmp = tempfile.mkdtemp(prefix="svcboard-hist-")
sb.HISTORY_PATH = os.path.join(tmp, "history.json")
sb.HISTORY["baseline"].clear()
sb.HISTORY["current"].clear()
del sb.HISTORY["events"][:]
sb.hist_by_key.clear()


def fake(st):
    return [{"label": "na-99", "ok": True, "units": {"demo": st}}]


sb.record_states(fake("active"))    # first sight -> baseline, no event

# a sub-second observation window must report ~100%, not be clamped by a
# 1s floor in the denominator (regression caught live on 2026-10-04)
t0 = sb.HISTORY["baseline"]["na-99/demo"]["ts"]
sub = sb.compute_uptime(t0 + 0.05)[0].get("na-99/demo")

time.sleep(1)
sb.record_states(fake("failed"))    # -> event 1
time.sleep(1)
sb.record_states(fake("active"))    # -> event 2

payload = sb.history_payload()
events = sb.HISTORY["events"]
up = payload["uptime"].get("na-99/demo")

checks = [
    ("2 events", len(events) == 2, len(events)),
    ("sub-second window ~100%",
     sub is not None and 99.9 <= sub <= 100.0, sub),
    ("event1 active->failed",
     bool(events) and events[0]["from"] == "active"
     and events[0]["to"] == "failed", events[0] if events else None),
    ("file saved", os.path.exists(sb.HISTORY_PATH), sb.HISTORY_PATH),
]
try:
    with open(sb.HISTORY_PATH, encoding="utf-8") as f:
        saved = json.load(f)
    checks.append(("file has 2 events",
                   len(saved.get("events", [])) == 2,
                   len(saved.get("events", []))))
except Exception as e:
    checks.append(("file readable", False, repr(e)))
checks.append(("uptime ~50%", up is not None and 35 <= up <= 65, up))
checks.append(("started set", payload["started"] is not None,
               payload["started"]))

ok = True
for name, passed, detail in checks:
    print("%-24s %s  (%r)" % (name, "PASS" if passed else "FAIL", detail))
    ok = ok and passed
sys.exit(0 if ok else 1)
