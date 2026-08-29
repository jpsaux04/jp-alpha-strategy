#!/usr/bin/env python3
"""PHASE 14 — CAPACITY.

At $100k this strategy is a rounding error in every name it trades. The
question capacity analysis answers is: at what AUM does the backtest stop
describing an achievable result?

Two binding constraints, measured separately:

  A. PARTICIPATION. Position notional as a fraction of 20-day median dollar
     volume (ADV). The conventional ceiling for a single day's execution
     without material impact is 1-3% of ADV; above ~10% a market order is
     not a realistic fill at the modelled price. The strategy trades MARKET
     ON OPEN, i.e. it takes liquidity in the single most concentrated
     auction of the day, so it should be held to the tighter end.

  B. IMPACT COST. Impact grows roughly with the SQUARE ROOT of participation
     (Almgren-Chriss / Kyle). Phase 6 measured that each 1bp of slippage costs
     ~0.13%/yr of CAGR at this turnover. Combining the two gives the AUM at
     which impact alone consumes the entire estimated alpha.

The alpha being defended is the Phase 8 estimate of +2.26%/yr, which is itself
not statistically distinguishable from zero. Capacity is therefore the AUM at
which a quantity we cannot prove exists is definitely gone.

Usage: python3 research/capacity.py [prefix]
"""
import sys
import numpy as np
import pandas as pd

START, END = "2019-01-01", "2026-08-29"
AUM_GRID = [1e5, 1e6, 5e6, 2.5e7, 1e8, 5e8]
BASE_EQUITY = 100_000.0
CAGR_PER_BP = 0.13          # Phase 6: %/yr of CAGR lost per 1bp/leg slippage
ALPHA_EST = 2.26            # Phase 8: %/yr, t=0.57 (not significant)
IMPACT_COEF = 10.0          # bps at 100% participation; sqrt law below


def section(t):
    print("\n" + "=" * 96)
    print(t)
    print("=" * 96)


def adv_table(symbols):
    """20-day median DOLLAR volume per symbol over the window."""
    import yfinance as yf
    d = yf.download(symbols, start=START, end=END, progress=False,
                    auto_adjust=True)
    close, vol = d["Close"], d["Volume"]
    dv = (close * vol).rolling(20).median()
    return dv


def main(prefix="s2_full_fix"):
    section("PHASE 14 — CAPACITY")
    f = pd.read_csv(f"{prefix}_trades.csv", parse_dates=["entry_date"])
    f["notional"] = f.entry_px * f.shares_total
    syms = sorted(f.symbol.unique())
    print(f"  {prefix}: {len(f)} fragments, {f.pos_id.nunique()} positions, "
          f"{len(syms)} symbols")
    print(f"  backtest equity base ${BASE_EQUITY:,.0f}")

    dv = adv_table(syms)

    # participation per ENTRY at base equity
    part = []
    for _, t in f.drop_duplicates("pos_id").iterrows():
        s = t.symbol
        if s not in dv.columns:
            continue
        d = dv[s].asof(t.entry_date)
        if d and d == d and d > 0:
            part.append((s, t.entry_date, t.notional, t.notional / d))
    P = pd.DataFrame(part, columns=["sym", "date", "notional", "frac"])

    section("A. PARTICIPATION — position notional as % of 20-day median ADV")
    print(f"\n  At the backtest's own ${BASE_EQUITY:,.0f}:")
    print(f"    median position ${P.notional.median():,.0f}   "
          f"p95 ${P.notional.quantile(.95):,.0f}")
    print(f"    median participation {P.frac.median()*100:.4f}%   "
          f"p95 {P.frac.quantile(.95)*100:.4f}%   max {P.frac.max()*100:.4f}%")

    print(f"\n  Scaling linearly with AUM. Reported figure is the p95 position,")
    print(f"  because capacity binds on the WORST fill, not the average one.\n")
    print(f"  {'AUM':>12}{'med pos':>12}{'p95 pos':>13}{'med part':>11}"
          f"{'p95 part':>11}{'max part':>11}   verdict")
    print("  " + "-" * 92)
    for aum in AUM_GRID:
        k = aum / BASE_EQUITY
        mp, pp = P.frac.median() * k, P.frac.quantile(.95) * k
        mx = P.frac.max() * k
        v = ("fine" if pp < 0.01 else
             "watch" if pp < 0.03 else
             "impaired" if pp < 0.10 else "NOT EXECUTABLE")
        print(f"  {aum:>12,.0f}{P.notional.median()*k:>12,.0f}"
              f"{P.notional.quantile(.95)*k:>13,.0f}{mp*100:>10.2f}%"
              f"{pp*100:>10.2f}%{mx*100:>10.2f}%   {v}")

    section("B. IMPACT COST — square-root law, and what it eats")
    print(f"  impact_bps = {IMPACT_COEF:.0f} * sqrt(participation)")
    print(f"  CAGR drag  = impact_bps * {CAGR_PER_BP} %/yr   (Phase 6 measurement)")
    print(f"  alpha being defended = {ALPHA_EST:+.2f}%/yr (Phase 8, t=0.57, ns)\n")
    print(f"  {'AUM':>12}{'p95 part':>11}{'impact bps':>12}"
          f"{'CAGR drag':>12}{'alpha left':>12}")
    print("  " + "-" * 61)
    for aum in AUM_GRID:
        k = aum / BASE_EQUITY
        pp = P.frac.quantile(.95) * k
        bps = IMPACT_COEF * np.sqrt(min(pp, 1.0))
        drag = bps * CAGR_PER_BP
        left = ALPHA_EST - drag
        print(f"  {aum:>12,.0f}{pp*100:>10.2f}%{bps:>12.2f}"
              f"{drag:>11.2f}%{left:>11.2f}%")

    section("C. WHERE IT BINDS FIRST — least liquid names")
    g = P.groupby("sym").agg(n=("frac", "size"), med=("frac", "median"),
                             p95=("frac", lambda x: x.quantile(.95)))
    g = g.sort_values("p95", ascending=False).head(8)
    print(f"\n  {'symbol':<8}{'n pos':>7}{'med part':>11}{'p95 part':>11}"
          f"{'AUM at 3% p95':>16}")
    print("  " + "-" * 53)
    for s, r in g.iterrows():
        cap = BASE_EQUITY * (0.03 / r.p95) if r.p95 > 0 else np.inf
        print(f"  {s:<8}{int(r.n):>7}{r.med*100:>10.4f}%{r.p95*100:>10.4f}%"
              f"{cap:>16,.0f}")

    print("""
  Caveats that cut AGAINST the numbers above:
   * ADV is measured on the realised 2019-2026 history of names that all
     survived. A point-in-time universe would contain less liquid names.
   * MARKET-ON-OPEN concentrates the whole order into the opening auction.
     Participation against FULL-DAY ADV therefore understates the true
     participation in the auction the order actually hits, by roughly the
     ratio of daily to opening-auction volume (commonly 5-10x).
   * The sqrt impact coefficient is a convention, not a calibration. No
     execution data exists for this system to fit one.
  The honest reading is that the participation column should be multiplied by
  several-fold before being compared to the 1-3% rule of thumb.""")


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
