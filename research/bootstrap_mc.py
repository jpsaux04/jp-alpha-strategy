#!/usr/bin/env python3
"""PHASE 11 — BOOTSTRAP AND MONTE CARLO.

Three resampling schemes, because each answers a different question and each
has a different failure mode:

  A. POSITION BOOTSTRAP   iid resample of position-level returns.
                          Q: is mean position return distinguishable from 0?
                          Caveat: destroys serial dependence and overlap.

  B. BLOCK BOOTSTRAP      circular block resample of DAILY returns, block
                          length 21 (~1 month). Preserves autocorrelation and
                          volatility clustering, which iid resampling destroys
                          and which materially affects drawdown estimates.
                          This is the honest basis for path statistics.

  C. MONTE CARLO REORDER  shuffle the realised daily returns. Holds the return
                          DISTRIBUTION fixed and varies only ORDER, isolating
                          how much of the observed drawdown was path luck
                          versus distributional inevitability.

Run on NET returns (after Phase 6 costs) as well as gross: a distribution built
on gross returns answers a question nobody can trade.

Usage: python3 research/bootstrap_mc.py <prefix> [--slip 10] [--borrow 0.01]
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "research")
from cost_stress import (load, statutory_costs, slippage_cost, borrow_cost,
                         dividend_subsidy, net_equity_curve)

TRADING_DAYS = 252
N_SIMS = 10000
BLOCK = 21
RNG = np.random.default_rng(42)


def path_stats(r):
    """CAGR / Sharpe / MaxDD for a return path (2-D: sims x days)."""
    eq = np.cumprod(1.0 + r, axis=1)
    yrs = r.shape[1] / TRADING_DAYS
    final = eq[:, -1]
    cagr = np.where(final > 0, np.sign(final) * np.abs(final) ** (1 / yrs) - 1, -1.0)
    sd = r.std(axis=1, ddof=1)
    sharpe = np.divide(r.mean(axis=1), sd, out=np.zeros_like(sd), where=sd > 0) * np.sqrt(TRADING_DAYS)
    run_max = np.maximum.accumulate(eq, axis=1)
    mdd = (eq / run_max - 1).min(axis=1)
    return cagr, sharpe, mdd, final


def circular_block(r, n_sims, block, rng):
    n = len(r)
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(n_sims, nb))
    offs = np.arange(block)
    idx = (starts[:, :, None] + offs[None, None, :]).reshape(n_sims, -1) % n
    return r[idx[:, :n]]


def pct(x, q):
    return np.percentile(x, q)


def report_paths(name, r, label):
    cagr, sharpe, mdd, final = r
    print(f"\n  {name}")
    print(f"    CAGR      mean {cagr.mean()*100:+6.2f}%   "
          f"5th {pct(cagr,5)*100:+6.2f}%   50th {pct(cagr,50)*100:+6.2f}%   "
          f"95th {pct(cagr,95)*100:+6.2f}%")
    print(f"    Sharpe    mean {sharpe.mean():+6.2f}    "
          f"5th {pct(sharpe,5):+6.2f}    50th {pct(sharpe,50):+6.2f}    "
          f"95th {pct(sharpe,95):+6.2f}")
    print(f"    MaxDD     mean {mdd.mean()*100:6.1f}%   "
          f"median {pct(mdd,50)*100:6.1f}%   95th-worst {pct(mdd,5)*100:6.1f}%   "
          f"worst {mdd.min()*100:6.1f}%")
    print(f"    P(CAGR>0)          {100*(cagr>0).mean():5.1f}%")
    print(f"    P(Sharpe>0.5)      {100*(sharpe>0.5).mean():5.1f}%")
    print(f"    P(losing money)    {100*(final<1).mean():5.1f}%")
    print(f"    P(MaxDD worse -20%){100*(mdd<-0.20).mean():5.1f}%")
    print(f"    P(MaxDD worse -35%){100*(mdd<-0.35).mean():5.1f}%")
    print(f"    P(ruin, -50% DD)   {100*(mdd<-0.50).mean():5.1f}%")


def main(prefix, slip=10, borrow=0.01):
    f, eq = load(prefix)

    tot = (statutory_costs(f) + slippage_cost(f, slip)
           + borrow_cost(f, borrow) + dividend_subsidy(f))
    eq_net = net_equity_curve(eq, f, tot)

    print("=" * 100)
    print(f"PHASE 11 — BOOTSTRAP / MONTE CARLO : {prefix}")
    print(f"  {N_SIMS:,} sims, seed 42, block={BLOCK}d | net = {slip}bps slippage/leg, "
          f"{borrow*100:.1f}% borrow, dividends charged")
    print("=" * 100)

    for tag, e in (("GROSS", eq), ("NET  ", eq_net)):
        r = e.pct_change().dropna().values
        if (e <= 0).any():
            print(f"\n{tag}: capital exhausted under these assumptions — skipped")
            continue
        yrs = len(r) / TRADING_DAYS
        obs_cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
        obs_sh = r.mean() / r.std() * np.sqrt(TRADING_DAYS)
        obs_dd = (e / e.cummax() - 1).min()
        print(f"\n{'-'*100}\n{tag}  OBSERVED: CAGR {obs_cagr*100:+.2f}%  "
              f"Sharpe {obs_sh:.2f}  MaxDD {obs_dd*100:.1f}%  n={len(r)} days")

        # B. block bootstrap
        report_paths(f"B. BLOCK BOOTSTRAP (preserves serial dependence)",
                     path_stats(circular_block(r, N_SIMS, BLOCK, RNG)), tag)

        # C. reorder
        idx = np.argsort(RNG.random((N_SIMS, len(r))), axis=1)
        report_paths("C. MONTE CARLO REORDER (same returns, random order)",
                     path_stats(r[idx]), tag)

    # A. position bootstrap
    try:
        pos = pd.read_csv(f"{prefix}_positions.csv")
    except FileNotFoundError:
        print("\n(no positions file — skipping position bootstrap)")
        return
    print(f"\n{'-'*100}\nA. POSITION BOOTSTRAP  (n={len(pos)} positions, iid resample)")
    for lbl, d in (("ALL", pos), ("LONG", pos[pos.direction == "long"]),
                   ("SHORT", pos[pos.direction == "short"])):
        x = d["ret_pct"].dropna().values
        if len(x) < 30:
            continue
        s = x[RNG.integers(0, len(x), size=(N_SIMS, len(x)))]
        m = s.mean(axis=1)
        wr = (s > 0).mean(axis=1)
        print(f"  {lbl:<6} n={len(x):>4}  mean ret {m.mean():+.3f}% "
              f"CI [{pct(m,2.5):+.3f}, {pct(m,97.5):+.3f}]   "
              f"P(mean>0) {100*(m>0).mean():5.1f}%   "
              f"win rate {wr.mean()*100:.1f}% CI [{pct(wr,2.5)*100:.1f}, {pct(wr,97.5)*100:.1f}]")


if __name__ == "__main__":
    a = sys.argv[1:]
    p = a[0] if a and not a[0].startswith("--") else "f_base"
    slip = float(a[a.index("--slip") + 1]) if "--slip" in a else 10
    br = float(a[a.index("--borrow") + 1]) if "--borrow" in a else 0.01
    main(p, slip, br)
