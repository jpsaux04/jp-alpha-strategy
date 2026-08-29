#!/usr/bin/env python3
"""PHASE 6 — REALISTIC COST MODEL AND STRESS TESTING.

Decomposes trading costs into separately-stressed components rather than one
blended haircut, then re-derives net performance across a grid.

Components
----------
  commission      Alpaca equities = $0. Kept explicit so it is not forgotten
                  if the venue ever changes.
  SEC Section 31  27.8 / $1,000,000 of principal, SELLS ONLY (long exits and
                  short entries). Statutory.
  FINRA TAF       $0.000166 / share on sells, capped $8.30 per trade. Statutory.
  slippage        Spread + market impact + implementation shortfall, applied as
                  a symmetric adverse bps haircut on BOTH legs. Stressed.
  short borrow    annual_rate * notional * days / 360, shorts only. Stressed.
  short dividends Dividends owed to the lender. See note below.

Note on dividends: backtest.py uses auto_adjust prices, so dividends are folded
into the price series. Long positions silently receive them; short positions
are silently NOT charged them (RESEARCH_AUDIT.md 1.3). The dividend column
below estimates that missing charge using the universe's realised dividend
yield. It is a SUBSIDY REMOVAL, not a new cost.

Usage: python3 research/cost_stress.py <prefix> [<prefix> ...]
"""
import sys
import numpy as np
import pandas as pd

SEC_FEE_RATE = 27.8e-6          # per $ of principal, sells only
TAF_PER_SHARE = 0.000166
TAF_CAP = 8.30
COMMISSION = 0.0                # Alpaca equities
TRADING_DAYS = 252

SLIPPAGE_GRID = [0, 5, 10, 20, 50, 100]                 # bps per leg
BORROW_GRID = [0.005, 0.01, 0.02, 0.05, 0.10, 0.25]     # annualised


def load(prefix):
    f = pd.read_csv(f"{prefix}_trades.csv", parse_dates=["entry_date", "exit_date"])
    e = pd.read_csv(f"{prefix}_equity.csv", parse_dates=["date"]).set_index("date")["pv"]
    return f, e


def statutory_costs(f):
    """SEC 31 + FINRA TAF. Sells only: a long EXIT and a short ENTRY are sells."""
    entry_notional = f.entry_px * f.qty
    exit_notional = f.exit_px * f.qty
    is_long = f.direction == "long"

    # long: sell on exit. short: sell on entry.
    sell_notional = np.where(is_long, exit_notional, entry_notional)
    sec = sell_notional * SEC_FEE_RATE
    taf = np.minimum(f.qty * TAF_PER_SHARE, TAF_CAP)
    comm = COMMISSION * 2
    return pd.Series(sec + taf + comm, index=f.index)


def slippage_cost(f, bps):
    """Adverse fill on both legs, proportional to notional traded."""
    return (f.entry_px * f.qty + f.exit_px * f.qty) * (bps / 1e4)


def borrow_cost(f, annual_rate):
    """Shorts only, act/360 on entry notional."""
    days = f.hold_days.clip(lower=0)
    notional = f.entry_px * f.qty
    return np.where(f.direction == "short", notional * annual_rate * days / 360.0, 0.0)


def dividend_subsidy(f, yield_annual=0.0155):
    """Dividends a short owes the lender. Removes an accounting subsidy.

    0.0155 ~ realised trailing dividend yield of a US mega-cap basket over
    2019-2026. Applied pro-rata over the holding period, shorts only.
    """
    days = f.hold_days.clip(lower=0)
    notional = f.entry_px * f.qty
    return np.where(f.direction == "short", notional * yield_annual * days / 365.0, 0.0)


def net_equity_curve(equity, f, total_cost_per_frag):
    """Subtract each fragment's cost from equity on its exit date, cumulatively."""
    c = pd.Series(total_cost_per_frag.values, index=f.exit_date.values)
    daily = c.groupby(level=0).sum()
    cum = daily.reindex(equity.index, fill_value=0.0).fillna(0.0).cumsum()
    return equity - cum


def perf(eq):
    r = eq.pct_change().dropna()
    if len(r) < 30 or eq.iloc[0] <= 0:
        return dict(ret=np.nan, cagr=np.nan, sharpe=np.nan, mdd=np.nan)
    yrs = len(r) / TRADING_DAYS
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else np.nan
    sharpe = r.mean() / r.std() * np.sqrt(TRADING_DAYS) if r.std() > 0 else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    return dict(ret=total * 100, cagr=cagr * 100, sharpe=sharpe, mdd=mdd * 100)


def report(prefix):
    f, eq = load(prefix)
    n_short = (f.direction == "short").sum()
    stat = statutory_costs(f)
    gross = f.gross_pnl.sum()

    print("\n" + "=" * 104)
    print(f"PHASE 6 — COST STRESS : {prefix}")
    print("=" * 104)
    print(f"  fragments {len(f)}  (long {len(f)-n_short} / short {n_short})   "
          f"gross P&L ${gross:,.0f}")
    print(f"  statutory (SEC 31 + FINRA TAF, sells only): ${stat.sum():,.0f} "
          f"= {stat.sum()/abs(gross)*100:.1f}% of |gross P&L|")
    turnover = (f.entry_px * f.qty).sum() + (f.exit_px * f.qty).sum()
    print(f"  total two-way notional traded: ${turnover:,.0f} "
          f"({turnover/eq.iloc[0]:.1f}x initial capital)")

    base = perf(eq)
    print(f"\n  GROSS (no costs)   return {base['ret']:+.1f}%   CAGR {base['cagr']:+.2f}%   "
          f"Sharpe {base['sharpe']:.2f}   MaxDD {base['mdd']:.1f}%")

    # ── slippage sensitivity (borrow held at 0.5%) ──
    print(f"\n  SLIPPAGE SENSITIVITY  (borrow fixed 0.5%, dividends charged on shorts)")
    print(f"  {'slip bps':>9} {'net P&L':>13} {'return':>9} {'CAGR':>8} {'Sharpe':>8} {'MaxDD':>8}")
    print("  " + "-" * 62)
    for bps in SLIPPAGE_GRID:
        tot = stat + slippage_cost(f, bps) + borrow_cost(f, 0.005) + dividend_subsidy(f)
        p = perf(net_equity_curve(eq, f, tot))
        print(f"  {bps:>9} {gross-tot.sum():>13,.0f} {p['ret']:>8.1f}% "
              f"{p['cagr']:>7.2f}% {p['sharpe']:>8.2f} {p['mdd']:>7.1f}%")

    # ── borrow sensitivity (slippage held at 10bps) ──
    if n_short:
        print(f"\n  SHORT BORROW SENSITIVITY  (slippage fixed 10bps)")
        print(f"  {'borrow':>9} {'net P&L':>13} {'return':>9} {'CAGR':>8} {'Sharpe':>8} {'MaxDD':>8}")
        print("  " + "-" * 62)
        for br in BORROW_GRID:
            tot = stat + slippage_cost(f, 10) + borrow_cost(f, br) + dividend_subsidy(f)
            p = perf(net_equity_curve(eq, f, tot))
            print(f"  {br*100:>8.1f}% {gross-tot.sum():>13,.0f} {p['ret']:>8.1f}% "
                  f"{p['cagr']:>7.2f}% {p['sharpe']:>8.2f} {p['mdd']:>7.1f}%")
    else:
        print("\n  (long-only: borrow and dividend-subsidy terms are zero)")

    # ── joint grid ──
    print(f"\n  JOINT GRID — net CAGR %   (rows = slippage bps, cols = borrow)")
    hdr = "  " + " " * 9 + "".join(f"{b*100:>9.1f}%" for b in BORROW_GRID)
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for bps in SLIPPAGE_GRID:
        row = f"  {bps:>8}b "
        for br in BORROW_GRID:
            tot = stat + slippage_cost(f, bps) + borrow_cost(f, br) + dividend_subsidy(f)
            row += f"{perf(net_equity_curve(eq, f, tot))['cagr']:>9.2f}"
        print(row)

    # ── decomposition at a central, defensible assumption ──
    print(f"\n  DECOMPOSITION at 10bps slippage / 1.0% borrow:")
    parts = {
        "statutory (SEC+TAF)": stat.sum(),
        "slippage/impact": slippage_cost(f, 10).sum(),
        "short borrow": borrow_cost(f, 0.01).sum(),
        "short dividends owed": dividend_subsidy(f).sum(),
    }
    tot = sum(parts.values())
    for k, v in parts.items():
        print(f"    {k:<24} ${v:>12,.0f}   {v/tot*100:>5.1f}% of costs")
    print(f"    {'TOTAL':<24} ${tot:>12,.0f}")
    print(f"    gross P&L ${gross:,.0f}  ->  NET P&L ${gross-tot:,.0f}")


if __name__ == "__main__":
    for p in (sys.argv[1:] or ["f_base"]):
        report(p)
