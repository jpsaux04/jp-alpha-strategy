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
import build_dashboard as _bdm
from datetime import timedelta as _td
from datetime import datetime as _DTC   # line 283 rebinds _dt to the MODULE
_SS_REAL = _bdm.SCHEDULE_SINCE          # production value, restored in s.7
_FIXTURE_SINCE = _bdm._date(2026, 1, 1)


def _pin_schedule_since(val):
    """Pin SCHEDULE_SINCE on the build_dashboard that is CURRENTLY imported.

    Section 5 pops build_dashboard from sys.modules and re-imports it, so a
    value pinned on an earlier module object is silently lost -- and callers
    holding a function from either object then disagree. Always re-pin, and
    return the value actually in effect so the caller can assert it.
    """
    _m = sys.modules.get("build_dashboard") or _bdm
    _m.SCHEDULE_SINCE = val
    return _m.missed_scheduled_runs


def _t(y, mo, d, h, mi):
    return _dt(y, mo, d, h, mi, tzinfo=_ET)


#  These fixtures predate the real SCHEDULE_SINCE and test slot walking,
#  not the changeover. Pin it -- and ASSERT the pin took effect, because if it
#  silently did not, every expectation below collapses to missed=0 and passes.
_pin_schedule_since(_FIXTURE_SINCE)
check("liveness fixtures are not suppressed by SCHEDULE_SINCE",
      missed_scheduled_runs(_t(2026, 8, 28, 16, 20),
                            _t(2026, 9, 3, 17, 30)) == 4,
      "pin did not take effect")

_FRI = _t(2026, 8, 28, 16, 20)      # a good Friday run (16:20 schedule)
#  Expectations written from the schedule (20 16 * * 1-5) BY HAND -- never
#  computed from the function being tested.
for _label, _last, _now, _want in [
    ("weekend is NOT stale (the bug)",  _FRI, _t(2026, 8, 30, 21, 56), 0),
    ("saturday is not stale",           _FRI, _t(2026, 8, 29, 10, 0),  0),
    ("before the run is due",           _FRI, _t(2026, 8, 31, 12, 0),  0),
    #  slot 16:20 + 45min grace => not missed until 17:05. Probe must sit
    #  AFTER the slot, or this merely repeats "before the run is due".
    ("inside the run grace window",     _FRI, _t(2026, 8, 31, 16, 50), 0),
    ("missed monday run IS stale",      _FRI, _t(2026, 8, 31, 17, 30), 1),
    ("ran monday, not stale", _t(2026, 8, 31, 16, 20), _t(2026, 8, 31, 17, 30), 0),
    ("missed midweek run",    _t(2026, 9,  1, 16, 20), _t(2026, 9,  2, 17, 30), 1),
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

print("== 6. the DIGEST agrees with the BANNER about liveness ==")
#  monitor.py kept its own `age_h > 26` rule after the banner stopped using one,
#  so on any weekend the page said LIVE while the digest said CRITICAL "may be
#  down". Whichever a reader believes, one of them trained them to ignore it.
import monitor as _mon

#  Section 5 re-imported build_dashboard, so the pin from section 4 is gone
#  and _mon is bound to the NEW module. Re-pin it, and assert that _mon and
#  the banner helper are the same object -- if they are not, a
#  banner-vs-digest comparison proves nothing about production.
_pin_schedule_since(_FIXTURE_SINCE)
check("digest and banner share one liveness implementation",
      _mon.missed_scheduled_runs is
      sys.modules["build_dashboard"].missed_scheduled_runs,
      "monitor is bound to a different build_dashboard instance")

_cfg = {"heartbeat_stale_hours": 26.0}


def _hb_at(dt):
    return {"last_run_ts": dt.isoformat()}


for _label, _last, _now, _want_alert in [
    #  Friday run, read on Sunday: 47h elapsed but NO scheduled run missed.
    ("weekend quiet is not an outage",
     _t(2026, 9, 11, 16, 20), _t(2026, 9, 13, 16, 21), False),
    #  Monday's run never happened.
    ("a genuinely missed run still alerts",
     _t(2026, 9, 11, 16, 20), _t(2026, 9, 14, 17, 30), True),
    #  Ran on schedule an hour ago.
    ("a run that happened does not alert",
     _t(2026, 9, 14, 16, 20), _t(2026, 9, 14, 16, 50), False),
]:
    _got = _mon.check_heartbeat(_hb_at(_last), _cfg, _now)
    check(f"digest liveness: {_label}", bool(_got) == _want_alert,
          f"(alerts={[a.code for a in _got]}, expected alert={_want_alert})")

#  And the two modules must not merely both be quiet -- they must agree.
for _label, _last, _now in [
    ("sunday",   _t(2026, 9, 11, 16, 20), _t(2026, 9, 13, 16, 21)),
    ("missed monday", _t(2026, 9, 11, 16, 20), _t(2026, 9, 14, 17, 30)),
    ("dead a week",   _t(2026, 9,  4, 16, 20), _t(2026, 9, 14, 17, 30)),
]:
    _banner = sys.modules["build_dashboard"].missed_scheduled_runs(_last, _now)
    _digest = _mon.check_heartbeat(_hb_at(_last), _cfg, _now)
    _dmissed = _digest[0].detail.get("missed_runs") if _digest else 0
    check(f"banner and digest agree [{_label}]", _banner == _dmissed,
          f"(banner says {_banner} missed, digest says {_dmissed})")

#  ----------------------------------------------------------------------
#  7. DASHBOARD DATE CONVENTION.
#
#  Alpaca stamps 1D portfolio-history rows at 20:00 ET, which is 00:00 UTC the
#  NEXT calendar day. Converting those timestamps in UTC labels every row one
#  day late: the whole curve shifts, each Friday close becomes a "Saturday",
#  and build_dashboard's trading-day filter then deletes those rows because
#  they have no SPY match. The visible damage is a mislabelled x-axis; the
#  invisible damage is that the SPY overlay, excess return, beta and the alpha
#  t-stat are all computed against the wrong day's benchmark.
#
#  Feed the real function a canned response of 20:00-ET closes and require the
#  dates back to be those same weekdays.
import datetime as _dt
import importlib
import types

_bd = importlib.import_module("build_dashboard")

#  Five consecutive weekday closes, Mon 2026-10-05 .. Fri 2026-10-09, each at
#  20:00 ET -- exactly how Alpaca emits them.
_want = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
_stamps = [int(_dt.datetime.fromisoformat(d + "T20:00:00")
               .replace(tzinfo=_bd.ET).timestamp()) for d in _want]

_real_get = _bd.requests.get


def _fake_get(url, **kw):
    r = types.SimpleNamespace()
    if "portfolio/history" in url:
        r.json = lambda: {"timestamp": _stamps,
                          "equity": [100.0] * len(_stamps)}
    else:                                     # /v2/account, for the period calc
        r.json = lambda: {"created_at": "2026-05-01T00:00:00Z"}
    return r


_prev_env = {k: os.environ.get(k) for k in
             ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")}
try:
    _bd.requests.get = _fake_get
    os.environ.setdefault("APCA_API_KEY_ID", "test")
    os.environ.setdefault("APCA_API_SECRET_KEY", "test")
    _got = [row["date"] for row in _bd.fetch_portfolio_history()]
finally:
    _bd.requests.get = _real_get
    for _k, _v in _prev_env.items():
        if _v is None:
            os.environ.pop(_k, None)

check("portfolio history dates are ET, not UTC-shifted", _got == _want,
      f"(got {_got}, expected {_want})")
check("no weekend rows manufactured from Friday closes",
      all(_dt.date.fromisoformat(d).weekday() < 5 for d in _got),
      f"(got {_got})")

# ── 7. schedule changeover must not fabricate OR hide an outage ───────────
print("== 7. schedule changeover does not fabricate or hide outages ==")
_msr = _pin_schedule_since(_SS_REAL)    # back to the production value
_SS = _SS_REAL

#  The bug being fixed: a run that SUCCEEDED under the old 15:45 time, read
#  after the new 16:20 slot had passed, was reported as a missed run --
#  publishing "STALE" next to a heartbeat saying RUN_OK.
_prev = _DTC(_SS.year, _SS.month, _SS.day, 15, 45, tzinfo=_ET) - _td(days=1)
_read = _DTC(_SS.year, _SS.month, _SS.day, 17, 30, tzinfo=_ET) - _td(days=1)
_m0 = _msr(_prev, _read)
check("a good run under the OLD schedule is not reported missed",
      _m0 == 0, f"missed={_m0}")

#  The suppression must stop AT the changeover. An outage on or after it is
#  still an outage -- otherwise this mechanism is just a mute button.
_due = _DTC(_SS.year, _SS.month, _SS.day, RUN_HOUR, RUN_MINUTE, tzinfo=_ET)
if _due.weekday() in range(0, 5):
    _m1 = _msr(_prev, _due + _td(hours=2))
    check("a real outage ON the changeover day is still caught",
          _m1 >= 1, f"missed={_m1}")

#  A SCHEDULE_SINCE dated in the future suppresses every day up to it, which
#  is the failure mode of this mechanism. Bound it.
check("SCHEDULE_SINCE is not dated in the future",
      _SS <= _DTC.now(_ET).date() + _td(days=1),
      f"SCHEDULE_SINCE={_SS} is ahead of today -- outage detection suppressed")

# ── 8. the equity-curve regime marker ─────────────────────────────────────
print("== 8. execution-regime marker is anchored and scoped ==")
_bd8 = sys.modules["build_dashboard"]

#  The marked boundary IS the schedule change. If someone edits one and not
#  the other, the curve claims the strategy changed on a day it did not.
_dates8 = [d for d, _s, _w in _bd8.EXEC_REGIME_CHANGES]
check("regime marker is anchored to SCHEDULE_SINCE",
      _bd8.SCHEDULE_SINCE in _dates8,
      f"marks={_dates8}, SCHEDULE_SINCE={_bd8.SCHEDULE_SINCE}")
check("every regime change carries a short label and an explanation",
      all(str(_s).strip() and str(_w).strip()
          for _d, _s, _w in _bd8.EXEC_REGIME_CHANGES),
      "a marker with no explanation is a mystery line on a public chart")

#  Scoping: in-window boundaries must be emitted, out-of-window ones must not.
#  Mirrors the filter in render() -- strictly after the first point (a line on
#  the y-axis marks nothing) and no later than the last.
def _marks_for(labels, changes):
    out = []
    for _d, _sh, _wh in changes:
        _iso = _d.isoformat()
        if labels and labels[0] < _iso <= labels[-1]:
            out.append(_iso)
    return out


_lab8 = ["2026-09-10", "2026-09-15", "2026-09-20"]
_mk = lambda ds: [(_bd8._date(*map(int, d.split("-"))), "s", "w") for d in ds]
for _label, _ch, _want in [
    ("inside the window is marked",        ["2026-09-15"], ["2026-09-15"]),
    ("on the last point is marked",        ["2026-09-20"], ["2026-09-20"]),
    ("before the window is not",           ["2026-08-01"], []),
    ("on the first point is not (no split in view)", ["2026-09-10"], []),
    ("after the window is not",            ["2026-10-30"], []),
]:
    _got = _marks_for(_lab8, _mk(_ch))
    check(f"regime scope: {_label}", _got == _want, f"(got {_got}, want {_want})")

check("regime scope: no data means no marker",
      _marks_for([], _mk(["2026-09-15"])) == [], "empty series")

# ── 9. Analysis tab: card N must describe column N ────────────────────────
print("== 9. condition cards line up with the dot columns ==")
import re as _re9
_bd9 = sys.modules["build_dashboard"]

#  The dots come from scan_universe's `lc` literal, the headings from
#  COND_KEYS. They are separate lists, so they can drift. Read the literal
#  out of the source and compare -- no network, no live data needed.
_src9 = (BASE / "build_dashboard.py").read_text()
_m9 = _re9.search(r"lc = \[(.*?)\] if ld else \[\]", _src9, _re9.S)
check("found the long-condition literal in scan_universe", bool(_m9))
if _m9:
    _lit = tuple(_re9.findall(r'\("([A-Z0-9]+)",', _m9.group(1)))
    check("dot order in scan_universe matches COND_KEYS",
          _lit == _bd9.COND_KEYS,
          f"literal={_lit}, COND_KEYS={_bd9.COND_KEYS}")

_meta9 = {"rsi_period": 14, "rsi_os": 45, "rsi_ob": 60, "min_long_disl": 0.02,
          "vol_period": 20, "vol_cap": 1.3, "close_pos": 0.5,
          "regime_max": 0.10, "regime_ma": 50, "spy_dev": 0.003,
          "regime_long": True, "allow_shorts": False, "universe": 42,
          "max_open": 8, "max_long": 8, "max_sector": 2, "bar": "2026-10-15",
          "settled": True, "vol_complete": 1.02}
_g9 = _bd9.cond_guide(_meta9)
check("guide has one card per condition, in COND_KEYS order",
      tuple(g["key"] for g in _g9) == _bd9.COND_KEYS,
      f"guide={[g['key'] for g in _g9]}")
check("every card has a name, a rule and a reason",
      all(g.get("name") and g.get("rule") and g.get("why") for g in _g9))

#  Render with synthetic rows: condition i true, all others false, so a
#  misalignment shows up as a filled dot in the wrong column.
def _row9(i):
    _c = [(k, j == i) for j, k in enumerate(_bd9.COND_KEYS)]
    return {"symbol": f"T{i}", "sector": "Test", "price": 100.0, "rsi": 50.0,
            "ma_pct": -1.0, "atr_pct": 2.0, "zone": "neutral",
            "long_conds": _c, "short_conds": [],
            "n_long": 1, "n_short": 0, "long_sig": False, "short_sig": False,
            "reconciles": True, "held": None}


_html9 = _bd9.render_scan([_row9(i) for i in range(len(_bd9.COND_KEYS))], _meta9)
_heads = _re9.findall(r'<th class="srt ccol" title="(\d+)\. ([^"]+)">(\d+)</th>',
                      _html9)
check("table has exactly one numbered column per condition",
      len(_heads) == len(_bd9.COND_KEYS), f"found {len(_heads)}")
check("column headings are numbered 1..N in guide order",
      [h[2] for h in _heads] == [str(i + 1) for i in range(len(_g9))]
      and [h[1] for h in _heads] == [g["name"] for g in _g9],
      f"heads={_heads}")

#  Positional check: row i must have its filled dot in column i.
_bodies = _re9.findall(r"<tr><td><b>T(\d+)</b>.*?</tr>", _html9, _re9.S)
check("every synthetic row rendered", len(_bodies) == len(_bd9.COND_KEYS),
      f"{len(_bodies)} rows")
for _i, _tr in enumerate(_re9.findall(r"(<tr><td><b>T\d+</b>.*?</tr>)",
                                      _html9, _re9.S)):
    _sym = int(_re9.search(r"<b>T(\d+)</b>", _tr).group(1))
    _cells = _re9.findall(r'<td class="ccol (y|n)"', _tr)
    check(f"row T{_sym}: filled dot sits in column {_sym + 1}",
          len(_cells) == len(_bd9.COND_KEYS)
          and [k for k, c in enumerate(_cells) if c == "y"] == [_sym],
          f"cells={_cells}")

#  The intraday volume caveat: present on a forming bar, absent on a settled
#  one. If it were always on, the count would be permanently hidden.
_CAV = "Not measurable until the close"
check("settled bar reports a real volume count",
      _CAV not in _bd9.render_scan([_row9(2)], dict(_meta9, settled=True)))
check("forming bar refuses to report the suppressed volume count",
      _CAV in _bd9.render_scan([_row9(2)], dict(_meta9, settled=False)))

# ── 10. Research tab: no stale numbers, no dead tab, caveat first ─────────
print("== 10. research tab renders only what was measured ==")
import re as _re10
import json as _js10
_bd10 = sys.modules["build_dashboard"]

#  (1) No artifact -> say so, and leak no figures. A number on this page with
#      nothing behind it is the failure the whole artifact design prevents.
_empty = _bd10.render_research(None)
check("missing artifact is reported, not papered over",
      "not been run" in _empty)
check("missing artifact leaks no percentages",
      not _re10.search(r"\d+\.\d%", _empty),
      f"found {_re10.findall(r'[-+]?\d+\.\d%', _empty)[:5]}")
check("missing artifact leaks no dollar amounts",
      not _re10.search(r"\$[\d,]{4,}", _empty))
_bad = _bd10.render_research({})
check("empty-dict artifact also refuses to render results",
      "not been run" in _bad)

#  (2) Render from a synthetic artifact so the assertions do not depend on
#      whatever the last real run produced.
_ART = {
    "generated": "2026-10-16 11:57 UTC", "span": ["2021-08-18", "2026-10-16"],
    "names": 42, "sessions": 1297, "start_equity": 100000.0,
    "config": {"max_longs": 7, "max_simultaneous": 10, "max_per_sector": 2,
               "time_stop": 21, "post_t1_stop": 30, "risk": 0.01,
               "stop_mult": 1.5, "t1": 0.04, "t2": 0.08, "t3": 0.12,
               "rsi_os": 45, "min_disl": 0.02, "vol_cap": 1.3,
               "close_pos": 0.5, "regime_max": 0.10, "version": "V5"},
    "headline": {"final": 202598.0, "cagr": 0.147, "dd": -0.128,
                 "sharpe": 1.25, "trades": 298, "win": 0.46, "R": 87.5,
                 "pos_days": 0.0},
    "spy": {"final": 187906.0, "cagr": 0.130, "dd": -0.245, "sharpe": 0.81,
            "trades": 0, "win": 0, "R": 0, "pos_days": 0.0},
    "paths": [{"path": "STOP", "n": 120, "pct": 0.403, "mean_ret": -0.0431,
               "mean_r": -1.0},
              {"path": "T1+T2+T3", "n": 102, "pct": 0.342, "mean_ret": 0.09,
               "mean_r": 1.98}],
    "hold": {"mean": 19.0, "median": 14.0},
    #  (7,21) is live at 0.147; (7,14) sits 0.005 away -> inside the 0.013
    #  noise band; the ml=3 row is far below -> clearly worse. The grid must
    #  distinguish all three, so the fixture contains all three.
    "grid": [{"max_longs": ml, "time_stop": ts,
              "cagr": {(7, 21): 0.147, (7, 14): 0.152,
                       (3, 21): 0.084, (3, 14): 0.092}[(ml, ts)],
              "dd": -0.12, "sharpe": 1.2, "trades": 290, "win": .46,
              "R": 80.0, "pos_days": 0.0, "final": 2e5}
             for ml in (3, 7) for ts in (14, 21)],
    "random": {"mean": 0.148, "sd": 0.013, "p5": 0.127, "p95": 0.169,
               "live": 0.147, "pct": 0.505, "n": 200},
    "risk_sweep": [{"risk": 0.005, "cagr": 0.10, "dd": -0.079, "sharpe": 1.28,
                    "trades": 290, "win": .46, "R": 80.0, "pos_days": 0.0,
                    "final": 1.6e5},
                   {"risk": 0.01, "cagr": 0.147, "dd": -0.128, "sharpe": 1.25,
                    "trades": 298, "win": .46, "R": 87.5, "pos_days": 0.0,
                    "final": 2e5}],
    "conditions": {"evaluated": 6614, "signals": 489, "base_mean": 0.0059,
                   "base_se": 0.0019,
                   "rows": [{"key": "VOL", "name": "Selling drying up",
                             "n": 4195, "mult": 8.6, "mean": 0.0053,
                             "diff": -0.0005, "t": -0.27, "dates": 956,
                             "top5": 0.031,
                             "verdict": "indistinguishable"},
                            {"key": "REGIME", "name": "Market not over-extended",
                             "n": 30, "mult": 0.06, "mean": 0.0132,
                             "diff": 0.0074, "t": 1.17, "dates": 7,
                             "top5": 0.933,
                             "verdict": "indistinguishable"}]},
    "ranking": {"signal_days": 267, "mean_per_day": 1.8, "median_per_day": 1.0,
                "max_per_day": 18,
                "crowded": [{"thr": 1, "n": 267, "pct": 1.0},
                            {"thr": 8, "n": 9, "pct": 0.034}]},
}
_h10 = _bd10.render_research(_ART)

#  (3) The caveat must come BEFORE any result the reader would act on.
check("survivorship caveat is present", "Survivorship" in _h10)
_i_cav = _h10.find("Survivorship")
_i_num = _h10.find("Strategy CAGR")
check("caveat appears above the headline figures",
      0 <= _i_cav < _i_num, f"caveat@{_i_cav} figures@{_i_num}")

#  Figures must come from the artifact, not from the template.
check("headline CAGR is formatted from the artifact",
      "14.7%" in _h10 and "13.0%" in _h10)
check("drawdown comparison is rendered",
      "-12.8%" in _h10 and "-24.5%" in _h10)
check("live grid cell is marked", 'class="gc live"' in _h10)
check("grid marks cells inside the noise band as indistinguishable",
      'class="gc noise"' in _h10)
check("grid still marks clearly worse cells as worse",
      'class="gc worse"' in _h10)
_n_noise = _h10.count('class="gc noise"')
_n_worse = _h10.count('class="gc worse"')
check("noise band is not swallowing every cell",
      _n_noise == 1 and _n_worse == 2,
      f"noise={_n_noise} worse={_n_worse} (expected 1 and 2)")
_live_cells = _h10.count('class="gc live"')
check("exactly one live grid cell", _live_cells == 1, f"{_live_cells}")
check("random-selection verdict is computed, not asserted",
      "indistinguishable from random" in _h10)
check("clustered condition is flagged as unreliable",
      "ignore" in _h10 and "7 days" in _h10)

#  A different artifact must produce a different page -- proves the numbers
#  are not baked into the template.
_ART2 = _js10.loads(_js10.dumps(_ART))
#  Rewrite EVERY cagr the artifact carries. The global check below only
#  means something if the fixture genuinely no longer contains 0.147.
_ART2["headline"]["cagr"] = 0.031
for _g in _ART2["grid"] + _ART2["risk_sweep"]:
    _g["cagr"] = 0.031
_ART2["random"]["live"] = 0.031
_h10b = _bd10.render_research(_ART2)


def _tile10(html, lbl):
    """The value out of a named headline tile, so the check is scoped to the
    figure under test instead of anywhere in the document."""
    _m = _re10.search(_re10.escape(lbl)
                      + r"</div>\s*<div class=\"val\">([^<]+)", html)
    return _m.group(1) if _m else None


check("headline tile reads from the artifact",
      _tile10(_h10, "Strategy CAGR") == "14.7%",
      f"got {_tile10(_h10, 'Strategy CAGR')}")
check("changing the artifact changes the rendered figure",
      _tile10(_h10b, "Strategy CAGR") == "3.1%",
      f"got {_tile10(_h10b, 'Strategy CAGR')}")
check("no CAGR is baked into the template anywhere on the page",
      "14.7%" not in _h10b,
      "a 14.7% survived an artifact that no longer contains 0.147")

#  (2) The dead-tab guard: TAB_JS's id list vs the panes actually emitted.
_panes = set(_re10.findall(r'<div id="tab-([a-z]+)"', _bd10.__dict__["render"]
                           .__doc__ or "")) or None
_src10 = (BASE / "build_dashboard.py").read_text()
_panes = set(_re10.findall(r'<div id="tab-([a-z0-9]+)"', _src10))
_jsids = set(_re10.findall(r"'([a-z0-9]+)'",
                           _re10.search(r"\[([^\]]*)\]\.forEach",
                                        _bd10.TAB_JS).group(1)))
check("every HTML pane is in the TAB_JS id list",
      _panes == _jsids, f"panes={sorted(_panes)} js={sorted(_jsids)}")
check("there is a button for every pane",
      all(f'id="btn-{p}"' in _src10 for p in _panes),
      f"missing buttons for "
      f"{[p for p in _panes if f'id=' + chr(34) + 'btn-' + p + chr(34) not in _src10]}")

print()
print("PHASE17 VERIFY:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
