#!/usr/bin/env python3
"""Phase 17 verification: lockfile mutual exclusion + atomic state write.

Runs against a COPY of state.json in a scratch dir; never touches live state
and never imports the trading path in a way that contacts the broker.
"""
import json, os, subprocess, sys, tempfile, time, fcntl
from pathlib import Path

BASE = Path("/root/jp_strategy")
PY = str(BASE / "venv/bin/python")
ok = True


def check(name, cond, extra=""):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        ok = False


print("== 1. atomic save_state ==")
# Exercise save_state against a scratch STATE_FILE by monkeypatching module globals.
os.environ.setdefault("APCA_API_KEY_ID", "x")
os.environ.setdefault("APCA_API_SECRET_KEY", "x")
sys.path.insert(0, str(BASE))
import jp_agent as A

scratch = Path(tempfile.mkdtemp(prefix="ph17_"))
A.BASE_DIR = scratch
A.STATE_FILE = scratch / "state.json"
A.STATE_BAK = scratch / "state_backups"

st = {"positions": {"AAPL": {"shares": 10}}, "last_run": None}
A.save_state(st)
check("state.json written", A.STATE_FILE.exists())
check("state.json parses", json.loads(A.STATE_FILE.read_text())["positions"] == {"AAPL": {"shares": 10}})
check("no temp files left behind", not list(scratch.glob(".state.*.tmp")),
      f"({list(scratch.glob('.state.*.tmp'))})")

st["positions"]["MSFT"] = {"shares": 5}
A.save_state(st)
baks = sorted(A.STATE_BAK.glob("state_*.json"))
check("previous state backed up", len(baks) == 1, f"({len(baks)} backup(s))")
check("backup holds the OLD state",
      json.loads(baks[0].read_text())["positions"] == {"AAPL": {"shares": 10}})
check("new state has both positions",
      set(json.loads(A.STATE_FILE.read_text())["positions"]) == {"AAPL", "MSFT"})

# Atomicity: os.replace means the inode changes, and the old content is never
# visible truncated. Assert save_state does not open STATE_FILE for writing.
import inspect
srcs = inspect.getsource(A.save_state)
check("uses os.replace", "os.replace" in srcs)
check("fsyncs before rename", "fsync" in srcs)
check("no in-place truncating open of STATE_FILE",
      'open(STATE_FILE, "w")' not in srcs)

print("== 2. single-instance lock ==")
lock = BASE / ".jp_agent.lock"
# Hold the lock from this process, then try to acquire from a child.
fh = open(lock, "a+")
fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
fh.seek(0); fh.truncate(); fh.write("pid=TESTHOLDER\n"); fh.flush()

child = subprocess.run(
    [PY, "-c",
     "import sys; sys.argv=['jp_agent']; "
     "sys.path.insert(0,'/root/jp_strategy'); "
     "import jp_agent as A; A.acquire_lock(); print('ACQUIRED')"],
    capture_output=True, text=True, timeout=60, cwd=str(BASE))
check("second instance is refused", child.returncode == 9,
      f"(rc={child.returncode})")
check("refusal is loud in the log", "ALREADY IN PROGRESS" in child.stderr + child.stdout)
check("second instance did NOT proceed", "ACQUIRED" not in child.stdout)

fcntl.flock(fh.fileno(), fcntl.LOCK_UN); fh.close()

child2 = subprocess.run(
    [PY, "-c",
     "import sys; sys.argv=['jp_agent']; "
     "sys.path.insert(0,'/root/jp_strategy'); "
     "import jp_agent as A; A.acquire_lock(); print('ACQUIRED')"],
    capture_output=True, text=True, timeout=60, cwd=str(BASE))
check("lock is free once released", child2.returncode == 0 and "ACQUIRED" in child2.stdout,
      f"(rc={child2.returncode})")
check("lock released on process exit (no stale lock)",
      subprocess.run([PY, "-c",
                      "import sys; sys.path.insert(0,'/root/jp_strategy'); "
                      "import jp_agent as A; A.acquire_lock(); print('ACQUIRED')"],
                     capture_output=True, text=True, cwd=str(BASE)).returncode == 0)

print("== 3. --no-lock escape hatch present ==")
main_src = (BASE / "jp_agent.py").read_text()
check("--no-lock handled at entrypoint", '"--no-lock" in sys.argv' in main_src)
check("lock acquired before main() in cron path",
      main_src.index("acquire_lock()\n    main()") > 0)

print("== 4. monitoring liveness: missed scheduled runs, not elapsed hours ==")
#  Regression guard. The banner used `age_h > 26`, a flat wall-clock threshold,
#  so it showed a red "STALE -- agent may be down" from Friday evening to Monday
#  afternoon EVERY WEEK while the agent was healthy -- printing RUN_OK in the
#  same line. An alert that is wrong every weekend trains the reader to ignore
#  it, so the one weekend it is right looks identical to the noise.
import subprocess as _sp
from datetime import datetime as _dt, timezone as _tz

sys.path.insert(0, str(BASE))
from build_dashboard import missed_scheduled_runs, RUN_HOUR, RUN_MINUTE, ET as _ET


def _t(y, mo, d, h, mi):
    return _dt(y, mo, d, h, mi, tzinfo=_ET)


_FRI = _t(2026, 8, 28, 16, 30)      # a good Friday run
#  Expectations written from the schedule (30 16 * * 1-5) BY HAND -- never
#  computed from the function being tested.
for _label, _last, _now, _want in [
    ("weekend is NOT stale (the bug)",  _FRI, _t(2026, 8, 30, 21, 56), 0),
    ("saturday is not stale",           _FRI, _t(2026, 8, 29, 10, 0),  0),
    ("before the run is due",           _FRI, _t(2026, 8, 31, 12, 0),  0),
    ("inside the run grace window",     _FRI, _t(2026, 8, 31, 16, 40), 0),
    ("missed monday run IS stale",      _FRI, _t(2026, 8, 31, 17, 30), 1),
    ("ran monday, not stale", _t(2026, 8, 31, 16, 30), _t(2026, 8, 31, 17, 30), 0),
    ("missed midweek run",    _t(2026, 9,  1, 16, 30), _t(2026, 9,  2, 17, 30), 1),
    ("dead since friday counts weekdays only", _FRI, _t(2026, 9, 3, 17, 30), 4),
]:
    _got = missed_scheduled_runs(_last, _now)
    check(f"liveness: {_label}", _got == _want, f"(missed={_got}, expected {_want})")

#  The helper's constants must match the crontab, or the banner lies in
#  whichever direction the drift went.
try:
    _cron = _sp.run(["crontab", "-l"], capture_output=True, text=True).stdout
    _line = [l for l in _cron.splitlines()
             if "jp_agent.py" in l and not l.strip().startswith("#")]
    if _line:
        _f = _line[0].split()
        check("banner schedule matches the crontab",
              int(_f[1]) == RUN_HOUR and int(_f[0]) == RUN_MINUTE,
              f"(cron {_f[1]}:{_f[0]} vs banner {RUN_HOUR}:{RUN_MINUTE:02d})")
        check("crontab runs weekdays only, as the banner assumes",
              _f[4] in ("1-5", "MON-FRI"), f"(dow field {_f[4]!r})")
    else:
        check("crontab contains a jp_agent entry", False, "(no jp_agent cron line)")
except FileNotFoundError:
    print("    SKIP  crontab not available in this environment")

print()
print("PHASE17 VERIFY:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
