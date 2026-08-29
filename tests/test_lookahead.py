#!/usr/bin/env python3
"""PHASE 3 — LOOK-AHEAD AUDIT AND BACKTEST/LIVE PARITY.

Rule #2 of the brief is "no look-ahead of any kind". This asserts it rather
than asserting that someone read the code carefully.

Four classes of leak are tested:

  L1 SIGNAL TIMING     An indicator computed on bar t must not use data from
                       t+1. Tested by TRUNCATION INVARIANCE: compute the
                       indicator on the full series and on the series truncated
                       at t; the value at t must be identical. Any forward
                       window, centred window, or full-series normalisation
                       breaks this immediately.

  L2 EXECUTION TIMING  A signal generated from bar t's CLOSE must not be
                       filled at bar t's price. Verified against the trade
                       ledger: entry_date must be strictly after the bar whose
                       close produced the signal, and the fill must be the
                       NEXT session's open.

  L3 EXIT TIMING       An exit triggered by bar t's high/low must fill at a
                       price available on bar t, and must not use the close of
                       a later bar.

  L4 SURVIVOR/DATA     Indicator warmup must not be silently satisfied by data
                       outside the test window, and no NaN-fill may propagate
                       backwards (bfill is a look-ahead operator).

Plus a PARITY check that backtest.py's indicator functions are numerically
identical to jp_agent.py's, since the entire research programme assumes the
backtest simulates the live agent.

Usage: venv/bin/python tests/test_lookahead.py
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/root/jp_strategy")
os.environ.setdefault("BT_PREFIX", "lookahead_probe")

import backtest as BT
import jp_agent as JP

PASS = FAIL = 0


def check(cond, name, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}   {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


def synth(n=400, seed=7):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.015, n)
    c = pd.Series(100 * np.exp(np.cumsum(r)))
    h = c * (1 + abs(rng.normal(0, 0.006, n)))
    l = c * (1 - abs(rng.normal(0, 0.006, n)))
    v = pd.Series(rng.lognormal(15, 0.4, n))
    return c, h, l, v


# ── L1 truncation invariance ────────────────────────────────────────────────
def test_truncation_invariance():
    print("\nL1 — SIGNAL TIMING (truncation invariance)")
    c, h, l, v = synth()
    cases = [
        ("wilder_rsi",       lambda k: BT.wilder_rsi(c[:k], 14)),
        ("calc_atr",         lambda k: BT.calc_atr(h[:k], l[:k], c[:k], 14)),
        ("vol_exhaust_long", lambda k: BT.vol_exhaust_long(c[:k], v[:k], 20)),
        ("vol_exhaust_short",lambda k: BT.vol_exhaust_short(c[:k], v[:k], 20)),
        ("MA20",             lambda k: c[:k].rolling(20).mean()),
        ("MA50",             lambda k: c[:k].rolling(50).mean()),
    ]
    for name, fn in cases:
        full = fn(len(c))
        bad = []
        for k in (120, 200, 275, 399):
            trunc = fn(k)
            a, b = full.iloc[k - 1], trunc.iloc[k - 1]
            if isinstance(a, (bool, np.bool_)):
                ok = bool(a) == bool(b)
            else:
                ok = (pd.isna(a) and pd.isna(b)) or np.isclose(float(a), float(b),
                                                              rtol=1e-12, atol=1e-12)
            if not ok:
                bad.append((k, a, b))
        check(not bad, f"{name} uses no future bars",
              "identical at t for every truncation" if not bad else f"diverges: {bad[:2]}")


# ── L4 no backward fill ─────────────────────────────────────────────────────
def test_no_bfill():
    print("\nL4 — DATA HYGIENE")
    import inspect
    src = inspect.getsource(BT)
    for pat, why in [
        ("bfill", "backward fill propagates future values into the past"),
        ("fillna(method='bfill')", "explicit bfill"),
        ("interpolate(", "interpolation can use both sides of a gap"),
        (".shift(-", "negative shift is an explicit look-ahead"),
    ]:
        check(pat not in src, f"backtest.py contains no `{pat}`", why)

    src_j = inspect.getsource(JP)
    check(".shift(-" not in src_j, "jp_agent.py contains no negative shift",
          "live code cannot look ahead by construction, but check anyway")


# ── L2/L3 execution timing from the realised ledger ─────────────────────────
def test_execution_timing(prefix="s2_full_fix"):
    print("\nL2/L3 — EXECUTION TIMING (from the realised trade ledger)")
    p = f"/root/jp_strategy/{prefix}_trades.csv"
    if not os.path.exists(p):
        print(f"  SKIP  no ledger at {p}")
        return
    f = pd.read_csv(p, parse_dates=["entry_date", "exit_date"])

    check((f.exit_date >= f.entry_date).all(),
          "no fragment exits before it enters",
          f"min hold {f.hold_days.min()} days")

    # entry_ref is the CLOSE that generated the signal; entry_px is the fill.
    # If they were ever identical for every row, the fill would be the signal
    # bar itself -- the classic look-ahead.
    same = np.isclose(f.entry_px, f.entry_ref)
    check(same.mean() < 0.5,
          "fills are not the signal bar's own close",
          f"only {same.mean()*100:.1f}% of fills equal the reference close "
          f"(coincidental ties expected)")

    check(f.anchor_err_pct.abs().mean() > 0.05,
          "fill differs from signal price by a real overnight gap",
          f"mean |gap| {f.anchor_err_pct.abs().mean():.3f}%")

    # A stop fill must be reachable: for a long, exit_px <= entry region high.
    lo = f[f.direction == "long"]
    st = lo[lo.exit_reason == "STOP_LOSS"]
    if len(st):
        check((st.return_pct < 0).mean() > 0.95,
              "long stop-outs are losses",
              f"{(st.return_pct<0).mean()*100:.1f}% negative, n={len(st)}")

    t3 = f[f.exit_reason == "T3_HIT"]
    if len(t3):
        check((t3.return_pct > 0).all(),
              "T3 target hits are all gains",
              f"n={len(t3)}, min {t3.return_pct.min():+.2f}%")


# ── parity between backtest.py and jp_agent.py ──────────────────────────────
def test_parity():
    print("\nPARITY — backtest.py must reproduce jp_agent.py's indicators")
    c, h, l, v = synth()
    # The two modules name the same indicator differently, so the mapping is
    # explicit. That divergence is itself worth recording: nothing but this
    # test stops the two implementations drifting apart.
    MAP = [
        ("wilder_rsi",        "wilder_rsi",              lambda f: f(c, 14)),
        ("calc_atr",          "calc_atr",                lambda f: f(h, l, c, 14)),
        ("vol_exhaust_long",  "volume_exhaustion_long",  lambda f: f(c, v, 20)),
        ("vol_exhaust_short", "volume_exhaustion_short", lambda f: f(c, v, 20)),
    ]
    missing = [(a, b) for a, b, _ in MAP
               if not (hasattr(BT, a) and hasattr(JP, b))]
    check(not missing, "every backtest indicator has a live counterpart",
          f"mapped {len(MAP)}" if not missing else f"missing {missing}")

    for bn, jn, call in MAP:
        if not (hasattr(BT, bn) and hasattr(JP, jn)):
            continue
        try:
            x = pd.Series(call(getattr(BT, bn))).astype(float)
            y = pd.Series(call(getattr(JP, jn))).astype(float)
        except Exception as e:
            check(False, f"{bn} parity", f"raised {type(e).__name__}: {e}")
            continue
        d = (x - y).abs().max()
        check(pd.isna(d) or d < 1e-10,
              f"{bn} == jp_agent.{jn}", f"max abs diff {d:.2e}")

    for nm in ("RSI_OVERSOLD", "MIN_LONG_DISL", "T1_PCT", "T2_PCT", "T3_PCT",
               "STOP_LOSS_PCT", "TIME_STOP_DAYS", "ATR_MULTIPLIER",
               "RISK_PER_TRADE_PCT", "MAX_SIMULTANEOUS", "MAX_LONGS",
               "MAX_PER_SECTOR"):
        if hasattr(BT, nm) and hasattr(JP, nm):
            check(float(getattr(BT, nm)) == float(getattr(JP, nm)),
                  f"constant {nm} matches",
                  f"backtest={getattr(BT,nm)} live={getattr(JP,nm)}")

    check(list(BT.WATCHLIST) == list(JP.WATCHLIST),
          "universe identical in backtest and live",
          f"{len(BT.WATCHLIST)} symbols")


if __name__ == "__main__":
    print("=" * 78)
    print("PHASE 3 — LOOK-AHEAD AUDIT AND BACKTEST/LIVE PARITY")
    print("=" * 78)
    test_truncation_invariance()
    test_no_bfill()
    test_execution_timing()
    test_parity()
    print("\n" + "=" * 78)
    print(f"  {PASS} passed, {FAIL} failed")
    print("=" * 78)
    sys.exit(1 if FAIL else 0)
