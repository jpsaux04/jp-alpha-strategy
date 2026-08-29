#!/usr/bin/env python3
"""PHASES 9, 13, 15 — REGIME ANALYSIS, EVENT RISK, RISK METRICS.

Phase 9  regime      Does the edge survive outside the regime that produced it?
                     The sample is one long bull market; the only honest way to
                     ask is to cut it and look at the pieces.
Phase 13 event risk  Named stress windows and gap/tail behaviour. Averages hide
                     the events that actually end strategies.
Phase 15 risk metrics The full downside battery, not just Sharpe. Sharpe
                     punishes upside volatility and is blind to path.

All three run on the same daily equity curves, so they share one loader.

Usage: python3 research/regime_risk.py [prefix ...]
"""
import sys
import numpy as np
import pandas as pd

TRADING_DAYS = 252
START, END = "2019-01-01", "2026-08-29"
RNG = np.random.default_rng(42)

# Named stress windows. Chosen ex-ante from market history, NOT by looking at
# the strategy's equity curve -- picking windows where it did badly would be
# reverse cherry-picking.
EVENTS = [
    ("COVID crash",            "2020-02-19", "2020-03-23"),
    ("COVID rebound",          "2020-03-24", "2020-06-08"),
    ("2022 bear market",       "2022-01-04", "2022-10-12"),
    ("2022 Q4 rally",          "2022-10-13", "2022-12-30"),
    ("SVB / banks",            "2023-03-08", "2023-03-31"),
    ("Aug-2024 vol spike",     "2024-07-16", "2024-08-07"),
    ("Apr-2025 selloff",       "2025-02-19", "2025-04-30"),
]


def section(t):
    print("\n" + "=" * 96)
    print(t)
    print("=" * 96)


def load_eq(prefix):
    return pd.read_csv(f"{prefix}_equity.csv",
                       parse_dates=["date"]).set_index("date")["pv"]


def spy_series():
    import yfinance as yf
    d = yf.download(["SPY"], start=START, end=END, progress=False,
                    auto_adjust=True)
    c = d["Close"]
    c = c["SPY"] if hasattr(c, "columns") else c
    return c.dropna()


# ── metrics ──────────────────────────────────────────────────────────────────
def metrics(r):
    """Full risk battery on a daily return series."""
    r = pd.Series(r).dropna()
    if len(r) < 20:
        return None
    eq = (1 + r).cumprod()
    yrs = len(r) / TRADING_DAYS
    tot = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else np.nan
    vol = r.std() * np.sqrt(TRADING_DAYS)
    dn = r[r < 0].std() * np.sqrt(TRADING_DAYS)
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    # ulcer index: RMS drawdown -- penalises depth AND duration, unlike MaxDD
    ulcer = np.sqrt((dd ** 2).mean()) * 100
    under = (dd < -0.01).mean()
    # longest underwater stretch in trading days
    uw, best, cur = (dd < -0.001).values, 0, 0
    for f in uw:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    var95 = np.percentile(r, 5)
    cvar95 = r[r <= var95].mean()
    tail = (np.percentile(r, 95) / abs(np.percentile(r, 5))) if np.percentile(r, 5) else np.nan
    return dict(
        n=len(r), tot=tot * 100, cagr=cagr * 100, vol=vol * 100,
        sharpe=r.mean() / r.std() * np.sqrt(TRADING_DAYS) if r.std() else np.nan,
        sortino=r.mean() * TRADING_DAYS / dn if dn else np.nan,
        calmar=cagr / abs(mdd) if mdd else np.nan,
        mdd=mdd * 100, ulcer=ulcer, under=under * 100, uw_days=best,
        var95=var95 * 100, cvar95=cvar95 * 100, tail=tail,
        skew=r.skew(), kurt=r.kurtosis(),
        worst=r.min() * 100, best_d=r.max() * 100,
    )


# ── Phase 9 ──────────────────────────────────────────────────────────────────
def phase9(prefixes, spy):
    section("PHASE 9 — REGIME ANALYSIS")
    sr = spy.pct_change().dropna()
    ma200 = spy.rolling(200).mean()
    rv = sr.rolling(21).std() * np.sqrt(TRADING_DAYS)

    regimes = {
        "SPY > MA200 (bull)":  (spy > ma200),
        "SPY < MA200 (bear)":  (spy < ma200),
        "realised vol < 15%":  (rv < 0.15),
        "realised vol 15-25%": (rv >= 0.15) & (rv < 0.25),
        "realised vol > 25%":  (rv >= 0.25),
        "calendar 2022 only":  pd.Series(
            (spy.index >= "2022-01-01") & (spy.index <= "2022-12-31"),
            index=spy.index),
    }
    print("\n  Strategy CAGR / Sharpe / MaxDD within each regime, with SPY for scale.")
    for p in prefixes:
        r = load_eq(p).pct_change().dropna()
        print(f"\n  {p}")
        print(f"    {'regime':<24}{'days':>6}{'CAGR':>10}{'Sharpe':>9}"
              f"{'MaxDD':>9}   {'SPY CAGR':>10}{'SPY Sh':>8}")
        print("    " + "-" * 78)
        for name, mask in regimes.items():
            m = mask.reindex(r.index).fillna(False)
            x, s = r[m], sr.reindex(r.index)[m]
            a, b = metrics(x), metrics(s)
            if not a or not b:
                continue
            print(f"    {name:<24}{a['n']:>6}{a['cagr']:>9.1f}%{a['sharpe']:>9.2f}"
                  f"{a['mdd']:>8.1f}%   {b['cagr']:>9.1f}%{b['sharpe']:>8.2f}")
    print("""
  Caveat that governs this whole table: 'SPY < MA200' inside 2019-2026 is a set
  of dips inside a secular bull, not a bear market sample. 2022 is the only
  genuine bear candidate and it is 12 months of one regime. Regime robustness
  is NOT established by these numbers; at best it is not contradicted.""")


# ── Phase 13 ─────────────────────────────────────────────────────────────────
def phase13(prefixes, spy):
    section("PHASE 13 — EVENT AND TAIL RISK")
    sr = spy.pct_change().dropna()
    print("\n  Named windows, selected from market history before looking at "
          "the equity curves.")
    for p in prefixes:
        e = load_eq(p)
        r = e.pct_change().dropna()
        print(f"\n  {p}")
        print(f"    {'event':<22}{'window':<25}{'strategy':>10}{'SPY':>9}{'diff':>9}")
        print("    " + "-" * 75)
        for name, a, b in EVENTS:
            m = (r.index >= a) & (r.index <= b)
            if m.sum() < 3:
                continue
            sx = (1 + r[m]).prod() - 1
            sy = (1 + sr.reindex(r.index)[m].fillna(0)).prod() - 1
            print(f"    {name:<22}{a}..{b[2:]:<15}{sx*100:>9.1f}%{sy*100:>8.1f}%"
                  f"{(sx-sy)*100:>8.1f}%")

        print(f"\n    worst 5 single days:")
        w = r.nsmallest(5)
        for d, v in w.items():
            sv = sr.get(d, np.nan)
            print(f"      {d.date()}  {v*100:+6.2f}%   (SPY {sv*100:+6.2f}%)")
        print(f"    worst 5 rolling 21-day windows:")
        roll = (1 + r).rolling(21).apply(np.prod, raw=True) - 1
        for d, v in roll.nsmallest(5).items():
            print(f"      ending {d.date()}  {v*100:+6.2f}%")


# ── Phase 15 ─────────────────────────────────────────────────────────────────
def phase15(prefixes, spy):
    section("PHASE 15 — RISK METRICS BATTERY")
    rows = []
    sr = spy.pct_change().dropna()
    for p in prefixes:
        m = metrics(load_eq(p).pct_change().dropna())
        m["name"] = p
        rows.append(m)
    m = metrics(sr); m["name"] = "SPY (benchmark)"; rows.append(m)

    def table(cols, hdr, fmt):
        print(f"\n  {'config':<18}" + "".join(f"{h:>10}" for h in hdr))
        print("  " + "-" * (18 + 10 * len(hdr)))
        for r in rows:
            print(f"  {r['name']:<18}" +
                  "".join(f"{fmt(r[c]):>10}" for c in cols))

    table(["cagr", "vol", "sharpe", "sortino", "calmar"],
          ["CAGR%", "Vol%", "Sharpe", "Sortino", "Calmar"],
          lambda v: f"{v:.2f}")
    table(["mdd", "ulcer", "under", "uw_days"],
          ["MaxDD%", "Ulcer", "%underwtr", "longestUW"],
          lambda v: f"{v:.1f}")
    table(["var95", "cvar95", "tail", "skew", "kurt", "worst"],
          ["VaR95%", "CVaR95%", "TailRatio", "Skew", "ExKurt", "WorstDay%"],
          lambda v: f"{v:.2f}")
    print("""
  Ulcer index is RMS drawdown: it charges for depth AND time spent down, which
  MaxDD (a single worst point) does not. Longest-UW is in trading days.
  Tail ratio is p95/|p5| of daily returns: >1 means the right tail pays more
  than the left tail costs.""")


def main(prefixes):
    spy = spy_series()
    phase9(prefixes, spy)
    phase13(prefixes, spy)
    phase15(prefixes, spy)


if __name__ == "__main__":
    main(sys.argv[1:] or ["f_base", "a_lo_base", "s2_full_fix"])
