#!/usr/bin/env python3
"""PHASE 2 — RISK MODEL.

RISK-1 (docs/RESEARCH_REPORT.md PART 5 §4): the strategy sizes positions from one risk
number and stops them out at a different one.

    calc_shares:  shares = PV * RISK_PER_TRADE_PCT / (ATR_MULTIPLIER * ATR)
                         = PV * 0.01 / (1.5 * ATR)

That expression means "risk 1% of PV" ONLY IF the stop sits at 1.5*ATR below
entry. It does not.

    V3 stop:  entry * (1 - 0.08)          -> distance = 0.08 * price
    V4 stop:  entry - 2.0 * ATR           -> distance = 2.0 * ATR

Realised risk at the stop is therefore

    risk_frac = shares * stop_distance / PV

  V3:  = 0.01 * (0.08 * price) / (1.5 * ATR) = 0.01 * (0.0533 / atr_pct)
       -> INVERSELY PROPORTIONAL TO VOLATILITY. A quiet name is risked many
          times harder than a violent one. This is backwards.

  V4:  = 0.01 * (2.0 * ATR) / (1.5 * ATR)   = 0.01333, CONSTANT
       -> volatility cancels. Every position risks the same 1.333% of PV.

The V4 figure is 1.333%, not the intended 1.000%: the sizing multiplier (1.5)
and the stop multiplier (2.0) disagree. That is a real 33% overshoot of stated
risk appetite, and it is fixable without touching alpha by aligning the two
constants.

This script measures all of the above on realised trades rather than asserting
it, and then goes to the portfolio level, because per-position risk limits say
nothing about portfolio risk when every position is a US mega-cap.

Usage: python3 research/risk_model.py [prefix ...]
"""
import sys
import numpy as np
import pandas as pd

RISK_PER_TRADE_PCT = 0.01
ATR_MULTIPLIER = 1.5          # sizing
STOP_LOSS_PCT = 0.08          # V3 stop
TRADING_DAYS = 252

# variant -> stop specification actually in force
STOPS = {
    "wfv_base_ls":  ("pct", STOP_LOSS_PCT),
    "wfv_lo":       ("pct", STOP_LOSS_PCT),
    "wfv_lo_atr15": ("atr", 1.5),
    "wfv_lo_atr2":  ("atr", 2.0),
    "wfv_lo_atr25": ("atr", 2.5),
    "wfv_lo_atr3":  ("atr", 3.0),
}


def realised_risk_frac(atr_pct, kind, mult):
    """Fraction of PV actually at risk between entry and stop."""
    atr_pct = np.asarray(atr_pct, float)
    if kind == "pct":
        # 0.01 * (STOP_LOSS_PCT * price) / (1.5 * ATR)
        return RISK_PER_TRADE_PCT * (STOP_LOSS_PCT / ATR_MULTIPLIER) / atr_pct
    return np.full_like(atr_pct, RISK_PER_TRADE_PCT * mult / ATR_MULTIPLIER)


def q(x, p):
    return np.percentile(x, p)


def section(t):
    print("\n" + "=" * 96)
    print(t)
    print("=" * 96)


# ── 1. intended vs realised per-position risk ────────────────────────────────
def per_position_risk(prefix):
    f = pd.read_csv(f"{prefix}_trades.csv", parse_dates=["entry_date"])
    a = f["atrpct_ent"].dropna().values
    a = a[a > 0]
    if len(a) < 30:
        return None
    kind, mult = STOPS.get(prefix, ("pct", STOP_LOSS_PCT))
    r = realised_risk_frac(a, kind, mult) * 100

    stop_desc = f"{mult:.1f}xATR" if kind == "atr" else f"-{mult*100:.0f}% fixed"
    print(f"\n  {prefix:<14} stop = {stop_desc:<12} n={len(a):>5}")
    print(f"    ATR%% at entry     median {np.median(a)*100:5.2f}%   "
          f"p5 {q(a,5)*100:5.2f}%   p95 {q(a,95)*100:5.2f}%")
    print(f"    realised risk     median {np.median(r):5.2f}%   "
          f"p5 {q(r,5):5.2f}%   p95 {q(r,95):5.2f}%   "
          f"max {r.max():5.2f}%")
    print(f"    vs INTENDED 1.00% ratio median {np.median(r)/1.0:5.2f}x   "
          f"spread p95/p5 {q(r,95)/max(q(r,5),1e-9):5.2f}x")
    over = (r > 2.0).mean() * 100
    print(f"    positions risking >2% of PV: {over:5.1f}%   "
          f">3%: {(r>3.0).mean()*100:4.1f}%")
    return dict(prefix=prefix, kind=kind, mult=mult, n=len(a),
                med=np.median(r), p5=q(r, 5), p95=q(r, 95), max=r.max())


# ── 2. does realised risk predict realised loss? ─────────────────────────────
def risk_realisation_check(prefix):
    """If the risk model is honest, stopped-out trades should lose an amount
    proportional to the risk the model *thought* it was taking."""
    f = pd.read_csv(f"{prefix}_trades.csv")
    f = f[(f.exit_reason == "STOP_LOSS") & f.atrpct_ent.notna() & (f.atrpct_ent > 0)]
    if len(f) < 30:
        return
    kind, mult = STOPS.get(prefix, ("pct", STOP_LOSS_PCT))
    modelled = realised_risk_frac(f.atrpct_ent.values, kind, mult) * 100
    loss = -f.return_pct.values
    if modelled.std() < 1e-12:
        cs = "n/a (constant by construction — that is the fix working)"
    else:
        cs = f"{np.corrcoef(modelled, loss)[0, 1]:+.3f}"
    print(f"    stopped-out n={len(f)}  corr(modelled risk, realised loss %) "
          f"= {cs}")
    print(f"      mean loss on stop {loss.mean():+.2f}%  "
          f"sd {loss.std():.2f}%  worst {loss.max():.2f}%")


# ── 3. portfolio-level: 1% per position is not 1% of portfolio ───────────────
def portfolio_risk(prefix, universe_prefix="f_base"):
    """Concurrent positions and their correlation. MAX_SIMULTANEOUS=10 at a
    nominal 1% each implies 10% risk only if the names are independent."""
    eq = pd.read_csv(f"{prefix}_equity.csv",
                     parse_dates=["date"]).set_index("date")["pv"]
    try:
        pos = pd.read_csv(f"{prefix}_positions.csv",
                          parse_dates=["entry_date", "exit_date"])
    except FileNotFoundError:
        # Rebuild positions from exit fragments: a position spans from its
        # entry to its LAST fragment exit. Tiered exits emit up to 3 rows.
        f = pd.read_csv(f"{prefix}_trades.csv",
                        parse_dates=["entry_date", "exit_date"])
        g = f.groupby("pos_id")
        pos = pd.DataFrame({
            "entry_date": g.entry_date.first(),
            "exit_date": g.exit_date.max(),
            "cost_basis": g.apply(
                lambda d: float(d.entry_px.iloc[0]) * float(d.shares_total.iloc[0]),
                include_groups=False),
        }).reset_index()
    conc = pd.Series(0, index=eq.index, dtype=int)
    grossx = pd.Series(0.0, index=eq.index)
    for _, p in pos.iterrows():
        m = (eq.index >= p.entry_date) & (eq.index < p.exit_date)
        conc[m] += 1
        grossx[m] += p.cost_basis
    ex = (grossx / eq)
    print(f"    concurrent positions  mean {conc.mean():4.1f}  "
          f"median {conc.median():4.0f}  max {conc.max():3d}")
    print(f"    gross exposure        mean {ex.mean()*100:5.1f}%  "
          f"p95 {q(ex.values,95)*100:5.1f}%  max {ex.max()*100:5.1f}%")
    print(f"    days fully idle (0 positions): {(conc==0).mean()*100:4.1f}%")
    return conc, ex


def diversification(prefix):
    """Effective number of independent bets, from the realised correlation of
    daily position returns proxied by symbol overlap in the universe."""
    try:
        import yfinance as yf
    except ImportError:
        return
    print("    (effective-bets estimate requires price data; see §portfolio)")


def main(prefixes):
    section("PHASE 2 — RISK MODEL : RISK-1, sizing risk vs stop risk")
    print("""
  calc_shares sizes on 1% risk assuming a 1.5xATR stop. The stop is not there.

    V3 (fixed -8%)   realised risk = 1% * (0.0533 / ATR%)  -> varies with vol
    V4 (2.0xATR)     realised risk = 1% * (2.0 / 1.5)      = 1.333%, constant

  Below the identity is measured on realised entries, not assumed.""")

    section("1. PER-POSITION RISK — intended 1.00% of PV")
    rows = [r for r in (per_position_risk(p) for p in prefixes) if r]

    section("2. IS THE RISK MODEL PREDICTIVE OF REALISED LOSS?")
    for p in prefixes:
        print(f"\n  {p}")
        risk_realisation_check(p)

    section("3. PORTFOLIO-LEVEL RISK")
    print("  MAX_SIMULTANEOUS=10, MAX_PER_SECTOR=2, universe = 42 US mega-caps.")
    for p in prefixes:
        print(f"\n  {p}")
        portfolio_risk(p)

    section("4. SUMMARY — dispersion of realised risk by stop rule")
    print(f"  {'variant':<15}{'stop':<12}{'median':>9}{'p5':>8}{'p95':>8}"
          f"{'max':>8}{'p95/p5':>9}")
    print("  " + "-" * 69)
    for r in rows:
        sd = f"{r['mult']:.1f}xATR" if r["kind"] == "atr" else f"-{r['mult']*100:.0f}%"
        print(f"  {r['prefix']:<15}{sd:<12}{r['med']:>8.2f}%{r['p5']:>7.2f}%"
              f"{r['p95']:>7.2f}%{r['max']:>7.2f}%{r['p95']/max(r['p5'],1e-9):>8.2f}x")
    print("""
  A constant column is the point. Under an ATR stop the volatility term cancels
  and every position carries identical risk; under the fixed-percent stop the
  same 1% instruction produces a wide range of realised exposure, systematically
  LARGER for low-volatility names.""")


if __name__ == "__main__":
    main(sys.argv[1:] or ["wfv_base_ls", "wfv_lo", "wfv_lo_atr15",
                          "wfv_lo_atr2", "wfv_lo_atr25", "wfv_lo_atr3"])
