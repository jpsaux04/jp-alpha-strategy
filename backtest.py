#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  backtest.py — Historical Backtest Harness           (RESEARCH · READ-ONLY)
═══════════════════════════════════════════════════════════════════════════════

  Runs the JP Alpha v3 mean-reversion logic over years of history so the edge
  can be evaluated across many regimes (2020 crash, 2022 bear, rate cycles)
  instead of the ~10-week live sample. The live paper account then stands as
  genuine OUT-OF-SAMPLE forward validation of what this backtest shows.

  FIDELITY
    • The signal, sizing and exit rules are re-implemented here to match
      jp_agent.py EXACTLY (same Wilder RSI/ATR, same MA20 dislocation, same
      volume-exhaustion patterns, same regime filter, same ATR sizing, same
      tiered T1/T2/T3 exits, stop, and time stops, same position/sector caps).
    • It IMPORTS NOTHING from jp_agent.py — the frozen engine is never touched.
    • Look-ahead-free execution: signals are decided on a day's CLOSE and filled
      at the NEXT session's OPEN — the exact "run after close, market order fills
      next open" behavior of the live cron. No bar is used before it exists.

  DATA: yfinance daily OHLCV, auto_adjust=True (same source the live engine uses).

  OUTPUT: backtest_equity.csv, backtest_trades.csv, backtest_results.json,
          plus a printed summary. Writes NO trading state.

  USAGE
    python backtest.py                       # 2019-01-01 → inception (2026-05-13)
    python backtest.py 2015-01-01 2026-05-13
"""

import os
import sys
import json
import math
from pathlib import Path
from datetime import date, datetime

import numpy as np
import pandas as pd
import yfinance as yf

import analytics

BASE = Path(__file__).parent

# Research toggles (default OFF → full frozen strategy, files unchanged).
LONG_ONLY = os.environ.get("LONG_ONLY") == "1"
NO_SCALE_OUT = os.environ.get("NO_SCALE_OUT") == "1"
TRAIL_ATR = float(os.environ.get("TRAIL_ATR", "0") or 0)
STOP_ATR = float(os.environ.get("STOP_ATR", "0") or 0)
T3_OVR = float(os.environ.get("T3_PCT_OVERRIDE", "0") or 0)
ANCHOR_FILL = os.environ.get("ANCHOR_FILL") == "1"
PREFIX = os.environ.get("BT_PREFIX", "backtest")
DATA_FP = None   # set by run(); stamped into the manifest

# ── Strategy constants — copied verbatim from jp_agent.py (frozen) ───────────
RSI_PERIOD, MA_PERIOD, VOL_PERIOD, ATR_PERIOD, REGIME_MA = 14, 20, 20, 14, 50
# ── Phase 12 research overrides ─────────────────────────────────────────────
# Each defaults to the frozen JP_ALPHA_V3 constant. An unset environment
# reproduces the frozen baseline exactly; see research/param_robustness.py.
def _envf(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else float(v)

def _envi(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else int(v)

RSI_OVERSOLD     = _envf("P_RSI_OS", 45)
MIN_LONG_DISL    = _envf("P_LONG_DISL", 0.02)
VOL_CAPITULATION = _envf("P_VOL_CAP", 1.3)
RSI_OVERBOUGHT   = _envf("P_RSI_OB", 60)
MIN_SHORT_DISL   = _envf("P_SHORT_DISL", 0.02)
VOL_DISTRIBUTION = _envf("P_VOL_DIST", 1.3)
CLOSE_POS_LONG, CLOSE_POS_SHORT = 0.50, 0.50
REGIME_LONG_MAX  = _envf("P_REG_LONG", 0.10)
REGIME_SHORT_MIN = _envf("P_REG_SHORT", -0.10)
ATR_MULTIPLIER     = _envf("P_ATR_MULT", 1.5)
RISK_PER_TRADE_PCT = _envf("P_RISK", 0.01)
T1_PCT        = _envf("P_T1", 0.04)
T2_PCT        = _envf("P_T2", 0.08)
T3_PCT        = _envf("P_T3", 0.12)
STOP_LOSS_PCT = _envf("P_STOP", 0.08)
TIME_STOP_DAYS      = _envi("P_TIME_STOP", 21)
POST_T1_STOP_DAYS   = _envi("P_POST_T1_STOP", 30)
MAX_SIMULTANEOUS, MAX_LONGS, MAX_SHORTS, MAX_PER_SECTOR = 10, 7, 5, 2
MIN_PRICE, MIN_SHARES = 10.0, 1
STARTING_EQUITY = 100_000.0

SPY = "SPY"
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "INTC", "CSCO", "AMD", "QCOM", "MU", "AMAT",
    "GOOGL", "META", "NFLX", "AMZN", "HD", "NKE", "MCD", "SBUX",
    "JPM", "BAC", "GS", "WFC", "MS", "C",
    "UNH", "JNJ", "PFE", "ABBV", "LLY", "MRK",
    "CAT", "BA", "HON", "GE", "LMT", "XOM", "CVX", "COP",
    "WMT", "KO", "PG", "QQQ", "IWM",
]
SECTOR_MAP = {
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "INTC": "Tech", "CSCO": "Tech",
    "AMD": "Tech", "QCOM": "Tech", "MU": "Semis", "AMAT": "Semis",
    "GOOGL": "CommSvc", "META": "CommSvc", "NFLX": "CommSvc",
    "AMZN": "ConDisc", "HD": "ConDisc", "NKE": "ConDisc", "MCD": "ConDisc", "SBUX": "ConDisc",
    "JPM": "Finance", "BAC": "Finance", "GS": "Finance", "WFC": "Finance", "MS": "Finance", "C": "Finance",
    "UNH": "Health", "JNJ": "Health", "PFE": "Health", "ABBV": "Health", "LLY": "Health", "MRK": "Health",
    "CAT": "Industr", "BA": "Industr", "HON": "Industr", "GE": "Industr", "LMT": "Industr",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "WMT": "Staples", "KO": "Staples", "PG": "Staples", "QQQ": "ETF", "IWM": "ETF",
}


# ── Indicators — verbatim from jp_agent.py ───────────────────────────────────
def wilder_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_atr(high, low, close, period=14):
    tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def vol_exhaust_long(close, volume, vp=20, thr=None):
    thr = VOL_CAPITULATION if thr is None else thr
    avg = volume.rolling(vp).mean()
    down = close < close.shift(1)
    vdown = volume < volume.shift(1)
    cap = (volume > thr * avg) & down
    dry = (down & vdown & down.shift(1) & vdown.shift(1) & down.shift(2) & vdown.shift(2))
    return cap | dry


def vol_exhaust_short(close, volume, vp=20, thr=None):
    thr = VOL_DISTRIBUTION if thr is None else thr
    avg = volume.rolling(vp).mean()
    up = close > close.shift(1)
    vup = volume > volume.shift(1)
    dist = (volume > thr * avg) & up
    dry = (up & vup & up.shift(1) & vup.shift(1) & up.shift(2) & vup.shift(2))
    return dist | dry


def add_indicators(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    rng = (h - l).replace(0, np.nan)
    df["RSI"] = wilder_rsi(c, RSI_PERIOD)
    df["ATR"] = calc_atr(h, l, c, ATR_PERIOD)
    df["MA20"] = c.rolling(MA_PERIOD).mean()
    df["long_disl"] = (df["MA20"] - c) / df["MA20"]
    df["short_disl"] = (c - df["MA20"]) / df["MA20"]
    df["vx_long"] = vol_exhaust_long(c, v, VOL_PERIOD, VOL_CAPITULATION)
    df["vx_short"] = vol_exhaust_short(c, v, VOL_PERIOD, VOL_DISTRIBUTION)
    ip = (c - l) / rng
    df["bull_id"] = ip >= CLOSE_POS_LONG
    df["bear_id"] = ip <= (1 - CLOSE_POS_SHORT)
    return df


def calc_shares(pv, price, atr):
    stop_distance = ATR_MULTIPLIER * atr
    if stop_distance <= 0:
        return 0
    shares = int(pv * RISK_PER_TRADE_PCT / stop_distance)
    if shares < MIN_SHARES:
        return 0
    max_shares = int(pv * 0.20 / price)
    return max(0, min(shares, max_shares))


# ─────────────────────────────────────────────────────────────────────────────
#  DATA
# ─────────────────────────────────────────────────────────────────────────────
def _cache_path(start, end):
    """Deterministic cache key: universe + window + data-source semantics."""
    import hashlib
    # The cached object contains COMPUTED INDICATORS and REGIME FLAGS, not raw
    # prices, so every constant that feeds them must be in the key. Omitting
    # them silently serves a frame built under different parameters.
    ind = "|".join(str(x) for x in (
        RSI_PERIOD, MA_PERIOD, VOL_PERIOD, ATR_PERIOD, REGIME_MA,
        VOL_CAPITULATION, VOL_DISTRIBUTION,
        REGIME_LONG_MAX, REGIME_SHORT_MIN,
        CLOSE_POS_LONG, CLOSE_POS_SHORT,
    ))
    key = ("|".join(sorted(WATCHLIST + [SPY]))
           + f"|{start}|{end}|yf-auto_adjust|ind:{ind}")
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    dd = BASE / "data"
    dd.mkdir(exist_ok=True)
    return dd / f"prices_{h}.pkl", h


def load_data(start, end):
    """Cached wrapper around the original loader (renamed _load_data_uncached).

    Reproducibility: a research run must be repeatable from its manifest, and
    yfinance revises adjusted history over time. The cache pins the frame.
    """
    import pickle
    cp, h = _cache_path(start, end)
    if cp.exists() and os.environ.get("BT_NOCACHE") != "1":
        with open(cp, "rb") as f:
            return pickle.load(f)
    obj = _load_data_uncached(start, end)
    try:
        with open(cp, "wb") as f:
            pickle.dump(obj, f)
    except Exception:
        pass
    return obj


def data_fingerprint(data):
    """Content hash of the actual price frame the run consumed.

    yfinance revises adjusted history, so the same code on the same window can
    produce different numbers on different days. The cache key pins WHICH frame
    was requested; this pins WHAT WAS IN IT. Only Close is hashed: it is what
    every signal and every fill is ultimately derived from, and hashing OHLCV
    would make the fingerprint churn on vendor volume restatements that cannot
    move the equity curve.
    """
    import hashlib
    h = hashlib.sha256()
    for sym in sorted(data):
        c = data[sym]["Close"].to_numpy(dtype="float64")
        h.update(sym.encode())
        h.update(np.round(c, 6).tobytes())
    return h.hexdigest()[:16]


def _load_data_uncached(start, end):
    syms = [SPY] + WATCHLIST
    raw = yf.download(syms, start=start, end=end, progress=False,
                      auto_adjust=True, group_by="ticker")
    data = {}
    for s in syms:
        try:
            df = raw[s].copy().dropna(subset=["Close"])
            if len(df) < 60:
                continue
            df.index = pd.to_datetime(df.index)
            data[s] = add_indicators(df)
        except Exception as e:
            print(f"  {s}: {e}", file=sys.stderr)
    # regime on SPY
    spy = data[SPY]
    spy["MA50"] = spy["Close"].rolling(REGIME_MA).mean()
    dev = (spy["Close"] - spy["MA50"]) / spy["MA50"]
    spy["regime_long"] = dev <= REGIME_LONG_MAX
    spy["regime_short"] = dev >= REGIME_SHORT_MIN
    return data


# ─────────────────────────────────────────────────────────────────────────────
#  EXIT LOGIC (per-day, date-aware; mirrors process_long/short_exits)
# ─────────────────────────────────────────────────────────────────────────────
def _entry_feats(r, srow, atr, price, direction, sector):
    """Signal state at the entry DECISION close. Strictly information that was
    available at that timestamp -- same bar as the decision, one bar before the
    fill. Used for attribution only; never fed back into any decision."""
    def f(x):
        try:
            x = float(x)
            return x if x == x else None
        except Exception:
            return None
    ma50 = f(srow.get("MA50")) if hasattr(srow, "get") else None
    spyc = f(srow.get("Close")) if hasattr(srow, "get") else None
    return {
        "rsi_ent": f(r.get("RSI")),
        "disl_ent": f(r.get("long_disl") if direction == "long" else r.get("short_disl")),
        "atrpct_ent": (f(atr) / f(price)) if f(atr) and f(price) else None,
        "spydev_ent": ((spyc - ma50) / ma50) if (spyc and ma50) else None,
        "sector": sector,
    }


def check_exit(pos, row, today):
    """Exit decision on today's OHLC. Baseline mirrors jp_agent.py exactly; the
    env toggles above alter only the exit *design* for research comparison."""
    h, l = row["High"], row["Low"]
    # Anchor for T1/T2/T3/stop. Baseline uses the reference price (prior close)
    # the order was sized from; ANCHOR_FILL=1 uses the price actually paid.
    entry = (pos.get("fill_price") or pos["entry_price"]) if ANCHOR_FILL else pos["entry_price"]
    total, rem = pos["shares_total"], pos["shares_remaining"]
    t1_hit, t2_hit = pos["t1_hit"], pos["t2_hit"]
    days_held = (today - pos["entry_date"]).days
    days_since_t1 = (today - pos["t1_date"]).days if pos["t1_date"] else 0
    t1lot = max(1, round(total * 0.25))
    t2lot = max(1, round(total * 0.25))
    atr = pos.get("atr") or 0.0
    t3pct = T3_OVR if T3_OVR > 0 else T3_PCT
    action = qty = None

    if pos["direction"] == "long":
        stop = (entry - STOP_ATR * atr) if (STOP_ATR > 0 and atr > 0) else entry * (1 - STOP_LOSS_PCT)
        t1, t2, t3 = entry * (1 + T1_PCT), entry * (1 + T2_PCT), entry * (1 + t3pct)
        pos["peak"] = max(pos.get("peak") or entry, entry, h)
        trail = (pos["peak"] - TRAIL_ATR * atr) if (TRAIL_ATR > 0 and atr > 0) else None
        # With no scale-out, trading above T1 still "arms" the longer time stop.
        reached_t1 = t1_hit or (NO_SCALE_OUT and pos["peak"] >= t1)

        if l <= stop:
            action, qty = "STOP_LOSS", rem
        elif trail is not None and pos["peak"] > entry and l <= trail:
            action, qty = "TRAIL_EXIT", rem
        elif trail is None and h >= t3 and (t2_hit or NO_SCALE_OUT):
            action, qty = "T3_HIT", rem
        elif (not NO_SCALE_OUT) and h >= t2 and t1_hit and not t2_hit:
            action, qty = "T2_HIT", min(t2lot, rem); pos["t2_hit"] = True
        elif (not NO_SCALE_OUT) and h >= t1 and not t1_hit:
            action, qty = "T1_HIT", min(t1lot, rem); pos["t1_hit"] = True; pos["t1_date"] = today
        elif not reached_t1 and days_held >= TIME_STOP_DAYS:
            action, qty = "TIME_STOP", rem
        elif (not NO_SCALE_OUT) and t1_hit and not t2_hit and days_since_t1 >= POST_T1_STOP_DAYS:
            action, qty = "POST_T1_STOP", rem
    else:
        stop = (entry + STOP_ATR * atr) if (STOP_ATR > 0 and atr > 0) else entry * (1 + STOP_LOSS_PCT)
        t1, t2, t3 = entry * (1 - T1_PCT), entry * (1 - T2_PCT), entry * (1 - t3pct)
        pos["peak"] = min(pos.get("peak") or entry, entry, l)
        trail = (pos["peak"] + TRAIL_ATR * atr) if (TRAIL_ATR > 0 and atr > 0) else None
        reached_t1 = t1_hit or (NO_SCALE_OUT and pos["peak"] <= t1)

        if h >= stop:
            action, qty = "STOP_LOSS", rem
        elif trail is not None and pos["peak"] < entry and h >= trail:
            action, qty = "TRAIL_EXIT", rem
        elif trail is None and l <= t3 and (t2_hit or NO_SCALE_OUT):
            action, qty = "T3_HIT", rem
        elif (not NO_SCALE_OUT) and l <= t2 and t1_hit and not t2_hit:
            action, qty = "T2_HIT", min(t2lot, rem); pos["t2_hit"] = True
        elif (not NO_SCALE_OUT) and l <= t1 and not t1_hit:
            action, qty = "T1_HIT", min(t1lot, rem); pos["t1_hit"] = True; pos["t1_date"] = today
        elif not reached_t1 and days_held >= TIME_STOP_DAYS:
            action, qty = "TIME_STOP", rem
        elif (not NO_SCALE_OUT) and t1_hit and not t2_hit and days_since_t1 >= POST_T1_STOP_DAYS:
            action, qty = "POST_T1_STOP", rem
    return (action, qty) if action else (None, None)


# ─────────────────────────────────────────────────────────────────────────────
#  EVENT-DRIVEN SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
def run(start="2019-01-01", end="2026-05-13"):
    print(f"Loading data {start} → {end} ...", file=sys.stderr)
    data = load_data(start, end)
    global DATA_FP
    DATA_FP = data_fingerprint(data)
    spy = data[SPY]
    calendar = list(spy.index)
    print(f"  {len(data)} symbols, {len(calendar)} sessions", file=sys.stderr)

    cash = STARTING_EQUITY
    positions = {}          # sym -> pos dict
    pending = []            # orders decided yesterday, fill at today's open
    equity_curve = []       # {date, pv}
    trades = []             # closed round-trip fragments
    warmup = 60

    def px(sym, d, field):
        df = data.get(sym)
        if df is None or d not in df.index:
            return None
        v = df.at[d, field]
        return float(v) if v == v else None  # NaN guard

    for i, d in enumerate(calendar):
        di = d.date()
        # ── 1. Fill pending orders at today's OPEN (exits before entries) ──
        for o in sorted(pending, key=lambda x: 0 if x["kind"] == "exit" else 1):
            o_open = px(o["symbol"], d, "Open")
            fill = o_open if o_open else o["ref_price"]
            if o["kind"] == "entry":
                positions[o["symbol"]] = {
                    "direction": o["direction"], "entry_date": di,
                    "entry_price": o["entry_price"], "fill_price": fill,
                    "shares_total": o["qty"], "shares_remaining": o["qty"],
                    "t1_hit": False, "t2_hit": False, "t1_date": None,
                    "atr": o.get("atr", 0.0), "peak": o["entry_price"],
                    "feat": o.get("feat", {}),
                    "pos_id": f"{o['symbol']}|{di.isoformat()}|{o['direction'][0].upper()}",
                    "sector": SECTOR_MAP.get(o["symbol"], "?"),
                }
                cash += -fill * o["qty"] if o["direction"] == "long" else fill * o["qty"]
            else:  # exit
                if o["direction"] == "long":
                    cash += fill * o["qty"]
                    pnl = (fill - o["entry_fill"]) * o["qty"]
                else:
                    cash -= fill * o["qty"]
                    pnl = (o["entry_fill"] - fill) * o["qty"]
                trades.append({
                    "symbol": o["symbol"], "direction": o["direction"], "qty": o["qty"],
                    "entry_date": o["entry_date"].isoformat(), "exit_date": di.isoformat(),
                    "entry_px": round(o["entry_fill"], 2), "exit_px": round(fill, 2),
                    "entry_ref": round(o.get("entry_ref") or 0.0, 2),
                    "anchor_err_pct": (round((o["entry_fill"] / o["entry_ref"] - 1) * 100, 3)
                                       if o.get("entry_ref") else None),
                    "gross_pnl": round(pnl, 2), "exit_reason": o["action"],
                    "pos_id": o.get("pos_id"), "shares_total": o.get("shares_total"),
                    **{k: o.get("feat", {}).get(k) for k in
                       ("rsi_ent", "disl_ent", "atrpct_ent", "spydev_ent", "sector")},
                    "hold_days": (di - o["entry_date"]).days,
                    "return_pct": round((pnl / (o["entry_fill"] * o["qty"])) * 100, 2) if o["entry_fill"] else None,
                })
        pending = []

        # ── 2. Mark equity at today's CLOSE ──
        mv = 0.0
        for sym, p in positions.items():
            c = px(sym, d, "Close")
            if c is None:
                continue
            mv += p["shares_remaining"] * c if p["direction"] == "long" else -p["shares_remaining"] * c
        pv = cash + mv
        if i >= warmup:
            equity_curve.append({"date": di.isoformat(), "pv": round(pv, 2)})

        if i < warmup or i == len(calendar) - 1:
            continue

        # ── 3a. Decide EXITS on today's OHLC → fill next open ──
        for sym in list(positions.keys()):
            p = positions[sym]
            row = data[sym].loc[d] if d in data[sym].index else None
            if row is None:
                continue
            action, qty = check_exit(p, row, di)
            if action:
                pending.append({"kind": "exit", "symbol": sym, "direction": p["direction"],
                                "qty": qty, "entry_fill": p["fill_price"],
                                "entry_ref": p["entry_price"],
                                "feat": p.get("feat", {}), "pos_id": p.get("pos_id"),
                                "shares_total": p["shares_total"],
                                "entry_date": p["entry_date"], "action": action,
                                "ref_price": px(sym, d, "Close")})
                p["shares_remaining"] -= qty
                if p["shares_remaining"] <= 0:
                    del positions[sym]

        # ── 3b. Decide ENTRIES on today's close + regime → fill next open ──
        srow = spy.loc[d]
        regime_long, regime_short = bool(srow["regime_long"]), bool(srow["regime_short"])
        n_longs = sum(1 for p in positions.values() if p["direction"] == "long")
        n_shorts = sum(1 for p in positions.values() if p["direction"] == "short")
        sec_long, sec_short = {}, {}
        for p in positions.values():
            m = sec_long if p["direction"] == "long" else sec_short
            m[p["sector"]] = m.get(p["sector"], 0) + 1
        orders = []

        for sym in WATCHLIST:
            if sym in positions or sym not in data or d not in data[sym].index:
                continue
            r = data[sym].loc[d]
            sector = SECTOR_MAP.get(sym, "?")
            price, atr = r["Close"], r["ATR"]
            if price != price or atr != atr or price < MIN_PRICE or atr <= 0:
                pass  # still allow short check below? live returns early → skip both
            valid = not (price != price or atr != atr or price < MIN_PRICE or atr <= 0)

            # LONG
            n_pend_long = sum(1 for o in orders if o["direction"] == "long")
            if valid and regime_long and (n_longs + n_pend_long) < MAX_LONGS:
                psl = sum(1 for o in orders if o["direction"] == "long" and SECTOR_MAP.get(o["symbol"]) == sector)
                if sec_long.get(sector, 0) + psl < MAX_PER_SECTOR:
                    if (r["RSI"] < RSI_OVERSOLD and r["long_disl"] > MIN_LONG_DISL and
                            bool(r["vx_long"]) and bool(r["bull_id"])):
                        sh = calc_shares(pv, round(float(price), 2), float(atr))
                        if sh >= MIN_SHARES:
                            orders.append({"symbol": sym, "qty": sh, "direction": "long",
                                           "entry_price": round(float(price), 2),
                                           "atr": float(atr),
                                           "feat": _entry_feats(r, srow, atr, price, "long", sector)})
            # SHORT
            already_long = any(o["symbol"] == sym and o["direction"] == "long" for o in orders)
            n_pend_short = sum(1 for o in orders if o["direction"] == "short")
            if not LONG_ONLY and valid and regime_short and not already_long and (n_shorts + n_pend_short) < MAX_SHORTS:
                pss = sum(1 for o in orders if o["direction"] == "short" and SECTOR_MAP.get(o["symbol"]) == sector)
                if sec_short.get(sector, 0) + pss < MAX_PER_SECTOR:
                    if (r["RSI"] > RSI_OVERBOUGHT and r["short_disl"] > MIN_SHORT_DISL and
                            bool(r["vx_short"]) and bool(r["bear_id"])):
                        sh = calc_shares(pv, round(float(price), 2), float(atr))
                        if sh >= MIN_SHARES:
                            orders.append({"symbol": sym, "qty": sh, "direction": "short",
                                           "entry_price": round(float(price), 2),
                                           "atr": float(atr),
                                           "feat": _entry_feats(r, srow, atr, price, "short", sector)})

            if n_longs + n_shorts + len(orders) >= MAX_SIMULTANEOUS:
                break

        for o in orders:
            pending.append({"kind": "entry", "symbol": o["symbol"], "direction": o["direction"],
                            "qty": o["qty"], "entry_price": o["entry_price"],
                            "atr": o.get("atr", 0.0), "feat": o.get("feat", {}),
                            "ref_price": o["entry_price"]})

    return equity_curve, trades


def summarize(equity, trades, start, end):
    nopath = BASE / "__nonexistent__.csv"
    m = analytics.compute_all(nopath, nopath, account=None, positions=None,
                              starting_equity=STARTING_EQUITY, equity_override=equity)
    tstats = analytics.trade_stats([
        {"gross_pnl": t["gross_pnl"], "return_pct": t["return_pct"],
         "hold_days": t["hold_days"]} for t in trades])
    final = equity[-1]["pv"] if equity else STARTING_EQUITY
    res = {
        "window": {"start": start, "end": end, "sessions": len(equity)},
        "final_equity": round(final, 2),
        "total_return_pct": round((final / STARTING_EQUITY - 1) * 100, 2),
        "cagr_pct": round((analytics.cagr(equity) or 0) * 100, 2),
        "max_drawdown_pct": round(m["drawdown"]["max_dd"] * 100, 2),
        "volatility_pct": round((m["volatility"]["value"] or 0) * 100, 2) if m["volatility"]["value"] else None,
        "sharpe": round(m["sharpe"]["value"], 2) if m["sharpe"]["value"] else None,
        "sortino": round(m["sortino"]["value"], 2) if m["sortino"]["value"] else None,
        "calmar": round(m["calmar"]["value"], 2) if m["calmar"]["value"] else None,
        "n_trades": len(trades),
        "win_rate_pct": round(tstats["win_rate"] * 100, 1) if tstats["win_rate"] is not None else None,
        "profit_factor": round(tstats["profit_factor"], 2) if tstats["profit_factor"] else None,
        "expectancy": round(tstats["expectancy"], 2) if tstats["expectancy"] is not None else None,
        "avg_hold_days": round(tstats["avg_hold_days"], 1) if tstats["avg_hold_days"] else None,
    }
    return res


def build_manifest(start, end):
    """Everything needed to decide whether two results may be compared."""
    import platform
    import subprocess

    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=str(BASE), capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return "unavailable"

    dirty = git("status", "--porcelain")
    params = {k: v for k, v in sorted(globals().items())
              if k.isupper() and isinstance(v, (int, float, str, bool))
              and k not in ("BASE", "PREFIX", "SPY", "DATA_FP")}
    _, cache_h = _cache_path(start, end)
    return {
        "code_sha": git("rev-parse", "HEAD") or "unavailable",
        "code_dirty": bool(dirty),
        "dirty_files": [l[3:] for l in dirty.splitlines()][:40],
        "data_cache_key": cache_h,
        "data_fingerprint": DATA_FP,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "env_overrides": {k: v for k, v in sorted(os.environ.items())
                          if k.startswith(("P_", "BT_")) or k in
                          ("LONG_ONLY", "ANCHOR_FILL", "STOP_ATR")},
        "params": params,
        "generated_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2019-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-05-13"
    equity, trades = run(start, end)

    pd.DataFrame(equity).to_csv(BASE / f"{PREFIX}_equity.csv", index=False)
    pd.DataFrame(trades).to_csv(BASE / f"{PREFIX}_trades.csv", index=False)
    res = summarize(equity, trades, start, end)
    res["manifest"] = build_manifest(start, end)
    (BASE / f"{PREFIX}_results.json").write_text(json.dumps(res, indent=2))

    print("=" * 60)
    print("  BACKTEST — JP Alpha v3 (frozen logic, out-of-sample vs live)")
    print("=" * 60)
    print(f"  Window          : {res['window']['start']} → {res['window']['end']}  ({res['window']['sessions']} sessions)")
    print(f"  Final equity    : ${res['final_equity']:,.0f}")
    print(f"  Total return    : {res['total_return_pct']:+.2f}%")
    print(f"  CAGR            : {res['cagr_pct']:+.2f}%")
    print(f"  Max drawdown    : {res['max_drawdown_pct']:.2f}%")
    print(f"  Volatility (ann): {res['volatility_pct']}%")
    print(f"  Sharpe          : {res['sharpe']}")
    print(f"  Sortino         : {res['sortino']}")
    print(f"  Calmar          : {res['calmar']}")
    print("-" * 60)
    print(f"  Closed trades   : {res['n_trades']}")
    print(f"  Win rate        : {res['win_rate_pct']}%")
    print(f"  Profit factor   : {res['profit_factor']}")
    print(f"  Expectancy/trade: ${res['expectancy']}")
    print(f"  Avg hold (days) : {res['avg_hold_days']}")
    print("=" * 60)
