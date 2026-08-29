#!/usr/bin/env python3
"""PHASE 10 — NESTED WALK-FORWARD.

WHAT IS ACTUALLY BEING VALIDATED
--------------------------------
This strategy fits no parameters. Nothing is estimated from data. The ONLY
thing selected using historical performance is the VARIANT CHOICE -- which is
precisely how `longonly_stopatr2` came to be the V4 candidate
(docs/RESEARCH_REPORT.md PART 3 §7). So the object under test here is not a
strategy, it is a SELECTION PROCEDURE:

    "look at history, pick the best-performing variant, trade it forward"

We run that procedure honestly -- selecting only on data strictly before each
test window -- and measure what it delivers on data it has never seen. This is
the only test that can distinguish a real edge from the selection bias that
consumed the original OOS window.

DESIGN NOTES
------------
* Each variant is simulated ONCE continuously over the full window and folds
  are sliced from it. Re-running per fold would make every fold pay the
  60-session indicator warmup and would reset position state 8 times, which is
  less realistic, not more.
* Test folds are 6 months, NON-OVERLAPPING, so stitched OOS returns are a
  genuine tradeable path with no reuse of any day.
* Two selection rules are compared, because the choice of lookback is itself a
  researcher degree of freedom and hiding it would be dishonest:
      A. EXPANDING  -- select on all history before the test window
      B. TRAILING12 -- select on the 12 months before the test window
* An ORACLE row (best variant chosen with hindsight per fold) is reported as an
  upper bound. It is not attainable; it is the yardstick that shows how much of
  the in-sample result is pure hindsight.
* Everything is reported NET of Phase 6 costs (10bps/leg, 1% borrow).

Usage: python3 research/walk_forward.py [--slip 10] [--borrow 0.01] [--crit sharpe|ret]
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "research")
from cost_stress import (load, statutory_costs, slippage_cost, borrow_cost,
                         dividend_subsidy, net_equity_curve)

TRADING_DAYS = 252

VARIANTS = ["base_ls", "ls_atr2", "lo", "lo_atr15", "lo_atr2", "lo_atr25",
            "lo_atr3", "lo_nsc", "lo_trail3", "lo_trail4", "lo_t316",
            "lo_t320", "lo_atr2_nsc", "lo_atr2_t316"]

CANDIDATE = "lo_atr2"        # the V4 candidate = longonly_stopatr2 + EXEC-2 fix
DEFAULT_CTRL = "base_ls"     # the frozen live strategy

TEST_STARTS = ["2022-07-01", "2023-01-01", "2023-07-01", "2024-01-01",
               "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01"]
FOLD_MONTHS = 6
MIN_SELECT_DAYS = 500        # ~2 yrs before the first selection is allowed


PFX = "wfv_"          # grid files written by research/wf_grid.sh


def net_returns(name, slip, borrow):
    f, eq = load(PFX + name)
    tot = (statutory_costs(f) + slippage_cost(f, slip)
           + borrow_cost(f, borrow) + dividend_subsidy(f))
    return net_equity_curve(eq, f, tot).pct_change().dropna()


def spy_returns(index):
    import yfinance as yf
    px = yf.download("SPY", start=index.min().strftime("%Y-%m-%d"),
                     end=(index.max() + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                     progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.DataFrame):
        px = px.iloc[:, 0]
    return px.pct_change().reindex(index).fillna(0.0)


def sharpe(r):
    r = np.asarray(r, float)
    return r.mean() / r.std() * np.sqrt(TRADING_DAYS) if len(r) > 5 and r.std() > 0 else np.nan


def cagr(r):
    r = np.asarray(r, float)
    if len(r) == 0:
        return np.nan
    return np.prod(1 + r) ** (TRADING_DAYS / len(r)) - 1


def score(r, crit):
    return cagr(r) if crit == "ret" else sharpe(r)


def main(slip=10, borrow=0.01, crit="sharpe"):
    R = pd.DataFrame({v: net_returns(v, slip, borrow) for v in VARIANTS}).dropna()
    spy = spy_returns(R.index)

    folds = []
    for ts in TEST_STARTS:
        t0 = pd.Timestamp(ts)
        t1 = t0 + pd.DateOffset(months=FOLD_MONTHS)
        te = R.loc[(R.index >= t0) & (R.index < t1)]
        if len(te) < 20:
            continue
        folds.append((t0, t1, te.index))

    print("=" * 118)
    print("PHASE 10 — NESTED WALK-FORWARD  (selection procedure under test)")
    print(f"  {len(VARIANTS)} variants | {len(folds)} non-overlapping {FOLD_MONTHS}-month test folds "
          f"| criterion = {crit} | NET of {slip}bps/leg, {borrow*100:.1f}% borrow")
    print("=" * 118)

    rows = {"A_expanding": [], "B_trailing12": [], "oracle": [],
            CANDIDATE: [], DEFAULT_CTRL: [], "SPY": []}
    picks = {"A_expanding": [], "B_trailing12": []}
    degrade = {"A_expanding": [], "B_trailing12": []}

    print(f"\n  {'test fold':<22} {'A: expanding':<28} {'B: trailing 12m':<28} "
          f"{'oracle':<16} {'cand':>7} {'SPY':>7}")
    print(f"  {'':<22} {'pick        sel->test':<28} {'pick        sel->test':<28} "
          f"{'pick      test':<16} {'test':>7} {'test':>7}")
    print("  " + "-" * 114)

    for t0, t1, tidx in folds:
        sel_all = R.loc[R.index < t0]
        sel_12 = R.loc[(R.index < t0) & (R.index >= t0 - pd.DateOffset(months=12))]
        line = f"  {t0.date()}→{t1.date()}   "

        for tag, sel in (("A_expanding", sel_all), ("B_trailing12", sel_12)):
            if len(sel) < MIN_SELECT_DAYS and tag == "A_expanding":
                pick, s_sel = None, np.nan
            else:
                scores = {v: score(sel[v], crit) for v in VARIANTS}
                pick = max(scores, key=lambda k: (scores[k] if scores[k] == scores[k] else -9e9))
                s_sel = scores[pick]
            if pick is None:
                line += f"{'(insufficient)':<28}"
                rows[tag].append(pd.Series(np.nan, index=tidx))
                continue
            s_test = score(R.loc[tidx, pick], crit)
            picks[tag].append(pick)
            degrade[tag].append((s_sel, s_test))
            rows[tag].append(R.loc[tidx, pick])
            line += f"{pick:<12}{s_sel:+6.2f}→{s_test:+6.2f}   "

        osc = {v: score(R.loc[tidx, v], crit) for v in VARIANTS}
        obest = max(osc, key=lambda k: (osc[k] if osc[k] == osc[k] else -9e9))
        rows["oracle"].append(R.loc[tidx, obest])
        rows[CANDIDATE].append(R.loc[tidx, CANDIDATE])
        rows[DEFAULT_CTRL].append(R.loc[tidx, DEFAULT_CTRL])
        rows["SPY"].append(spy.loc[tidx])
        line += f"{obest:<10}{osc[obest]:+5.2f}  {score(R.loc[tidx,CANDIDATE],crit):+7.2f} " \
                f"{score(spy.loc[tidx],crit):+7.2f}"
        print(line)

    # ── stitched out-of-sample paths ──
    print("\n" + "=" * 118)
    print("  STITCHED OUT-OF-SAMPLE RESULT  (all test folds concatenated, no day reused)")
    print("=" * 118)
    print(f"  {'strategy':<34} {'CAGR':>9} {'Sharpe':>8} {'MaxDD':>9} {'total':>9}   note")
    print("  " + "-" * 114)

    order = [("A_expanding", "selection procedure A (expanding)"),
             ("B_trailing12", "selection procedure B (trailing 12m)"),
             (CANDIDATE, f"always {CANDIDATE}  (= longonly_stopatr2)"),
             (DEFAULT_CTRL, f"always {DEFAULT_CTRL}  (frozen live control)"),
             ("SPY", "SPY buy & hold"),
             ("oracle", "ORACLE — hindsight best per fold")]
    stitched = {}
    for key, label in order:
        r = pd.concat(rows[key]).dropna()
        stitched[key] = r
        eq = (1 + r).cumprod()
        mdd = (eq / eq.cummax() - 1).min()
        note = "UNATTAINABLE upper bound" if key == "oracle" else ""
        print(f"  {label:<34} {cagr(r)*100:+8.2f}% {sharpe(r):+8.2f} {mdd*100:8.1f}% "
              f"{(eq.iloc[-1]-1)*100:+8.1f}%   {note}")

    # ── selection bias, measured ──
    print("\n" + "=" * 118)
    print("  SELECTION BIAS, MEASURED")
    print("=" * 118)
    for tag in ("A_expanding", "B_trailing12"):
        d = [x for x in degrade[tag] if x[0] == x[0] and x[1] == x[1]]
        if not d:
            continue
        sel = np.array([x[0] for x in d]); tst = np.array([x[1] for x in d])
        print(f"\n  {tag}")
        print(f"    mean {crit} on SELECTION window : {sel.mean():+.2f}")
        print(f"    mean {crit} on TEST window      : {tst.mean():+.2f}")
        gap = tst.mean() - sel.mean()
        verdict = ("test EXCEEDED selection -- see note on regime below"
                   if gap > 0 else
                   f"lost {round(100*(1-tst.mean()/sel.mean()))}% of apparent edge")
        print(f"    DEGRADATION                     : {gap:+.2f}   ({verdict})")
        print(f"    folds where test beat selection : {int((tst>sel).sum())}/{len(d)}")
        u = pd.Series(picks[tag]).value_counts()
        print(f"    variants chosen ({len(u)} distinct): "
              + ", ".join(f"{k}×{v}" for k, v in u.items()))
        chg = sum(1 for a, b in zip(picks[tag], picks[tag][1:]) if a != b)
        print(f"    selection changed               : {chg}/{len(picks[tag])-1} fold transitions")
        print(f"    times {CANDIDATE} was chosen      : {picks[tag].count(CANDIDATE)}/{len(picks[tag])}")

    # ── does the procedure beat doing nothing? ──
    print("\n" + "=" * 118)
    print("  IS THE SELECTION PROCEDURE WORTH ANYTHING?  (paired daily differences, stitched OOS)")
    print("=" * 118)
    base = stitched["SPY"]
    for tag in ("A_expanding", "B_trailing12", CANDIDATE, DEFAULT_CTRL):
        d = (stitched[tag] - base).dropna()
        t = d.mean() / d.std() * np.sqrt(len(d)) if d.std() > 0 else np.nan
        print(f"    {tag:<16} minus SPY : {d.mean()*TRADING_DAYS*100:+7.2f}%/yr   t={t:+5.2f}   "
              f"{'beats SPY' if d.mean()>0 else 'trails SPY'}")
    dA = (stitched["A_expanding"] - stitched[CANDIDATE]).dropna()
    tA = dA.mean() / dA.std() * np.sqrt(len(dA)) if dA.std() > 0 else np.nan
    print(f"\n    procedure A minus always-{CANDIDATE}: {dA.mean()*TRADING_DAYS*100:+7.2f}%/yr   t={tA:+5.2f}")
    print("    -> if this is <= 0, the selection procedure adds nothing over committing")
    print("       to one variant, and the variant's apparent superiority was hindsight.")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(float(a[a.index("--slip") + 1]) if "--slip" in a else 10,
         float(a[a.index("--borrow") + 1]) if "--borrow" in a else 0.01,
         a[a.index("--crit") + 1] if "--crit" in a else "sharpe")
