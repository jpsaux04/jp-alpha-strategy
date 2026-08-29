#!/usr/bin/env python3
"""PHASE 4 — POINT-IN-TIME UNIVERSE / SURVIVORSHIP BIAS.

DATA-1 (docs/RESEARCH_REPORT.md PART 5 §4): the 42-name WATCHLIST is a fixed, present-day
list applied backwards to 2019. Every member survived to 2026 and was written
down by someone who knew that.

What this script can and cannot do
----------------------------------
It CANNOT reconstruct a true point-in-time universe: that needs historical
index-constituent data (CRSP / Compustat / index vendor) which this project
does not have and cannot obtain from yfinance. Claiming otherwise would be
worse than admitting the gap.

What it CAN do is bound the size of the problem three ways:

  1. UNIVERSE PREMIUM. Equal-weight buy-and-hold of the 42 names vs SPY over
     the same window. If the basket beat the index, that margin is a tailwind
     the strategy received for free from the list, before any signal fired.

  2. SYMBOL CONCENTRATION. Jackknife the realised P&L one symbol at a time.
     If dropping a handful of names destroys the result, the result is a
     statement about those names, not about the strategy.

  3. HINDSIGHT-WINNER DEPENDENCE. Remove the names whose 2019-2026 returns
     make them obvious retrospective picks (the ones nobody would reliably
     have listed in 2019) and re-measure.

Usage: python3 research/universe_bias.py [prefix]
"""
import sys
import numpy as np
import pandas as pd

TRADING_DAYS = 252
START, END = "2019-01-01", "2026-08-29"
SPY = "SPY"

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "INTC", "CSCO", "AMD", "QCOM", "MU", "AMAT",
    "GOOGL", "META", "NFLX", "AMZN", "HD", "NKE", "MCD", "SBUX",
    "JPM", "BAC", "GS", "WFC", "MS", "C",
    "UNH", "JNJ", "PFE", "ABBV", "LLY", "MRK",
    "CAT", "BA", "HON", "GE", "LMT", "XOM", "CVX", "COP",
    "WMT", "KO", "PG", "QQQ", "IWM",
]

RNG = np.random.default_rng(42)


def section(t):
    print("\n" + "=" * 96)
    print(t)
    print("=" * 96)


def get_prices():
    import yfinance as yf
    px = yf.download(WATCHLIST + [SPY], start=START, end=END,
                     progress=False, auto_adjust=True)["Close"]
    return px.dropna(how="all")


# ── 1. universe premium ──────────────────────────────────────────────────────
def universe_premium(px):
    section("1. UNIVERSE PREMIUM — what the LIST was worth, before any signal")
    r = px.pct_change().dropna(how="all")
    have = [s for s in WATCHLIST if s in r.columns]
    ew = r[have].mean(axis=1).dropna()
    sp = r[SPY].dropna()
    idx = ew.index.intersection(sp.index)
    ew, sp = ew.loc[idx], sp.loc[idx]

    def stats(x):
        tot = (1 + x).prod() - 1
        yrs = len(x) / TRADING_DAYS
        cagr = (1 + tot) ** (1 / yrs) - 1
        sh = x.mean() / x.std() * np.sqrt(TRADING_DAYS)
        eq = (1 + x).cumprod()
        mdd = (eq / eq.cummax() - 1).min()
        return tot, cagr, sh, mdd

    et, ec, es, em = stats(ew)
    st, sc, ss, sm = stats(sp)
    print(f"  n={len(idx)} days ({len(idx)/TRADING_DAYS:.1f} yrs), "
          f"{len(have)} names equal-weighted, daily rebalance")
    print(f"  {'':<28}{'total':>10}{'CAGR':>9}{'Sharpe':>9}{'MaxDD':>9}")
    print(f"  {'EW basket of the 42':<28}{et*100:>9.1f}%{ec*100:>8.2f}%{es:>9.2f}{em*100:>8.1f}%")
    print(f"  {'SPY':<28}{st*100:>9.1f}%{sc*100:>8.2f}%{ss:>9.2f}{sm*100:>8.1f}%")
    d = ew - sp
    t = d.mean() / d.std() * np.sqrt(len(d))
    print(f"  {'PREMIUM (EW - SPY)':<28}{(et-st)*100:>9.1f}%{(ec-sc)*100:>8.2f}%"
          f"{'':>9}{'':>9}   t={t:+.2f}")
    print(f"""
  Reading: the list itself out-returned the index by {(ec-sc)*100:+.2f}%/yr
  (t={t:+.2f}). A strategy that can only buy these names inherits that tilt
  whether or not its signal works. Any alpha estimate measured against SPY
  rather than against this basket is overstated by roughly that margin.""")
    return ew, sp


# ── 2. symbol concentration ──────────────────────────────────────────────────
def concentration(prefix):
    section("2. SYMBOL CONCENTRATION — jackknife of realised P&L")
    f = pd.read_csv(f"{prefix}_trades.csv")
    tot = f.gross_pnl.sum()
    by = f.groupby("symbol").gross_pnl.sum().sort_values()
    n = f.groupby("symbol").size()
    print(f"  {prefix}: total gross P&L ${tot:,.0f} across {f.symbol.nunique()} symbols\n")
    print(f"  {'WORST 6':<10}{'P&L':>12}{'n':>6}    {'BEST 6':<10}{'P&L':>12}{'n':>6}")
    lo, hi = by.head(6), by.tail(6)[::-1]
    for i in range(6):
        a, b = lo.index[i], hi.index[i]
        print(f"  {a:<10}{lo.iloc[i]:>12,.0f}{n[a]:>6}    "
              f"{b:<10}{hi.iloc[i]:>12,.0f}{n[b]:>6}")

    cum = by.sort_values(ascending=False).cumsum()
    pos_only = by[by > 0].sort_values(ascending=False)
    print(f"\n  profitable symbols: {len(pos_only)} of {len(by)}")
    for k in (1, 3, 5, 10):
        share = by.sort_values(ascending=False).head(k).sum() / tot * 100 if tot else np.nan
        print(f"    top {k:>2} symbols contribute {share:6.1f}% of total gross P&L")
    print(f"\n  JACKKNIFE — total P&L with each of the top 5 removed:")
    for s in by.sort_values(ascending=False).head(5).index:
        print(f"    without {s:<6} ${tot - by[s]:>12,.0f}   "
              f"({(tot-by[s])/tot*100 if tot else float('nan'):6.1f}% of actual)")

    # bootstrap over SYMBOLS, not trades: the unit of selection bias is the name
    syms = by.index.values
    pnl = by.values
    draws = RNG.integers(0, len(syms), size=(10000, len(syms)))
    sims = pnl[draws].sum(axis=1)
    print(f"\n  SYMBOL-LEVEL BOOTSTRAP (10,000 resamples of the {len(syms)} names)")
    print(f"    mean ${sims.mean():,.0f}   95% CI "
          f"[${np.percentile(sims,2.5):,.0f}, ${np.percentile(sims,97.5):,.0f}]")
    print(f"    P(total P&L > 0) = {100*(sims>0).mean():.1f}%")
    print("""
  This is the honest uncertainty attributable to WHICH NAMES were on the list.
  Resampling trades holds the universe fixed and so cannot see this at all.""")
    return by


# ── 3. hindsight winners ─────────────────────────────────────────────────────
def hindsight(px, by):
    section("3. HINDSIGHT-WINNER DEPENDENCE")
    r = px.pct_change().dropna(how="all")
    tot = {s: (1 + r[s].dropna()).prod() - 1 for s in WATCHLIST if s in r}
    ranked = sorted(tot.items(), key=lambda kv: -kv[1])
    print("  Buy-and-hold 2019-2026 by name (top 8 / bottom 8):")
    for s, v in ranked[:8]:
        print(f"    {s:<7}{v*100:>9.0f}%")
    print("    ...")
    for s, v in ranked[-8:]:
        print(f"    {s:<7}{v*100:>9.0f}%")

    winners = [s for s, v in ranked[:6]]
    strat_from_winners = by.reindex(winners).fillna(0).sum()
    print(f"\n  The six best buy-and-hold names are {', '.join(winners)}.")
    print(f"  Strategy gross P&L attributable to those six: ${strat_from_winners:,.0f} "
          f"({strat_from_winners/by.sum()*100:.1f}% of total)")
    print("""
  A 2019 author writing this list would plausibly have included AAPL, MSFT and
  the banks. NVDA at a 30x forward outcome, AMD, and LLY are exactly the names
  a 2026 author includes and a 2019 author does not. Their presence is the
  survivorship channel, and it is not removable by any amount of resampling of
  the trades themselves -- only by a genuine point-in-time universe.""")


# ── 4. the benchmark that actually controls for the universe ─────────────────
def alpha_vs_universe(prefix, ew, sp):
    """Phase 8 measured alpha against SPY. But the strategy cannot buy SPY --
    it can only buy these 42 names. The correct passive control is therefore
    the equal-weight basket of its own universe, which already contains the
    survivorship tilt. Regressing on BOTH separates market beta from the
    universe-selection premium."""
    sys.path.insert(0, "research")
    from factor_attribution import newey_west_ols, stars

    section("4. ALPHA AGAINST THE STRATEGY'S OWN UNIVERSE")
    print("""  Phase 8 regressed on SPY and found alpha +2.26%/yr (t=0.57, ns).
  But SPY is not an instrument this strategy could have chosen instead --
  it is restricted to the 42 names. The honest passive control is an
  equal-weight buy-and-hold of that same list.""")

    e = pd.read_csv(f"{prefix}_equity.csv",
                    parse_dates=["date"]).set_index("date")["pv"]
    rs = e.pct_change().dropna()
    idx = rs.index.intersection(ew.index).intersection(sp.index)
    y = rs.loc[idx].values
    A, B = ew.loc[idx].values, sp.loc[idx].values

    for name, cols, X in (
        ("vs SPY            ", ["SPY"], np.column_stack([np.ones(len(idx)), B])),
        ("vs EW UNIVERSE    ", ["EWUNI"], np.column_stack([np.ones(len(idx)), A])),
        ("vs BOTH           ", ["SPY", "EWUNI"],
         np.column_stack([np.ones(len(idx)), B, A])),
    ):
        r = newey_west_ols(y, X)
        b, se, t = r["beta"], r["se"], r["t"]
        a, ase = b[0] * TRADING_DAYS * 100, se[0] * TRADING_DAYS * 100
        print(f"\n  {name} n={r['n']}  (Newey-West, 5 lags)")
        print(f"    alpha  {a:+7.2f}%/yr  t={t[0]:+5.2f}{stars(t[0]):<3} "
              f"95% CI [{a-1.96*ase:+.2f}%, {a+1.96*ase:+.2f}%]")
        for i, c in enumerate(cols, 1):
            print(f"    {c:<7}{b[i]:+7.3f}      t={t[i]:+5.2f}{stars(t[i])}")
        print(f"    R2={r['r2']:.3f}")

    print("""
  If alpha falls when the benchmark changes from SPY to the strategy's own
  universe, the difference is the part of the "edge" that was really just the
  survivorship-selected list.""")


def main(prefix="s2_full_fix"):
    section("PHASE 4 — POINT-IN-TIME UNIVERSE AND SURVIVORSHIP BIAS")
    print(f"  universe: {len(WATCHLIST)} fixed present-day names, window {START} -> {END}")
    print("  NOTE: a true PIT universe cannot be reconstructed from the available")
    print("        data. This bounds the bias; it does not remove it.")
    px = get_prices()
    ew, sp = universe_premium(px)
    by = concentration(prefix)
    hindsight(px, by)
    alpha_vs_universe(prefix, ew, sp)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
