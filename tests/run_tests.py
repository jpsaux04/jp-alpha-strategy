#!/usr/bin/env python3
"""PHASE 18 — CONSOLIDATED TEST SUITE.

One command that either says the system is safe to run or says exactly which
guarantee has broken. Four suites, each defending a different claim:

  test_execution.py   BROKER CORRECTNESS. Idempotent client order ids, fill-
                      driven state, snapshot-and-rollback on exit failure,
                      reconciliation halt gate. Runs against a FakeBroker, so
                      it needs no credentials and touches no live account.

  test_lookahead.py   RULE #2. Truncation invariance of every indicator, no
                      bfill / interpolate / negative shift anywhere, fills are
                      not the signal bar's own close, and backtest.py's
                      indicators are numerically identical to jp_agent.py's.
                      If this fails, every backtest number in the repo is void.

  test_phase17_ops.py OPERATIONAL SAFETY. Single-instance lock, atomic state
                      write with backup, no secrets on disk.

  tests/test_strategy_versions.py   STRATEGY VERSION GATE. Every version resolves to the
                      constants it claims, stops fire at the right level, and
                      long-only versions cannot emit a short.

Exit status is 0 only if every suite passes. That is the point: a partial pass
is a fail, because the suites defend independent guarantees and there is no
useful sense in which three-quarters of them holding is reassuring.

Usage: venv/bin/python tests/run_tests.py [-v]
"""
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable

SUITES = [
    ("execution",  "tests/test_execution.py",    "broker idempotency, rollback, halt gate"),
    ("lookahead",  "tests/test_lookahead.py",    "Rule #2 + backtest/live indicator parity"),
    ("ops",        "tests/test_phase17_ops.py",  "single-instance lock, atomic state, secrets"),
    ("versions",   "tests/test_strategy_versions.py", "V3/V4/V5 gate: constants, stop levels, no-short guarantee"),
]

VERBOSE = "-v" in sys.argv


def main():
    print("=" * 78)
    print("  JP-ALPHA CONSOLIDATED TEST SUITE  (Phase 18)")
    print("=" * 78)
    print(f"  interpreter : {PY}")
    print(f"  repo        : {REPO}")

    results = []
    for name, path, what in SUITES:
        full = os.path.join(REPO, path)
        if not os.path.exists(full):
            print(f"\n  MISSING  {name:<12} {path}")
            results.append((name, "MISSING", 0.0, ""))
            continue
        print(f"\n  RUN      {name:<12} {what}")
        t0 = time.time()
        r = subprocess.run([PY, full], cwd=REPO, capture_output=True, text=True)
        dt = time.time() - t0
        out = (r.stdout or "") + (r.stderr or "")
        if VERBOSE or r.returncode != 0:
            for line in out.rstrip().splitlines():
                print(f"      | {line}")
        # pull the suite's own tally line if it printed one
        tally = ""
        for line in reversed(out.splitlines()):
            if "passed" in line and "failed" in line:
                tally = line.strip()
                break
        status = "PASS" if r.returncode == 0 else "FAIL"
        print(f"  {status:<8} {name:<12} {dt:6.1f}s   {tally}")
        results.append((name, status, dt, tally))

    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    for name, status, dt, tally in results:
        print(f"    {status:<8} {name:<14}{dt:7.1f}s   {tally}")

    bad = [n for n, s, _, _ in results if s != "PASS"]
    print()
    if bad:
        print(f"  {len(bad)} of {len(results)} suites did not pass: {', '.join(bad)}")
        print("  The system should NOT be considered safe to run unattended.")
    else:
        print(f"  All {len(results)} suites pass.")
        print("  This certifies broker correctness, absence of look-ahead,")
        print("  backtest/live parity and operational safety — nothing about")
        print("  whether the strategy is profitable.")
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
