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

print("== 5. dashboard reports the stop the AGENT will act on ==")
#  Regression guard. build_dashboard.py computed `entry * (1 - 0.08)` for every
#  long -- V3's fixed stop -- from V4 onward, so the Stop column, the
#  distance-to-stop cushion and the Open Risk tile all described a strategy that
#  was not running. The overstated cushion was the dangerous half: a position
#  could stop out while the page still showed comfortable room.
import importlib as _il
import os as _os

_ENTRY, _ATR, _QTY = 100.0, 3.0, 10

for _ver in ("JP_ALPHA_V3_FROZEN",
             "JP_ALPHA_V4_LONGONLY_STOPATR2",
             "JP_ALPHA_V5_LONGONLY_STOPATR15"):
    _os.environ["STRATEGY_VERSION"] = _ver
    for _m in ("jp_agent", "build_dashboard"):
        sys.modules.pop(_m, None)
    _A = _il.import_module("jp_agent")
    _D = _il.import_module("build_dashboard")

    _pos = {"direction": "long", "entry_price": _ENTRY, "fill_price": None,
            "atr_at_entry": _ATR, "shares_total": _QTY, "shares_remaining": _QTY,
            "t1_hit": False, "t2_hit": False, "t1_hit_date": None,
            "entry_date": _A.date.today().isoformat()}
    _state = {"positions": {"TEST": dict(_pos)}}
    _broker = [{"symbol": "TEST", "qty": str(_QTY),
                "avg_entry_price": str(_ENTRY), "current_price": str(_ENTRY),
                "unrealized_pl": "0", "unrealized_plpc": "0"}]

    _shown = _D.enrich_positions(_broker, _state)[0]["stop"]

    #  Find the agent's real trigger empirically rather than restating the
    #  formula: graze the level, then miss it by a cent.
    def _fires(low):
        r = _A.process_long_exits("TEST", dict(_pos),
                                  {"High": _ENTRY, "Low": low, "Close": _ENTRY},
                                  _A.date.today())
        a = r.get("action") if isinstance(r, dict) else (
            r[0] if isinstance(r, (tuple, list)) else r)
        return a == "STOP_LOSS"

    check(f"dashboard stop == agent stop [{_ver.split('_')[2]}]",
          _fires(round(_shown, 2)) and not _fires(round(_shown + 0.01, 2)),
          f"(dashboard {_shown:.2f}; agent fires at it: {_fires(round(_shown,2))}, "
          f"fires 1c above: {_fires(round(_shown + 0.01, 2))})")

#  A position opened under an older version keeps the multiple it was pinned
#  with -- the dashboard must honour that pin, not the live constant.
_os.environ["STRATEGY_VERSION"] = "JP_ALPHA_V5_LONGONLY_STOPATR15"
for _m in ("jp_agent", "build_dashboard"):
    sys.modules.pop(_m, None)
_D = _il.import_module("build_dashboard")
_legacy = {"positions": {"TEST": {"direction": "long", "entry_price": _ENTRY,
                                  "fill_price": None, "atr_at_entry": _ATR,
                                  "stop_atr_mult": 2.0,      # opened under V4
                                  "t1_hit": False, "t2_hit": False}}}
_shown = _D.enrich_positions(
    [{"symbol": "TEST", "qty": str(_QTY), "avg_entry_price": str(_ENTRY),
      "current_price": str(_ENTRY), "unrealized_pl": "0",
      "unrealized_plpc": "0"}], _legacy)[0]["stop"]
check("dashboard honours a V4-pinned stop while V5 is live",
      abs(_shown - (_ENTRY - 2.0 * _ATR)) < 1e-6,
      f"(showed {_shown:.2f}, expected {_ENTRY - 2.0*_ATR:.2f} = 2.0xATR, not 1.5x)")

_os.environ["STRATEGY_VERSION"] = "JP_ALPHA_V5_LONGONLY_STOPATR15"

print()
print("PHASE17 VERIFY:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
