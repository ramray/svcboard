# -*- coding: utf-8 -*-
"""One-off: append the last pytest run from runbook-log.txt into
tests-history.json (the "Тесты" tab history starts empty when the feature
lands - this imports the runs that already happened).

Usage:
  python tests-history-backfill.py <runbook-log.txt> <tests-history.json> ["YYYY-MM-DD HH:MM:SS"]
Without the 3rd argument the log file mtime is used as the run time.
Idempotent: skips when a record for that moment already exists.
"""
import json
import os
import re
import sys
import time


def main():
    log_p, hist_p = sys.argv[1], sys.argv[2]
    with open(log_p, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    summary = re.findall(r"(?m)^__SUMMARY_\w+=.*$", text)[:4]
    if not summary:
        print("NO_SUMMARY: nothing to backfill")
        return 0
    tail = ""
    for m in re.finditer(r"(?m)^.*\b\d+ (?:failed|passed).+ in \d+\.\d+s.*$",
                         text):
        tail = m.group(0).strip()
    pytest_rc = None
    m = re.search(r"(?m)^__SUMMARY_PYTEST_RC=(\S+)", text)
    if m:
        v = m.group(1)
        pytest_rc = int(v) if v.isdigit() else v
    # only from pytest's short summary (tests may print these words themselves)
    pos = text.rfind("short test summary info")
    seg = text[pos:] if pos >= 0 else ""
    failed = []
    for m in re.finditer(r"(?m)^(?:FAILED|ERROR) (.+)$", seg):
        tid, sep, err = m.group(1).strip().partition(" - ")
        failed.append({"test": tid.strip(),
                       "err": (err.strip()[:400] if sep else "")})
    if len(sys.argv) > 3 and sys.argv[3].strip():
        epoch = time.mktime(time.strptime(sys.argv[3].strip(),
                                          "%Y-%m-%d %H:%M:%S"))
    else:
        epoch = os.path.getmtime(log_p)
    rec = {"epoch": epoch,
           "started": time.strftime("%H:%M:%S", time.localtime(epoch)),
           "ended": time.strftime("%H:%M:%S", time.localtime(epoch)),
           "rc": 0,                      # summary exists = runner reached the end
           "pytest_rc": pytest_rc, "summary": summary, "tail": tail,
           "failed": failed, "error": ""}
    hist = []
    if os.path.exists(hist_p):
        try:
            with open(hist_p, "r", encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    if any(abs((r.get("epoch") or 0) - epoch) < 1 for r in hist):
        print("SKIPPED: record for epoch %d already present" % int(epoch))
        return 0
    hist.append(rec)
    hist.sort(key=lambda r: -(r.get("epoch") or 0))
    with open(hist_p, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    print("BACKFILLED: epoch=%d pytest_rc=%s failed=%d"
          % (int(epoch), pytest_rc, len(failed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
