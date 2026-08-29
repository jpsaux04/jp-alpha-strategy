#!/usr/bin/env python3
"""PHASE 8 — FACTOR AND BETA ATTRIBUTION.

Question: is any of this alpha, or is it long beta in a bull market?

Method
------
Daily strategy returns are regressed on factor returns:

    r_t - rf_t = alpha + SUM_k beta_k * f_kt + e_t

Standard errors are **Newey-West** (HAC, 5 lags). Plain OLS t-stats are
overstated here because the strategy holds overlapping multi-day positions,
which induces serial correlation in daily returns.

Factors: Fama-French 5 + momentum from the Ken French data library where
reachable; otherwise long/short ETF mimicking portfolios. Which source was
used is always printed.

Reporting discipline (per the brief)
------------------------------------
* every estimate carries n, t-stat and a 95% CI
* annualised alpha is shown ONLY alongside the daily estimate and its CI,
  because annualising a noisy daily mean inflates the apparent effect
* average net exposure is reported, because a strategy that is 40% invested
  will show beta ~0.4 and a correspondingly small alpha for purely
  mechanical reasons

Usage: python3 research/factor_attribution.py <equity_prefix> [more prefixes...]
"""
import io
import sys
import zipfile
import urllib.request

import numpy as np
import pandas as pd

TRADING_DAYS = 252
NW_LAGS = 5

FF5_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
           "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip")
MOM_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
           "F-F_Momentum_Factor_daily_CSV.zip")

# ETF fallback: (factor name, long leg, short leg or None)
ETF_FACTORS = [
    ("MKT",  "SPY",  None),
    ("SMB",  "IWM",  "SPY"),    # small minus large
    ("HML",  "IWD",  "IWF"),    # value minus growth
    ("MOM",  "MTUM", "SPY"),    # momentum
    ("QMJ",  "QUAL", "SPY"),    # quality
    ("LVOL", "USMV", "SPY"),    # low volatility
]


# ── econometrics ─────────────────────────────────────────────────────────────
def newey_west_ols(y, X, lags=NW_LAGS):
    """OLS with HAC (Newey-West) covariance. X must already include a constant."""
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta

    # Newey-West meat matrix
    S = (resid[:, None] * X).T @ (resid[:, None] * X)
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1.0)
        u_t = resid[L:, None] * X[L:]
        u_tL = resid[:-L, None] * X[:-L]
        G = u_t.T @ u_tL
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0))

    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    adj_r2 = 1 - (1 - r2) * (n - 1) / (n - k) if n > k else np.nan
    return {
        "beta": beta, "se": se, "t": np.divide(beta, se, out=np.zeros_like(beta),
                                               where=se > 0),
        "r2": r2, "adj_r2": adj_r2, "n": n,
        "resid_vol_ann": float(resid.std(ddof=k)) * np.sqrt(TRADING_DAYS),
    }


def stars(t):
    a = abs(t)
    return "***" if a > 2.576 else "**" if a > 1.96 else "*" if a > 1.645 else ""


# ── data ─────────────────────────────────────────────────────────────────────
def _ff_zip(url, skip_scan=True):
    with urllib.request.urlopen(url, timeout=30) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    name = z.namelist()[0]
    raw = z.read(name).decode("latin-1").splitlines()
    start = next(i for i, ln in enumerate(raw)
                 if ln.strip()[:8].isdigit() and len(ln.strip()[:8]) == 8)
    rows = []
    for ln in raw[start:]:
        p = [x.strip() for x in ln.split(",")]
        if not p or not p[0].isdigit() or len(p[0]) != 8:
            break
        rows.append(p)
    hdr = [x.strip() for x in raw[start - 1].split(",")][1:]
    df = pd.DataFrame(rows).set_index(0)
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.iloc[:, :len(hdr)]
    df.columns = hdr
    return df.apply(pd.to_numeric, errors="coerce") / 100.0


def load_factors(start, end):
    """Ken French if reachable, else ETF mimicking portfolios."""
    try:
        ff = _ff_zip(FF5_URL)
        mom = _ff_zip(MOM_URL)
        ff = ff.join(mom, how="inner")
        ff.columns = [c.strip().replace("Mkt-RF", "MKT").replace("Mom", "MOM")
                      for c in ff.columns]
        ff = ff.loc[start:end]
        if len(ff) < 200:
            raise ValueError("insufficient FF rows")
        rf = ff["RF"]
        cols = [c for c in ("MKT", "SMB", "HML", "RMW", "CMA", "MOM") if c in ff]
        print(f"  factor source: Ken French research library ({len(ff)} days, "
              f"{', '.join(cols)})")
        return ff[cols], rf, "ken_french"
    except Exception as e:
        print(f"  [Ken French unavailable: {type(e).__name__}: {e}] -> ETF proxies")

    import yfinance as yf
    tick = sorted({t for _, a, b in ETF_FACTORS for t in (a, b) if t})
    px = yf.download(tick, start=start, end=end, progress=False, auto_adjust=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    ret = px.pct_change().dropna(how="all")
    out = {}
    for name, lo, sh in ETF_FACTORS:
        if lo not in ret:
            continue
        out[name] = ret[lo] - ret[sh] if sh and sh in ret else ret[lo]
    F = pd.DataFrame(out).dropna()
    print(f"  factor source: ETF mimicking portfolios ({len(F)} days, "
          f"{', '.join(F.columns)})")
    return F, pd.Series(0.0, index=F.index), "etf_proxy"


def strategy_returns(prefix):
    e = pd.read_csv(f"{prefix}_equity.csv", parse_dates=["date"]).set_index("date")["pv"]
    return e.pct_change().dropna()


# ── reporting ────────────────────────────────────────────────────────────────
def run_model(name, r_ex, F, cols):
    F = F[cols].dropna()
    idx = r_ex.index.intersection(F.index)
    y, X = r_ex.loc[idx].values, F.loc[idx]
    Xm = np.column_stack([np.ones(len(idx)), X.values])
    res = newey_west_ols(y, Xm)
    b, se, t = res["beta"], res["se"], res["t"]

    a_d, a_se = b[0], se[0]
    lo_d, hi_d = a_d - 1.96 * a_se, a_d + 1.96 * a_se
    print(f"\n  {name}   (n={res['n']} daily obs, Newey-West {NW_LAGS} lags)")
    print(f"    alpha      {a_d*1e4:+7.2f} bp/day  t={t[0]:+5.2f}{stars(t[0]):<3} "
          f"95% CI [{lo_d*1e4:+.2f}, {hi_d*1e4:+.2f}] bp/day")
    print(f"               annualised {a_d*TRADING_DAYS*100:+6.2f}%/yr   "
          f"CI [{lo_d*TRADING_DAYS*100:+.2f}%, {hi_d*TRADING_DAYS*100:+.2f}%]")
    for i, c in enumerate(cols, start=1):
        lo, hi = b[i] - 1.96 * se[i], b[i] + 1.96 * se[i]
        print(f"    {c:<6}     {b[i]:+7.3f}          t={t[i]:+5.2f}{stars(t[i]):<3} "
              f"95% CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"    R2={res['r2']:.3f}  adjR2={res['adj_r2']:.3f}  "
          f"residual vol {res['resid_vol_ann']*100:.2f}%/yr")
    return res


def exposure_stats(prefix):
    """Average net long exposure, to contextualise beta."""
    try:
        pos = pd.read_csv(f"{prefix}_positions.csv", parse_dates=["entry_date", "exit_date"])
        eq = pd.read_csv(f"{prefix}_equity.csv", parse_dates=["date"]).set_index("date")["pv"]
    except FileNotFoundError:
        return None
    gross = pd.Series(0.0, index=eq.index)
    net = pd.Series(0.0, index=eq.index)
    for _, p in pos.iterrows():
        m = (eq.index >= p.entry_date) & (eq.index < p.exit_date)
        gross[m] += p.cost_basis
        net[m] += p.cost_basis * (1 if p.direction == "long" else -1)
    return (gross / eq).mean(), (net / eq).mean(), ((gross / eq) > 0).mean()


def main(prefixes):
    print("=" * 100)
    print("PHASE 8 — FACTOR AND BETA ATTRIBUTION")
    print("=" * 100)

    allr = {p: strategy_returns(p) for p in prefixes}
    start = min(r.index.min() for r in allr.values()).strftime("%Y-%m-%d")
    end = max(r.index.max() for r in allr.values()).strftime("%Y-%m-%d")
    print(f"\nWindow {start} -> {end}")
    F, rf, src = load_factors(start, end)

    for p in prefixes:
        r = allr[p]
        idx = r.index.intersection(F.index)
        rfi = rf.reindex(idx).fillna(0.0)
        r_ex = r.loc[idx] - rfi

        print("\n" + "-" * 100)
        print(f"STRATEGY: {p}    {idx.min().date()} -> {idx.max().date()}   "
              f"n={len(idx)} days ({len(idx)/TRADING_DAYS:.1f} yrs)")
        tot = (1 + r.loc[idx]).prod() - 1
        vol = r.loc[idx].std() * np.sqrt(TRADING_DAYS)
        shp = (r_ex.mean() / r_ex.std() * np.sqrt(TRADING_DAYS)) if r_ex.std() > 0 else np.nan
        print(f"  total return {tot*100:+.1f}%   ann.vol {vol*100:.2f}%   "
              f"Sharpe(excess) {shp:.2f}")
        ex = exposure_stats(p)
        if ex:
            print(f"  avg gross exposure {ex[0]*100:.1f}%   avg NET exposure "
                  f"{ex[1]*100:.1f}%   time invested {ex[2]*100:.0f}%")
            print("  -> beta must be read against net exposure, not against 1.0")

        run_model("CAPM            ", r_ex, F, ["MKT"])
        for m in (["MKT", "SMB", "HML"],
                  ["MKT", "SMB", "HML", "MOM"],
                  [c for c in ["MKT", "SMB", "HML", "RMW", "CMA", "MOM"] if c in F]):
            if all(c in F for c in m) and len(m) > 1:
                run_model(f"{len(m)}-factor        ", r_ex, F, m)


if __name__ == "__main__":
    main(sys.argv[1:] or ["f_base"])
