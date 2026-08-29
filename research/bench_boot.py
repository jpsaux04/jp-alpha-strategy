#!/usr/bin/env python3
"""PHASE 11b — BENCHMARK-RELATIVE BOOTSTRAP.

P(CAGR>0) is not evidence of edge: a 0.55-beta book in a bull market clears
that bar mechanically. The question Phase 8 poses is whether the strategy beats
the passive alternative. So we bootstrap:

  1. SPY itself, under the identical circular-block scheme  -> the control
  2. the DIFFERENCE r_strat - r_spy, block-resampled PAIRED (same block indices
     applied to both series, preserving their contemporaneous correlation)
  3. the difference vs a BETA-MATCHED passive book: beta*SPY + (1-beta)*cash,
     which is the actual replicating portfolio, not full SPY

(3) is the fair comparison. (2) is included because it is the comparison a
retail investor actually faces: "should I just buy the index instead?"
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "research")
from cost_stress import (load, statutory_costs, slippage_cost, borrow_cost,
                         dividend_subsidy, net_equity_curve)

TRADING_DAYS, N_SIMS, BLOCK = 252, 10000, 21
RNG = np.random.default_rng(42)


def block_idx(n, n_sims, block, rng):
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(n_sims, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_sims, -1) % n
    return idx[:, :n]


def cagr(r):
    return np.cumprod(1.0 + r, axis=1)[:, -1] ** (TRADING_DAYS / r.shape[1]) - 1


def sharpe(r):
    sd = r.std(axis=1, ddof=1)
    return np.divide(r.mean(axis=1), sd, out=np.zeros_like(sd), where=sd > 0) * np.sqrt(TRADING_DAYS)


def spy_returns(index):
    import yfinance as yf
    px = yf.download("SPY", start=index.min().strftime("%Y-%m-%d"),
                     end=(index.max() + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
                     progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.DataFrame):
        px = px.iloc[:, 0]
    return px.pct_change().reindex(index).fillna(0.0)


def line(tag, x):
    lo, hi = np.percentile(x, 2.5), np.percentile(x, 97.5)
    print(f"    {tag:<34} mean {x.mean()*100:+7.2f}%   95% CI [{lo*100:+7.2f}%, {hi*100:+7.2f}%]"
          f"   P(>0) {100*(x>0).mean():5.1f}%")


def main(prefix, slip=10, borrow=0.01):
    f, eq = load(prefix)
    tot = statutory_costs(f) + slippage_cost(f, slip) + borrow_cost(f, borrow) + dividend_subsidy(f)
    eq_net = net_equity_curve(eq, f, tot)

    rs = eq_net.pct_change().dropna()
    rb = spy_returns(rs.index).values
    rs = rs.values
    n = len(rs)

    beta = np.cov(rs, rb)[0, 1] / np.var(rb)
    rep = beta * rb                      # beta-matched passive book, cash at 0%

    idx = block_idx(n, N_SIMS, BLOCK, RNG)   # SAME indices for both -> paired
    S, B, R = rs[idx], rb[idx], rep[idx]

    print("=" * 100)
    print(f"PHASE 11b — BENCHMARK-RELATIVE BOOTSTRAP : {prefix}  (NET of {slip}bps/{borrow*100:.1f}%)")
    print(f"  {N_SIMS:,} paired circular-block sims, block={BLOCK}d, seed 42, n={n} days")
    print(f"  estimated beta vs SPY = {beta:.3f}  -> replicating book = {beta:.3f}x SPY + cash")
    print("=" * 100)

    print(f"\n  ABSOLUTE CAGR")
    line("strategy (net)", cagr(S))
    line("SPY buy & hold", cagr(B))
    line(f"beta-matched passive ({beta:.2f}x SPY)", cagr(R))

    print(f"\n  PAIRED DIFFERENCES  (the actual test)")
    line("strategy - SPY", cagr(S) - cagr(B))
    line("strategy - beta-matched passive", cagr(S) - cagr(R))

    print(f"\n  SHARPE")
    for tag, x in (("strategy (net)", sharpe(S)), ("SPY", sharpe(B)),
                   ("beta-matched passive", sharpe(R))):
        lo, hi = np.percentile(x, 2.5), np.percentile(x, 97.5)
        print(f"    {tag:<34} mean {x.mean():+7.2f}    95% CI [{lo:+7.2f}, {hi:+7.2f}]")
    d = sharpe(S) - sharpe(B)
    lo, hi = np.percentile(d, 2.5), np.percentile(d, 97.5)
    print(f"    {'strategy - SPY (paired)':<34} mean {d.mean():+7.2f}    95% CI [{lo:+7.2f}, {hi:+7.2f}]"
          f"   P(>0) {100*(d>0).mean():.1f}%")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a and not a[0].startswith("--") else "f_base",
         float(a[a.index("--slip") + 1]) if "--slip" in a else 10,
         float(a[a.index("--borrow") + 1]) if "--borrow" in a else 0.01)
