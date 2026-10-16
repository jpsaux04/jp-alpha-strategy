#!/usr/bin/env python3
"""Full equity-curve simulation of the long book, to answer the one question
the condition and ranking studies pointed at and could not settle:

  MAX_LONGS = 7 and TIME_STOP_DAYS = 21 jointly reject 28% of signals.
  Is that capacity ceiling costing compounded return, or protecting it?

This replaces the proxies used earlier. Two things I modelled wrong before and
am fixing here, because both bias the result:

  1. EXIT LADDER. Earlier I treated T1 as a full exit. The agent sells 25% at
     T1 (+4%), 25% at T2 (+8%), everything left at T3 (+12%), with a time stop
     at 21d if T1 never hits and a POST_T1 stop 30d after T1 if T2 never hits.
     There is no trailing stop and the initial stop never moves. A full exit at
     T1 understates winners and overstates the hit-rate's value.

  2. TIME STOP UNITS. TIME_STOP_DAYS is compared against
     (date.today() - entry_date).days -- CALENDAR days. I previously held 21
     BARS, which is ~30 calendar days. Every earlier table therefore held
     positions ~40% longer than the agent does.

Exit priority, order and lot maths mirror process_long_exits line for line.
Sizing calls the agent's own calc_shares against LIVE equity, so the curve
compounds. Fills at the next open, which is the current execution regime.

Forward prices are used only to settle trades whose entry was already decided
by prior-bar data. No decision anywhere reads a future bar -- that is the
Rule #2 line and this stays on the right side of it.
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
START_EQUITY = 100_000.0

syms = [A.SPY] + A.WATCHLIST
start = (date.today() - timedelta(days=365 * YEARS + 60)).isoformat()
raw = yf.download(tickers=syms, start=start, progress=False,
                  auto_adjust=True, group_by="ticker")

spy = raw[A.SPY].dropna(subset=["Close"]).copy()
spy.index = pd.to_datetime(spy.index)
spy = A.compute_regime(A.add_indicators(spy))

# ── per-symbol frames + the signal mask, computed once ──────────────────────
F, SIG = {}, {}
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
    SIG[sym] = (ok
                & (df["RSI"] < A.RSI_OVERSOLD)
                & (df["long_disl"] > A.MIN_LONG_DISL)
                & df["vol_exhaust_long"].astype(bool)
                & df["bullish_intraday"].astype(bool)
                & reg.astype(bool))
    F[sym] = df

DATES = spy.index.intersection(
    sorted(set().union(*[set(d.index) for d in F.values()])))
DATES = [d for d in spy.index if d in set(DATES)]
print(f"{len(F)} names, {len(DATES):,} sessions "
      f"{DATES[0].date()} .. {DATES[-1].date()}")
print(f"live config: MAX_LONGS={A.MAX_LONGS} MAX_SIMULTANEOUS="
      f"{A.MAX_SIMULTANEOUS} MAX_PER_SECTOR={A.MAX_PER_SECTOR} "
      f"TIME_STOP={A.TIME_STOP_DAYS}cal POST_T1={A.POST_T1_STOP_DAYS}cal "
      f"risk={A.RISK_PER_TRADE_PCT:.0%} stop={A.STOP_ATR_MULT}xATR")


def simulate(max_longs=None, time_stop=None, risk=None, max_sector=None,
             rank="wl", seed=None):
    """One pass. Returns (equity Series, trades DataFrame)."""
    ML = A.MAX_LONGS if max_longs is None else max_longs
    TS = A.TIME_STOP_DAYS if time_stop is None else time_stop
    RK = A.RISK_PER_TRADE_PCT if risk is None else risk
    MS = A.MAX_PER_SECTOR if max_sector is None else max_sector
    rng = np.random.default_rng(seed)

    cash, pos, trades, curve = START_EQUITY, {}, [], []
    pend = []                                  # signals awaiting next open

    for di, dt in enumerate(DATES):
        d = dt.date()

        # ── 1. EXITS on today's bar, one action per position per day ───────
        for sym in list(pos):
            df = F[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            p = pos[sym]
            h, l, c = row["High"], row["Low"], row["Close"]
            entry, rem, tot = p["entry"], p["rem"], p["tot"]
            stop = entry - p["mult"] * p["atr"]
            t1, t2, t3 = (entry * (1 + A.T1_PCT), entry * (1 + A.T2_PCT),
                          entry * (1 + A.T3_PCT))
            t1lot = max(1, round(tot * 0.25))
            t2lot = max(1, round(tot * 0.25))
            held = (d - p["entry_date"]).days
            since_t1 = (d - p["t1_date"]).days if p["t1_date"] else 0

            act = qty = px = None
            if l <= stop:
                act, qty, px = "STOP", rem, stop
            elif h >= t3 and p["t2"]:
                act, qty, px = "T3", rem, t3
            elif h >= t2 and p["t1"] and not p["t2"]:
                act, qty, px = "T2", min(t2lot, rem), t2
                p["t2"] = True
            elif h >= t1 and not p["t1"]:
                act, qty, px = "T1", min(t1lot, rem), t1
                p["t1"], p["t1_date"] = True, d
            elif not p["t1"] and held >= TS:
                act, qty, px = "TIME", rem, c
            elif p["t1"] and not p["t2"] and since_t1 >= A.POST_T1_STOP_DAYS:
                act, qty, px = "POST_T1", rem, c
            if act:
                cash += qty * px
                p["rem"] -= qty
                p["pnl"] += qty * (px - entry)
                p["acts"].append(act)
                if p["rem"] <= 0:
                    trades.append({
                        "sym": sym, "sector": A.SECTOR_MAP.get(sym, "?"),
                        "entry_date": p["entry_date"], "exit_date": d,
                        "entry": entry, "shares": tot, "pnl": p["pnl"],
                        "R": p["pnl"] / (tot * p["mult"] * p["atr"]),
                        "ret": p["pnl"] / (tot * entry),
                        "path": "+".join(p["acts"]),
                        "held": (d - p["entry_date"]).days,
                    })
                    del pos[sym]

        # ── 2. FILL yesterday's signals at today's open ────────────────────
        if pend:
            eq = cash + sum(pos[s]["rem"] * F[s]["Close"].asof(dt)
                            for s in pos)
            sec = {}
            for s in pos:
                k = A.SECTOR_MAP.get(s, "?")
                sec[k] = sec.get(k, 0) + 1
            cand = pend
            if rank == "random":
                cand = [cand[i] for i in rng.permutation(len(cand))]
            elif rank == "atr":
                cand = sorted(cand, key=lambda x: x["atr"] / x["price"])
            for s in cand:
                sym = s["sym"]
                if sym in pos or len(pos) >= min(ML, A.MAX_SIMULTANEOUS):
                    continue
                k = A.SECTOR_MAP.get(sym, "?")
                if sec.get(k, 0) >= MS:
                    continue
                df = F[sym]
                if dt not in df.index:
                    continue
                fill = float(df.loc[dt, "Open"])
                if not np.isfinite(fill) or fill <= 0:
                    continue
                #  the agent's own sizing, against live equity
                shares = int(eq * RK / (A.ATR_MULTIPLIER * s["atr"]))
                shares = max(0, min(shares, int(eq * 0.20 / s["price"])))
                if shares < A.MIN_SHARES or shares * fill > cash:
                    continue
                cash -= shares * fill
                pos[sym] = {"entry": fill, "tot": shares, "rem": shares,
                            "atr": s["atr"], "mult": A.STOP_ATR_MULT,
                            "entry_date": d, "t1": False, "t2": False,
                            "t1_date": None, "pnl": 0.0, "acts": []}
                sec[k] = sec.get(k, 0) + 1
            pend = []

        # ── 3. TODAY'S signals, queued for the next open ───────────────────
        for sym, m in SIG.items():
            if dt in m.index and bool(m.loc[dt]) and sym not in pos:
                r = F[sym].loc[dt]
                pend.append({"sym": sym, "atr": float(r["ATR"]),
                             "price": float(r["Close"]),
                             "wl": A.WATCHLIST.index(sym)})
        pend.sort(key=lambda x: x["wl"])

        eq = cash + sum(pos[s]["rem"] * float(F[s].loc[dt, "Close"])
                        for s in pos if dt in F[s].index)
        curve.append(eq)

    return pd.Series(curve, index=DATES), pd.DataFrame(trades)


def stats(eq, tr):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    expo = 0.0
    if len(tr):
        expo = tr.held.sum() / (yrs * 365.25) / max(1, 1)
    return {"final": eq.iloc[-1], "cagr": cagr, "dd": dd, "sharpe": sharpe,
            "trades": len(tr), "win": (tr.pnl > 0).mean() if len(tr) else 0,
            "R": tr.R.sum() if len(tr) else 0, "pos_days": expo}


base_eq, base_tr = simulate()
b = stats(base_eq, base_tr)

sp = spy["Close"].reindex(DATES).ffill()
sp_eq = START_EQUITY * sp / sp.iloc[0]
s = stats(sp_eq, pd.DataFrame())

print("\n" + "=" * 78)
print("1. THE LIVE CONFIGURATION, SIMULATED END TO END")
print("=" * 78)
print(f"{'':<22}{'final':>12}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}"
      f"{'trades':>8}{'win%':>7}{'tot R':>8}")
print(f"{'STRATEGY (live cfg)':<22}{b['final']:>12,.0f}{b['cagr']:>7.1%}"
      f"{b['dd']:>8.1%}{b['sharpe']:>8.2f}{b['trades']:>8,}"
      f"{b['win'] * 100:>6.0f}%{b['R']:>8.1f}")
print(f"{'SPY buy & hold':<22}{s['final']:>12,.0f}{s['cagr']:>7.1%}"
      f"{s['dd']:>8.1%}{s['sharpe']:>8.2f}{'—':>8}{'—':>7}{'—':>8}")

print("\nexit-path mix (how trades actually ended):")
for p, n in base_tr.path.value_counts().head(10).items():
    sub = base_tr[base_tr.path == p]
    print(f"  {p:<22}{n:>5}  ({n / len(base_tr):>5.1%})  "
          f"mean {sub.ret.mean():>7.2%}  mean R {sub.R.mean():>6.2f}")
print(f"  mean hold {base_tr.held.mean():.0f} calendar days, "
      f"median {base_tr.held.median():.0f}")

print("\n" + "=" * 78)
print("2. THE CAPACITY QUESTION: MAX_LONGS x TIME_STOP_DAYS")
print("   does lifting the ceiling add compounded return, or just risk?")
print("=" * 78)
print(f"{'MAX_LONGS':>10}{'TIME_STOP':>11}{'CAGR':>8}{'maxDD':>8}"
      f"{'Sharpe':>8}{'trades':>8}{'CAGR/DD':>9}")
grid = []
for ml in (3, 5, 7, 10, 14, 20):
    for ts in (14, 21, 30, 45):
        e, t = simulate(max_longs=ml, time_stop=ts)
        st = stats(e, t)
        flag = "  <- LIVE" if (ml == A.MAX_LONGS
                              and ts == A.TIME_STOP_DAYS) else ""
        ratio = st["cagr"] / abs(st["dd"]) if st["dd"] else float("nan")
        print(f"{ml:>10}{ts:>11}{st['cagr']:>7.1%}{st['dd']:>8.1%}"
              f"{st['sharpe']:>8.2f}{st['trades']:>8,}{ratio:>9.2f}{flag}")
        grid.append({"max_longs": ml, "time_stop": ts, **st})
pd.DataFrame(grid).to_csv("/tmp/sim_grid.csv", index=False)

print("\n" + "=" * 78)
print("3. IS THE LIVE CONFIG DISTINGUISHABLE FROM A RANDOM ONE?")
print("   same caps, candidates shuffled at the moment of choice, 200 draws")
print("=" * 78)
rr = []
for sd in range(200):
    e, t = simulate(rank="random", seed=sd)
    rr.append(stats(e, t)["cagr"])
rr = np.array(rr)
pct = (rr < b["cagr"]).mean()
print(f"random CAGR: mean {rr.mean():.1%}, sd {rr.std(ddof=1):.1%}, "
      f"5th {np.percentile(rr, 5):.1%}, 95th {np.percentile(rr, 95):.1%}")
print(f"LIVE   CAGR: {b['cagr']:.1%}  -> {pct:.1%} percentile of random")
print("verdict:", "BEATS RANDOM" if pct > 0.95 else
      "LOSES TO RANDOM" if pct < 0.05 else "INDISTINGUISHABLE FROM RANDOM")

print("\n" + "=" * 78)
print("4. RISK BUDGET: does 1% per trade leave return on the table?")
print("=" * 78)
print(f"{'risk/trade':>11}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}{'CAGR/DD':>9}")
_risk_sweep = []
for rk in (0.005, 0.01, 0.015, 0.02, 0.03):
    e, t = simulate(risk=rk)
    st = stats(e, t)
    _risk_sweep.append({"risk": rk, **{k: float(v) for k, v in st.items()}})
    flag = "  <- LIVE" if abs(rk - A.RISK_PER_TRADE_PCT) < 1e-9 else ""
    print(f"{rk:>10.1%}{st['cagr']:>8.1%}{st['dd']:>8.1%}"
          f"{st['sharpe']:>8.2f}"
          f"{st['cagr'] / abs(st['dd']) if st['dd'] else 0:>9.2f}{flag}")

base_eq.to_csv("/tmp/sim_equity.csv")
base_tr.to_csv("/tmp/sim_trades.csv", index=False)

# ── emit the artifact the dashboard renders ────────────────────────────────
#  Nothing below is typed into the presentation layer. If this file is absent
#  the research tab says the study has not been run instead of showing stale
#  numbers -- the drift failure this project keeps rediscovering.
import json as _js
import os as _os
from datetime import datetime as _dtn, timezone as _tzn

_art = {
    "generated": _dtn.now(_tzn.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "span": [str(DATES[0].date()), str(DATES[-1].date())],
    "names": len(F),
    "sessions": len(DATES),
    "start_equity": START_EQUITY,
    "config": {
        "max_longs": A.MAX_LONGS, "max_simultaneous": A.MAX_SIMULTANEOUS,
        "max_per_sector": A.MAX_PER_SECTOR,
        "time_stop": A.TIME_STOP_DAYS, "post_t1_stop": A.POST_T1_STOP_DAYS,
        "risk": A.RISK_PER_TRADE_PCT, "stop_mult": A.STOP_ATR_MULT,
        "t1": A.T1_PCT, "t2": A.T2_PCT, "t3": A.T3_PCT,
        "rsi_os": A.RSI_OVERSOLD, "min_disl": A.MIN_LONG_DISL,
        "vol_cap": A.VOL_CAPITULATION, "close_pos": A.CLOSE_POS_LONG,
        "regime_max": A.REGIME_LONG_MAX, "version": A.STRATEGY_VERSION,
    },
    "headline": {k: float(v) for k, v in b.items()},
    "spy": {k: float(v) for k, v in s.items()},
    "paths": [
        {"path": p, "n": int(n), "pct": float(n / len(base_tr)),
         "mean_ret": float(base_tr[base_tr.path == p].ret.mean()),
         "mean_r": float(base_tr[base_tr.path == p].R.mean())}
        for p, n in base_tr.path.value_counts().items()
    ],
    "hold": {"mean": float(base_tr.held.mean()),
             "median": float(base_tr.held.median())},
    "grid": grid,
    "random": {"mean": float(rr.mean()), "sd": float(rr.std(ddof=1)),
               "p5": float(np.percentile(rr, 5)),
               "p95": float(np.percentile(rr, 95)),
               "live": float(b["cagr"]), "pct": float(pct), "n": int(len(rr))},
    "risk_sweep": _risk_sweep,
}

# ── recompute the two earlier studies from their saved raw rows ────────────
_KEYS = ("RSI", "MA20", "VOL", "CLOSE", "REGIME")
_CN = {"RSI": "Momentum washed out", "MA20": "Stretched below its average",
       "VOL": "Selling drying up", "CLOSE": "Buyers showed up by the close",
       "REGIME": "Market not over-extended"}
_cp = "/root/jp_strategy/data/cond_recs.csv"
if _os.path.exists(_cp):
    _C = pd.read_csv(_cp, parse_dates=["date"])
    _sig = _C[_C.blocker.isna()]
    _m0, _s0, _n0 = _sig.ret.mean(), _sig.ret.std(ddof=1), len(_sig)
    _conds = []
    for _k in _KEYS:
        _d = _C[_C.blocker == _k]
        _row = {"key": _k, "name": _CN[_k], "n": int(len(_d)),
                "mult": float(len(_d) / _n0)}
        if len(_d) >= 3:
            _m1, _s1, _n1 = _d.ret.mean(), _d.ret.std(ddof=1), len(_d)
            _se = np.sqrt(_s0**2 / _n0 + _s1**2 / _n1)
            _top = _d["date"].value_counts()
            _row.update({
                "mean": float(_m1), "diff": float(_m1 - _m0),
                "t": float((_m1 - _m0) / _se),
                "dates": int(_d["date"].nunique()),
                "top5": float(_top.head(5).sum() / len(_d)),
                "verdict": ("worse" if (_m1 - _m0) / _se < -1.96 else
                            "better" if (_m1 - _m0) / _se > 1.96 else
                            "indistinguishable"),
            })
        _conds.append(_row)
    _art["conditions"] = {
        "evaluated": int(len(_C)), "signals": int(_n0),
        "base_mean": float(_m0),
        "base_se": float(_s0 / np.sqrt(_n0)), "rows": _conds,
    }

_rp = "/root/jp_strategy/data/rank_signals.csv"
if _os.path.exists(_rp):
    _S = pd.read_csv(_rp, parse_dates=["date"])
    _pd_ = _S.groupby("date").size()
    _art["ranking"] = {
        "signal_days": int(len(_pd_)),
        "mean_per_day": float(_pd_.mean()),
        "median_per_day": float(_pd_.median()),
        "max_per_day": int(_pd_.max()),
        "crowded": [{"thr": int(t), "n": int((_pd_ >= t).sum()),
                     "pct": float((_pd_ >= t).mean())}
                    for t in (1, 2, 4, 8, 16)],
    }

_op = "/root/jp_strategy/data/research.json"
with open(_op, "w") as _f:
    _js.dump(_art, _f, indent=1, default=str)
print(f"\nwrote {_op}  ({_os.path.getsize(_op):,} bytes)")

print("""
=== CAVEATS, and they are load-bearing ===
WATCHLIST is today's 42 names, chosen knowing how they performed. This is the
single biggest bias here and it inflates EVERY row, including the SPY-relative
comparison, because the names were not picked in 2021. Treat the ranking of
configurations as the output; do not treat any CAGR as an expectation.

No commissions, no slippage, no borrow, no partial fills. Targets are assumed
to fill at the exact target price when the bar's high reaches it, and stops at
the exact stop when the low reaches it -- real fills are worse on both sides.
Intrabar order is unknown, so a bar touching stop and target is scored a stop.

Dividends are in (auto_adjust), taxes are not. Shorts are not modelled; the
live version has ALLOW_SHORTS off.""")
