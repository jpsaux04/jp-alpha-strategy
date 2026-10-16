#!/usr/bin/env python3
"""Given ~10x more qualifying candidates than the book can hold, HOW does the
agent choose -- and does that choice beat choosing at random?

process_entries iterates `for sym in WATCHLIST` and stops at the caps. So the
selection rule is literally the order of a hand-written list, which is grouped
by sector with AAPL/MSFT/NVDA at the top. That is a decision rule whether or
not it was intended as one, and it has never been measured.

Method
------
1. Generate every TRUE long signal (all five conditions + the same gating
   check_long_signal applies) over ~5y, with the features a ranker could use,
   and the forward trade outcome. Entry at the NEXT OPEN, 1.5xATR stop, +4% T1,
   21-day time stop, stop wins intrabar ties -- same as the condition study.

2. Replay day by day with the REAL caps (MAX_LONGS, MAX_PER_SECTOR, one
   position per symbol, slot occupied until the trade exits) under different
   selection rules. Only the ordering changes; everything else is held fixed.

3. Score in R-multiples, not percent. Sizing is equal-risk (1% of equity per
   position, stop distance = 1.5xATR), so portfolio P&L is proportional to the
   sum of R = ret / (stop_distance/entry). A 2% gain on a tight-ATR name is
   worth more to the book than 2% on a wide one, and percent returns hide that.

The null hypothesis is that WATCHLIST order is indistinguishable from random.
If it is, the selection rule is not costing anything -- but it also means the
list order is load-bearing by accident, which is worth knowing. If random
BEATS it, the sector grouping is actively concentrating the book.
"""
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

sys.path.insert(0, "/root/jp_strategy")
from dotenv import load_dotenv
load_dotenv("/root/jp_strategy/.env")
import jp_agent as A

YEARS = 5
HOLD = A.TIME_STOP_DAYS
N_RANDOM = 400

syms = [A.SPY] + A.WATCHLIST
start = (date.today() - timedelta(days=365 * YEARS + 60)).isoformat()
raw = yf.download(tickers=syms, start=start, progress=False,
                  auto_adjust=True, group_by="ticker")

spy = raw[A.SPY].dropna(subset=["Close"]).copy()
spy.index = pd.to_datetime(spy.index)
spy = A.compute_regime(A.add_indicators(spy))

WL_POS = {s: i for i, s in enumerate(A.WATCHLIST)}
rows = []

for sym in A.WATCHLIST:
    try:
        df = raw[sym].dropna(subset=["Close"]).copy()
    except Exception:
        continue
    if len(df) < 120:
        continue
    df.index = pd.to_datetime(df.index)
    df = A.add_indicators(df)
    reg = spy["regime_long"].reindex(df.index)

    ok = (df["Close"] >= A.MIN_PRICE) & df["ATR"].notna() & (df["ATR"] > 0) \
        & df["RSI"].notna() & df["MA20"].notna() & df["vol_avg"].notna() \
        & reg.notna()
    sig = (ok
           & (df["RSI"] < A.RSI_OVERSOLD)
           & (df["long_disl"] > A.MIN_LONG_DISL)
           & df["vol_exhaust_long"].astype(bool)
           & df["bullish_intraday"].astype(bool)
           & reg.astype(bool))

    o, h, l, cl = (df["Open"].values, df["High"].values,
                   df["Low"].values, df["Close"].values)
    atr, rsi, disl = (df["ATR"].values, df["RSI"].values,
                      df["long_disl"].values)
    vr = (df["Volume"] / df["vol_avg"]).values
    cp = ((df["Close"] - df["Low"]) / (df["High"] - df["Low"])
          .replace(0, np.nan)).values
    idx = df.index

    for i in np.where(sig.values)[0]:
        if i + 1 >= len(df):
            continue
        entry = o[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        a = atr[i]
        stop, t1 = entry - A.STOP_ATR_MULT * a, entry * (1 + A.T1_PCT)
        out, ex = None, i + 1
        for j in range(i + 1, min(i + 1 + HOLD, len(df))):
            ex = j
            if l[j] <= stop:
                out = "stop"
                break
            if h[j] >= t1:
                out = "t1"
                break
        if out is None:
            out = "timeout"
        ret = {"stop": stop / entry - 1, "t1": t1 / entry - 1,
               "timeout": cl[ex] / entry - 1}[out]
        risk = (A.STOP_ATR_MULT * a) / entry      # stop distance as a fraction
        shares = A.calc_shares(100_000.0, float(cl[i]), float(a))
        rows.append({
            "sym": sym, "date": idx[i].normalize(),
            "exit_date": idx[ex].normalize(),
            "sector": A.SECTOR_MAP.get(sym, "Unknown"),
            "wl": WL_POS[sym], "outcome": out, "ret": ret,
            "R": ret / risk, "rsi": rsi[i], "disl": disl[i],
            "atr_pct": a / cl[i], "vol_ratio": vr[i], "close_pos": cp[i],
            "tradable": shares >= A.MIN_SHARES,
        })

S = pd.DataFrame(rows).sort_values(["date", "wl"]).reset_index(drop=True)
S.to_csv("/tmp/rank_signals.csv", index=False)
print(f"true long signals: {len(S):,}  span {S.date.min().date()} .. "
      f"{S.date.max().date()}  names {S.sym.nunique()}")
print(f"caps replayed: {A.MAX_LONGS} long, {A.MAX_PER_SECTOR}/sector, "
      f"hold<= {HOLD}d; sizing drops {(~S.tradable).sum()} as under MIN_SHARES")
S = S[S.tradable].reset_index(drop=True)

print("\n" + "=" * 78)
print("0. HOW OVERSUBSCRIBED IS THE BOOK, DAY BY DAY?")
print("=" * 78)
per_day = S.groupby("date").size()
print(f"days with >=1 signal: {len(per_day):,}")
for thr in (1, 2, 4, 8, 16):
    n = int((per_day >= thr).sum())
    print(f"  days with >= {thr:>2} signals: {n:>5,}  ({n / len(per_day):>5.1%})")
print(f"  mean {per_day.mean():.1f}, median {per_day.median():.0f}, "
      f"max {per_day.max()} signals on a signal-day")


# ── the replay ─────────────────────────────────────────────────────────────
def replay(order_fn, seed=None):
    """Walk the dates. Hold a slot until the trade's exit date. Choose among
    that day's candidates with order_fn. Returns the taken trades."""
    rng = np.random.default_rng(seed)
    open_pos = {}                      # sym -> exit_date
    taken = []
    for dt, grp in S.groupby("date", sort=True):
        for s in [s for s, e in open_pos.items() if e < dt]:
            del open_pos[s]
        sec = {}
        for s in open_pos:
            k = A.SECTOR_MAP.get(s, "Unknown")
            sec[k] = sec.get(k, 0) + 1
        cand = grp[~grp.sym.isin(open_pos)]
        if cand.empty:
            continue
        cand = order_fn(cand, rng)
        for _, r in cand.iterrows():
            if len(open_pos) >= A.MAX_LONGS:
                break
            if sec.get(r.sector, 0) >= A.MAX_PER_SECTOR:
                continue
            open_pos[r.sym] = r.exit_date
            sec[r.sector] = sec.get(r.sector, 0) + 1
            taken.append(r)
    return pd.DataFrame(taken)


RULES = {
    "WATCHLIST order (ACTUAL)": lambda c, g: c.sort_values("wl"),
    "reverse WATCHLIST":        lambda c, g: c.sort_values("wl", ascending=False),
    "deepest RSI first":        lambda c, g: c.sort_values("rsi"),
    "most dislocated first":    lambda c, g: c.sort_values("disl", ascending=False),
    "widest ATR% first":        lambda c, g: c.sort_values("atr_pct", ascending=False),
    "tightest ATR% first":      lambda c, g: c.sort_values("atr_pct"),
    "biggest vol spike first":  lambda c, g: c.sort_values("vol_ratio",
                                                           ascending=False),
    "strongest close first":    lambda c, g: c.sort_values("close_pos",
                                                           ascending=False),
}

print("\n" + "=" * 78)
print("1. DOES THE SELECTION RULE MATTER?  (same caps, only the order differs)")
print("=" * 78)
print(f"{'rule':<26}{'trades':>8}{'T1%':>7}{'mean R':>9}{'total R':>9}"
      f"{'R/yr':>8}")
res = {}
span_yr = (S.date.max() - S.date.min()).days / 365.25
for name, fn in RULES.items():
    t = replay(fn)
    res[name] = t
    vc = t.outcome.value_counts(normalize=True)
    print(f"{name:<26}{len(t):>8,}{vc.get('t1', 0) * 100:>6.1f}%"
          f"{t.R.mean():>9.3f}{t.R.sum():>9.1f}{t.R.sum() / span_yr:>8.1f}")

print("\n" + "=" * 78)
print("2. VS RANDOM SELECTION  (same caps, candidates shuffled each day)")
print(f"   {N_RANDOM} independent draws -- this is the null the list order "
      f"must beat")
print("=" * 78)
tot, mn, nt = [], [], []
for s in range(N_RANDOM):
    t = replay(lambda c, g: c.iloc[g.permutation(len(c))], seed=s)
    tot.append(t.R.sum())
    mn.append(t.R.mean())
    nt.append(len(t))
tot, mn = np.array(tot), np.array(mn)
act = res["WATCHLIST order (ACTUAL)"]
a_tot = act.R.sum()
print(f"random total R : mean {tot.mean():.1f}, sd {tot.std(ddof=1):.1f}, "
      f"5th {np.percentile(tot, 5):.1f}, 95th {np.percentile(tot, 95):.1f}")
print(f"random trades  : mean {np.mean(nt):.0f}")
print(f"ACTUAL total R : {a_tot:.1f}   ({len(act):,} trades)")
pct = (tot < a_tot).mean()
z = (a_tot - tot.mean()) / tot.std(ddof=1)
print(f"\nACTUAL sits at the {pct:.1%} percentile of random  (z = {z:+.2f})")
print("verdict:", "ACTUAL BEATS RANDOM" if pct > 0.95 else
      "ACTUAL LOSES TO RANDOM" if pct < 0.05 else
      "INDISTINGUISHABLE FROM RANDOM")

print("\n" + "=" * 78)
print("3. WHAT THE LIST ORDER DOES TO THE BOOK'S COMPOSITION")
print("=" * 78)
print(f"{'sector':<24}{'ACTUAL':>9}{'random avg':>12}")
rnd_sec = {}
for s in range(50):
    t = replay(lambda c, g: c.iloc[g.permutation(len(c))], seed=1000 + s)
    for k, v in t.sector.value_counts().items():
        rnd_sec[k] = rnd_sec.get(k, 0) + v / 50
a_sec = act.sector.value_counts()
for k in sorted(set(a_sec.index) | set(rnd_sec)):
    print(f"{k:<24}{a_sec.get(k, 0):>9,}{rnd_sec.get(k, 0):>12.0f}")

print("\n" + "=" * 78)
print("4. OPPORTUNITY COST: what did the caps turn away?")
print("=" * 78)
taken_keys = set(zip(act.sym, act.date))
miss = S[~pd.Series(list(zip(S.sym, S.date)), index=S.index).isin(taken_keys)]
print(f"signals taken {len(act):,} of {len(S):,} "
      f"({len(act) / len(S):.1%}); turned away {len(miss):,}")
if len(miss) > 2:
    se = np.sqrt(act.R.std(ddof=1)**2 / len(act)
                 + miss.R.std(ddof=1)**2 / len(miss))
    d = miss.R.mean() - act.R.mean()
    print(f"taken      mean R {act.R.mean():>7.3f}  (n={len(act):,})")
    print(f"turned away mean R {miss.R.mean():>7.3f}  (n={len(miss):,})")
    print(f"difference {d:+.3f} R, t={d / se:+.2f}  -> "
          f"{'DIFFERENT' if abs(d / se) > 1.96 else 'indistinguishable'}")

print("""
CAVEATS
T1 is modelled as a full exit; the live strategy scales out 25% at T1, 25% at
T2 and trails the rest. Identical across every rule, so the comparison holds,
but no absolute number here is the strategy's P&L.

WATCHLIST is today's 42 names, picked knowing how they did. That inflates every
rule including ACTUAL. Again: compare rules, do not quote levels.

MAX_SIMULTANEOUS and shorts are not modelled -- longs only, so the long cap is
the binding one. No commissions, no slippage, no capital constraint.""")
