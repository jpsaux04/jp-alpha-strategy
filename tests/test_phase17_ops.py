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

print()
print("PHASE17 VERIFY:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
