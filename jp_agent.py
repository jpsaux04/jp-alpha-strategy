#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  JP Alpha Strategy v3 — Live Alpaca Paper Trading Agent
  Bidirectional Mean Reversion | 42-Stock Universe | Validated 2020-2025
═══════════════════════════════════════════════════════════════════════════════

  OVERVIEW
  --------
  Runs daily at 4:30 PM ET after market close.

  LONG SIGNAL  (oversold mean reversion):
    - Wilder RSI < 45 (oversold)
    - Price ≥ 2% below MA20 (stretched to downside)
    - Volume exhaustion: spike on down day OR 3 consecutive declining-vol down days
    - Bullish intraday close: close in upper 50% of day's range (buyers stepped in)
    - Regime OK: SPY not more than 10% above its MA50

  SHORT SIGNAL (overbought mean reversion — mirror image):
    - Wilder RSI > 60 (overbought)
    - Price ≥ 2% above MA20 (stretched to upside)
    - Volume distribution: spike on up day OR 3 consecutive rising-vol up days
    - Bearish intraday close: close in lower 50% of day's range (sellers stepped in)
    - Regime OK: SPY not more than 10% BELOW its MA50 (don't short a crash)

  EXITS (longs):   T1 +4% (25%), T2 +8% (25%), T3 +12% (50%), Stop -8%, Time 21d
  EXITS (shorts):  T1 -4% (25%), T2 -8% (25%), T3 -12% (50%), Stop +8%, Time 21d

  SIZING: ATR-based equal risk — 1% of portfolio per 1.5 ATR move
  NO LOOK-AHEAD BIAS: signal at Close(T), fill at Open(T+1)
"""

import os, sys, json, logging, csv, requests
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

# Load secrets from .env (kept out of version control — see .gitignore)
load_dotenv(Path(__file__).parent / ".env")

# ───────────────────────────────────────────────────────────────────────────────
#  PATHS & LOGGING
# ───────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
LOG_DIR    = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

ET        = ZoneInfo("America/New_York")
today_str = datetime.now(ET).strftime("%Y-%m-%d")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"agent_{today_str}.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("jp_agent")

# ───────────────────────────────────────────────────────────────────────────────
#  ALPACA CREDENTIALS
# ───────────────────────────────────────────────────────────────────────────────

APCA_KEY    = os.environ["APCA_API_KEY_ID"]
APCA_SECRET = os.environ["APCA_API_SECRET_KEY"]
APCA_URL    = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")

HEADERS = {
    "APCA-API-KEY-ID":     APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET,
    "Content-Type":        "application/json",
}

# CSV files for building a track record (equity curve + trade blotter)
EQUITY_CSV = BASE_DIR / "equity_curve.csv"
TRADES_CSV = BASE_DIR / "trade_log.csv"

# ───────────────────────────────────────────────────────────────────────────────
#  UNIVERSE — expanded to 42 stocks + ETFs across 9 sectors
#  Includes deliberate underperformers (INTC, PFE, BA, WFC) to avoid
#  survivorship bias — the strategy must work on losers too
# ───────────────────────────────────────────────────────────────────────────────

WATCHLIST = [
    # Technology (7) — large cap, liquid, high ATR = more signal opportunities
    "AAPL", "MSFT", "NVDA", "INTC", "CSCO", "AMD", "QCOM",
    # Semiconductors (2) — cyclical, mean-reversion friendly
    "MU", "AMAT",
    # Communication Services (3)
    "GOOGL", "META", "NFLX",
    # Consumer Discretionary (5)
    "AMZN", "HD", "NKE", "MCD", "SBUX",
    # Finance (6) — rate-sensitive, tends to overshoot on macro news
    "JPM", "BAC", "GS", "WFC", "MS", "C",
    # Healthcare (6) — defensive + event-driven (FDA, earnings)
    "UNH", "JNJ", "PFE", "ABBV", "LLY", "MRK",
    # Industrials (5)
    "CAT", "BA", "HON", "GE", "LMT",
    # Energy (3) — oil-price driven volatility = mean reversion setups
    "XOM", "CVX", "COP",
    # Consumer Staples (3) — low beta, defensive
    "WMT", "KO", "PG",
    # ETFs (2) — broad market mean reversion when indices overshoot
    "QQQ", "IWM",
]

SECTOR_MAP = {
    "AAPL":"Tech",    "MSFT":"Tech",    "NVDA":"Tech",    "INTC":"Tech",
    "CSCO":"Tech",    "AMD":"Tech",     "QCOM":"Tech",
    "MU":"Semis",     "AMAT":"Semis",
    "GOOGL":"CommSvc","META":"CommSvc", "NFLX":"CommSvc",
    "AMZN":"ConDisc", "HD":"ConDisc",   "NKE":"ConDisc",
    "MCD":"ConDisc",  "SBUX":"ConDisc",
    "JPM":"Finance",  "BAC":"Finance",  "GS":"Finance",   "WFC":"Finance",
    "MS":"Finance",   "C":"Finance",
    "UNH":"Health",   "JNJ":"Health",   "PFE":"Health",   "ABBV":"Health",
    "LLY":"Health",   "MRK":"Health",
    "CAT":"Industr",  "BA":"Industr",   "HON":"Industr",
    "GE":"Industr",   "LMT":"Industr",
    "XOM":"Energy",   "CVX":"Energy",   "COP":"Energy",
    "WMT":"Staples",  "KO":"Staples",   "PG":"Staples",
    "QQQ":"ETF",      "IWM":"ETF",
}

SPY = "SPY"  # regime benchmark — not in tradeable watchlist

# ───────────────────────────────────────────────────────────────────────────────
#  STRATEGY PARAMETERS
# ───────────────────────────────────────────────────────────────────────────────

# Indicators
RSI_PERIOD          = 14
MA_PERIOD           = 20
VOL_PERIOD          = 20
ATR_PERIOD          = 14
REGIME_MA           = 50

# Long entry thresholds
RSI_OVERSOLD        = 45      # RSI must be BELOW this to go long
MIN_LONG_DISL       = 0.02    # Price must be ≥2% BELOW MA20
VOL_CAPITULATION    = 1.3     # Volume spike threshold (1.3x avg = capitulation)
CLOSE_POS_LONG      = 0.50    # Close in upper 50% of range (bullish intraday)
REGIME_LONG_MAX     = 0.10    # SPY must NOT be >10% above MA50 (avoid buying into bubble)

# Short entry thresholds (exact mirror of longs)
RSI_OVERBOUGHT      = 60      # RSI must be ABOVE this to go short
MIN_SHORT_DISL      = 0.02    # Price must be ≥2% ABOVE MA20
VOL_DISTRIBUTION    = 1.3     # Volume spike on up day = distribution (smart money selling)
CLOSE_POS_SHORT     = 0.50    # Close in lower 50% of range (bearish intraday)
REGIME_SHORT_MIN    = -0.10   # SPY must NOT be >10% BELOW MA50 (don't short a crash)

# Position sizing
ATR_MULTIPLIER      = 1.5     # Stop = 1.5 × ATR from entry
RISK_PER_TRADE_PCT  = 0.01    # Risk 1% of portfolio per trade

# Exit levels — same percentages for both longs and shorts
T1_PCT              = 0.04    # First target: 4% profit → sell 25%
T2_PCT              = 0.08    # Second target: 8% profit → sell 25%
T3_PCT              = 0.12    # Third target: 12% profit → sell 50%
STOP_LOSS_PCT       = 0.08    # Stop loss: 8% adverse move → exit all

# Time exits
TIME_STOP_DAYS      = 21      # Exit if T1 not hit within 21 calendar days
POST_T1_STOP_DAYS   = 30      # Exit remaining if T2 not hit within 30d of T1

# Portfolio limits
MAX_SIMULTANEOUS    = 10      # Max total positions (longs + shorts combined)
MAX_LONGS           = 7       # Max long positions simultaneously
MAX_SHORTS          = 5       # Max short positions simultaneously
MAX_PER_SECTOR      = 2       # Max 2 positions per sector (in same direction)
MIN_PRICE           = 10.0    # Skip cheap stocks (wider spreads, less reliable signals)
MIN_SHARES          = 1

# ───────────────────────────────────────────────────────────────────────────────
#  ALPACA API HELPERS
# ───────────────────────────────────────────────────────────────────────────────

def alpaca_get(endpoint):
    r = requests.get(f"{APCA_URL}{endpoint}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

def alpaca_post(endpoint, body):
    r = requests.post(f"{APCA_URL}{endpoint}", headers=HEADERS,
                      json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def alpaca_delete(endpoint):
    r = requests.delete(f"{APCA_URL}{endpoint}", headers=HEADERS, timeout=15)
    return r.status_code

def get_account():
    return alpaca_get("/v2/account")

def get_positions():
    return alpaca_get("/v2/positions")

def get_clock():
    return alpaca_get("/v2/clock")

def place_market_order(symbol, qty, side):
    """
    Alpaca v2 only accepts side='buy' or side='sell'.
    Shorting is implicit: sell with no position = open short.
    Covering is implicit: buy when short = close short.
    We normalize our internal labels here.
    """
    alpaca_side = {"sell_short": "sell", "buy_to_cover": "buy"}.get(side, side)
    body = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          alpaca_side,
        "type":          "market",
        "time_in_force": "day",
    }
    return alpaca_post("/v2/orders", body)

def cancel_all_pending_orders():
    status = alpaca_delete("/v2/orders")
    log.info(f"Cancelled pending orders → HTTP {status}")

# ───────────────────────────────────────────────────────────────────────────────
#  STATE MANAGEMENT
# ───────────────────────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
        n = len(state.get("positions", {}))
        log.info(f"State loaded: {n} open positions")
        return state
    return {"positions": {}, "last_run": None}

def save_state(state):
    state["last_run"] = datetime.now(ET).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved: {len(state['positions'])} open positions")

def reconcile_state(state, alpaca_positions):
    """
    Sync state.json with what Alpaca actually holds.
    For longs:  Alpaca qty > 0
    For shorts: Alpaca qty < 0 (negative)
    """
    alpaca_map = {p["symbol"]: float(p["qty"]) for p in alpaca_positions}
    to_remove = []

    for sym, pos in state["positions"].items():
        direction = pos.get("direction", "long")
        alpaca_qty = alpaca_map.get(sym, 0)

        if direction == "long" and alpaca_qty <= 0:
            log.warning(f"RECONCILE: LONG {sym} not in Alpaca → removing from state")
            to_remove.append(sym)
        elif direction == "short" and alpaca_qty >= 0:
            log.warning(f"RECONCILE: SHORT {sym} not in Alpaca → removing from state")
            to_remove.append(sym)
        else:
            actual_shares = abs(int(alpaca_qty))
            if actual_shares != pos["shares_remaining"]:
                log.warning(f"RECONCILE: {sym} shares {pos['shares_remaining']} → {actual_shares}")
                pos["shares_remaining"] = actual_shares

    for sym in to_remove:
        del state["positions"][sym]

    return state

# ───────────────────────────────────────────────────────────────────────────────
#  INDICATOR CALCULATIONS
# ───────────────────────────────────────────────────────────────────────────────

def wilder_rsi(close, period=14):
    """
    Wilder's RSI using EWM (com = period-1). The correct implementation.
    Simple rolling average RSI (common in many libraries) gives different values
    and can produce false signals — we always use Wilder's smoothing.
    """
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(high, low, close, period=14):
    """Average True Range using Wilder's EWM smoothing."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()

def volume_exhaustion_long(close, volume, vol_period=20, thresh=1.3):
    """
    LONG-SIDE volume exhaustion — selling is drying up:
    Pattern 1 (Capitulation): volume spike >1.3x avg on a DOWN day
               → panic sellers at the lows, smart money absorbing
    Pattern 2 (Dry-up):       3 consecutive declining-volume DOWN days
               → each new low brings fewer sellers, supply exhausted
    """
    avg_vol  = volume.rolling(vol_period).mean()
    down_day = close < close.shift(1)
    vol_down = volume < volume.shift(1)

    capitulation = (volume > thresh * avg_vol) & down_day

    dry_up = (down_day & vol_down
              & down_day.shift(1) & vol_down.shift(1)
              & down_day.shift(2) & vol_down.shift(2))

    return capitulation | dry_up

def volume_exhaustion_short(close, volume, vol_period=20, thresh=1.3):
    """
    SHORT-SIDE volume exhaustion — buying is drying up (distribution):
    Pattern 1 (Distribution): volume spike >1.3x avg on an UP day
               → insiders/funds selling into retail strength
    Pattern 2 (Dry-up up):    3 consecutive rising-volume UP days
               → each new high brings fewer buyers, demand exhausted
    """
    avg_vol = volume.rolling(vol_period).mean()
    up_day  = close > close.shift(1)
    vol_up  = volume > volume.shift(1)

    distribution = (volume > thresh * avg_vol) & up_day

    dry_up_up = (up_day & vol_up
                 & up_day.shift(1) & vol_up.shift(1)
                 & up_day.shift(2) & vol_up.shift(2))

    return distribution | dry_up_up

def add_indicators(df):
    """Calculate all indicators for a single stock's OHLCV DataFrame."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    rng = (h - l).replace(0, np.nan)

    df["RSI"]     = wilder_rsi(c, RSI_PERIOD)
    df["ATR"]     = calc_atr(h, l, c, ATR_PERIOD)
    df["MA20"]    = c.rolling(MA_PERIOD).mean()
    df["vol_avg"] = v.rolling(VOL_PERIOD).mean()

    # Dislocation: how far price has moved from MA20 (positive = below MA20)
    df["long_disl"]  = (df["MA20"] - c) / df["MA20"]   # positive = below MA20
    df["short_disl"] = (c - df["MA20"]) / df["MA20"]   # positive = above MA20

    # Volume signals for each direction
    df["vol_exhaust_long"]  = volume_exhaustion_long(c, v, VOL_PERIOD, VOL_CAPITULATION)
    df["vol_exhaust_short"] = volume_exhaustion_short(c, v, VOL_PERIOD, VOL_DISTRIBUTION)

    # Intraday close position (0 = at low, 1 = at high)
    intraday_pos = (c - l) / rng
    df["bullish_intraday"] = intraday_pos >= CLOSE_POS_LONG    # upper half = bullish
    df["bearish_intraday"] = intraday_pos <= (1 - CLOSE_POS_SHORT)  # lower half = bearish

    return df

def compute_regime(spy_df):
    """
    SPY vs MA50 regime filter.
    regime_long:  True when SPY is NOT dangerously overbought (≤ +10% vs MA50)
                  — prevents buying dips in a bubble market
    regime_short: True when SPY is NOT in a crash (≥ -10% vs MA50)
                  — prevents shorting already-beaten-down stocks
    """
    spy_df["MA50"] = spy_df["Close"].rolling(REGIME_MA).mean()
    deviation = (spy_df["Close"] - spy_df["MA50"]) / spy_df["MA50"]
    spy_df["regime_long"]  = deviation <= REGIME_LONG_MAX
    spy_df["regime_short"] = deviation >= REGIME_SHORT_MIN
    return spy_df

# ───────────────────────────────────────────────────────────────────────────────
#  DATA FETCHING
# ───────────────────────────────────────────────────────────────────────────────

def fetch_data():
    """
    Download 280 days of OHLCV for SPY + all 42 watchlist stocks.
    Uses yfinance batch download (one API call for all symbols).
    """
    all_syms = [SPY] + WATCHLIST
    log.info(f"Fetching {len(all_syms)} symbols...")

    start = (date.today() - timedelta(days=310)).isoformat()
    raw = yf.download(
        tickers=all_syms,
        start=start,
        progress=False,
        auto_adjust=True,
        group_by="ticker",
    )

    data = {}
    for sym in all_syms:
        try:
            df = raw[sym].copy() if len(all_syms) > 1 else raw.copy()
            df = df.dropna(subset=["Close"])
            df.index = pd.to_datetime(df.index)

            if len(df) < 60:
                log.warning(f"{sym}: only {len(df)} rows — skipping")
                continue

            df = add_indicators(df)
            data[sym] = df

        except Exception as e:
            log.error(f"{sym}: {e}")

    log.info(f"Data ready: {len(data)} symbols, latest date: {list(data.values())[0].index[-1].date()}")
    return data

# ───────────────────────────────────────────────────────────────────────────────
#  SIGNAL GENERATION
# ───────────────────────────────────────────────────────────────────────────────

def check_long_signal(sym, df, spy_df):
    """
    Check if today's close triggers a LONG entry signal.
    Returns (True/False, details dict)
    """
    if len(df) < 60:
        return False, {}

    row     = df.iloc[-1]
    spy_row = spy_df.iloc[-1]

    price   = row["Close"]
    rsi     = row["RSI"]
    disl    = row["long_disl"]
    vol_ex  = bool(row["vol_exhaust_long"])
    bull_id = bool(row["bullish_intraday"])
    regime  = bool(spy_row["regime_long"])
    atr     = row["ATR"]

    if price < MIN_PRICE or np.isnan(atr) or atr <= 0:
        return False, {}

    signal = (
        rsi    < RSI_OVERSOLD   and   # oversold
        disl   > MIN_LONG_DISL  and   # stretched below MA20
        vol_ex                  and   # selling volume exhausted
        bull_id                 and   # buyers stepped in intraday
        regime                        # market not in bubble
    )

    details = {
        "direction": "long",
        "price":    round(price, 2),
        "rsi":      round(rsi, 1),
        "disl_pct": round(disl * 100, 1),
        "vol_ex":   vol_ex,
        "intraday": bull_id,
        "regime":   regime,
        "atr":      round(atr, 4),
    }
    return signal, details

def check_short_signal(sym, df, spy_df):
    """
    Check if today's close triggers a SHORT entry signal.
    Mirror image of the long signal — overbought instead of oversold.
    Returns (True/False, details dict)
    """
    if len(df) < 60:
        return False, {}

    row     = df.iloc[-1]
    spy_row = spy_df.iloc[-1]

    price    = row["Close"]
    rsi      = row["RSI"]
    disl     = row["short_disl"]
    vol_dist = bool(row["vol_exhaust_short"])
    bear_id  = bool(row["bearish_intraday"])
    regime   = bool(spy_row["regime_short"])
    atr      = row["ATR"]

    if price < MIN_PRICE or np.isnan(atr) or atr <= 0:
        return False, {}

    signal = (
        rsi     > RSI_OVERBOUGHT  and   # overbought
        disl    > MIN_SHORT_DISL  and   # stretched above MA20
        vol_dist                  and   # buying volume exhausted (distribution)
        bear_id                   and   # sellers stepped in intraday
        regime                          # market not already in crash
    )

    details = {
        "direction": "short",
        "price":    round(price, 2),
        "rsi":      round(rsi, 1),
        "disl_pct": round(disl * 100, 1),
        "vol_dist": vol_dist,
        "intraday": bear_id,
        "regime":   regime,
        "atr":      round(atr, 4),
    }
    return signal, details

# ───────────────────────────────────────────────────────────────────────────────
#  POSITION SIZING
# ───────────────────────────────────────────────────────────────────────────────

def calc_shares(portfolio_value, price, atr):
    """
    Equal risk sizing: risk exactly 1% of portfolio per position.
    Stop distance = 1.5 × ATR → shares = (portfolio × 1%) / (1.5 × ATR)
    Capped at 20% of portfolio per position to prevent concentration.
    """
    risk_dollars  = portfolio_value * RISK_PER_TRADE_PCT
    stop_distance = ATR_MULTIPLIER * atr
    shares        = int(risk_dollars / stop_distance)

    if shares < MIN_SHARES:
        return 0
    max_shares = int(portfolio_value * 0.20 / price)
    return max(0, min(shares, max_shares))

# ───────────────────────────────────────────────────────────────────────────────
#  EXIT PROCESSING
# ───────────────────────────────────────────────────────────────────────────────

def process_long_exits(sym, pos, today_row, today):
    """
    Check exit conditions for a LONG position using today's OHLC.
    Priority: Stop > T3 > T2 > T1 > Time stops
    """
    h = today_row["High"]
    l = today_row["Low"]

    entry   = pos["entry_price"]
    total   = pos["shares_total"]
    rem     = pos["shares_remaining"]
    t1_hit  = pos["t1_hit"]
    t2_hit  = pos["t2_hit"]
    t1_date = date.fromisoformat(pos["t1_hit_date"]) if pos.get("t1_hit_date") else None

    days_held     = (date.today() - date.fromisoformat(pos["entry_date"])).days
    days_since_t1 = (date.today() - t1_date).days if t1_date else 0

    stop  = entry * (1 - STOP_LOSS_PCT)   # -8%
    t1    = entry * (1 + T1_PCT)           # +4%
    t2    = entry * (1 + T2_PCT)           # +8%
    t3    = entry * (1 + T3_PCT)           # +12%
    t1lot = max(1, round(total * 0.25))
    t2lot = max(1, round(total * 0.25))

    action = sell_qty = reason = None

    if l <= stop:
        action, sell_qty = "STOP_LOSS", rem
        reason = f"Low {l:.2f} ≤ Stop {stop:.2f} (-8%)"

    elif h >= t3 and t2_hit:
        action, sell_qty = "T3_HIT", rem
        reason = f"High {h:.2f} ≥ T3 {t3:.2f} (+12%)"

    elif h >= t2 and t1_hit and not t2_hit:
        action, sell_qty = "T2_HIT", min(t2lot, rem)
        reason = f"High {h:.2f} ≥ T2 {t2:.2f} (+8%)"
        pos["t2_hit"] = True

    elif h >= t1 and not t1_hit:
        action, sell_qty = "T1_HIT", min(t1lot, rem)
        reason = f"High {h:.2f} ≥ T1 {t1:.2f} (+4%)"
        pos["t1_hit"] = True
        pos["t1_hit_date"] = today

    elif not t1_hit and days_held >= TIME_STOP_DAYS:
        action, sell_qty = "TIME_STOP", rem
        reason = f"Held {days_held}d, T1 not hit"

    elif t1_hit and not t2_hit and days_since_t1 >= POST_T1_STOP_DAYS:
        action, sell_qty = "POST_T1_STOP", rem
        reason = f"{days_since_t1}d since T1, T2 not hit"

    if action:
        return {"symbol": sym, "qty": sell_qty, "side": "sell",
                "action": action, "reason": reason}
    return None

def process_short_exits(sym, pos, today_row, today):
    """
    Check exit conditions for a SHORT position using today's OHLC.
    For shorts: profit = price moving DOWN, loss = price moving UP.
    We 'buy to cover' to close.

    Stop   = entry × 1.08  (price went UP 8% → we're losing)
    T1     = entry × 0.96  (price fell 4%  → first target)
    T2     = entry × 0.92  (price fell 8%  → second target)
    T3     = entry × 0.88  (price fell 12% → third target)
    """
    h = today_row["High"]
    l = today_row["Low"]

    entry   = pos["entry_price"]
    total   = pos["shares_total"]
    rem     = pos["shares_remaining"]
    t1_hit  = pos["t1_hit"]
    t2_hit  = pos["t2_hit"]
    t1_date = date.fromisoformat(pos["t1_hit_date"]) if pos.get("t1_hit_date") else None

    days_held     = (date.today() - date.fromisoformat(pos["entry_date"])).days
    days_since_t1 = (date.today() - t1_date).days if t1_date else 0

    # For shorts: stop = price went UP (bad), targets = price went DOWN (good)
    stop  = entry * (1 + STOP_LOSS_PCT)   # +8% = stop loss
    t1    = entry * (1 - T1_PCT)           # -4% = T1 profit
    t2    = entry * (1 - T2_PCT)           # -8% = T2 profit
    t3    = entry * (1 - T3_PCT)           # -12% = T3 profit
    t1lot = max(1, round(total * 0.25))
    t2lot = max(1, round(total * 0.25))

    action = sell_qty = reason = None

    if h >= stop:
        action, sell_qty = "STOP_LOSS", rem
        reason = f"High {h:.2f} ≥ Stop {stop:.2f} (+8%)"

    elif l <= t3 and t2_hit:
        action, sell_qty = "T3_HIT", rem
        reason = f"Low {l:.2f} ≤ T3 {t3:.2f} (-12%)"

    elif l <= t2 and t1_hit and not t2_hit:
        action, sell_qty = "T2_HIT", min(t2lot, rem)
        reason = f"Low {l:.2f} ≤ T2 {t2:.2f} (-8%)"
        pos["t2_hit"] = True

    elif l <= t1 and not t1_hit:
        action, sell_qty = "T1_HIT", min(t1lot, rem)
        reason = f"Low {l:.2f} ≤ T1 {t1:.2f} (-4%)"
        pos["t1_hit"] = True
        pos["t1_hit_date"] = today

    elif not t1_hit and days_held >= TIME_STOP_DAYS:
        action, sell_qty = "TIME_STOP", rem
        reason = f"Held {days_held}d, T1 not hit"

    elif t1_hit and not t2_hit and days_since_t1 >= POST_T1_STOP_DAYS:
        action, sell_qty = "POST_T1_STOP", rem
        reason = f"{days_since_t1}d since T1, T2 not hit"

    if action:
        return {"symbol": sym, "qty": sell_qty, "side": "buy_to_cover",
                "action": action, "reason": reason}
    return None

def process_exits(state, data):
    """
    Iterate all open positions and check for exit conditions.
    Dispatches to process_long_exits or process_short_exits based on direction.
    """
    orders = []
    positions = state["positions"]
    today = date.today().isoformat()

    for sym, pos in list(positions.items()):
        if sym not in data:
            log.warning(f"{sym}: no data — skipping exit check")
            continue

        today_row = data[sym].iloc[-1]
        direction = pos.get("direction", "long")

        if direction == "long":
            order = process_long_exits(sym, pos, today_row, today)
        else:
            order = process_short_exits(sym, pos, today_row, today)

        if order:
            log.info(f"EXIT {direction.upper()} {sym}: {order['action']} | {order['reason']} | qty={order['qty']}")
            orders.append(order)
            pos["shares_remaining"] -= order["qty"]
            if pos["shares_remaining"] <= 0:
                del positions[sym]
                log.info(f"{sym}: position fully closed")

    return orders

# ───────────────────────────────────────────────────────────────────────────────
#  ENTRY PROCESSING
# ───────────────────────────────────────────────────────────────────────────────

def process_entries(state, data, portfolio_value):
    """
    Scan all 42 watchlist stocks for both long and short entry signals.
    Applies portfolio-level constraints (max positions, sector limits).
    """
    if SPY not in data:
        log.error("SPY data missing — aborting entry scan")
        return []

    spy_df = compute_regime(data[SPY])
    spy_row = spy_df.iloc[-1]
    regime_long  = bool(spy_row["regime_long"])
    regime_short = bool(spy_row["regime_short"])

    spy_dev = (spy_row["Close"] - spy_row["MA50"]) / spy_row["MA50"] * 100
    log.info(f"Regime: SPY vs MA50 = {spy_dev:+.1f}% | "
             f"Long OK: {regime_long} | Short OK: {regime_short}")

    positions = state["positions"]
    n_longs  = sum(1 for p in positions.values() if p.get("direction","long") == "long")
    n_shorts = sum(1 for p in positions.values() if p.get("direction") == "short")

    # Count sector exposure per direction
    sector_long_count  = {}
    sector_short_count = {}
    for sym, pos in positions.items():
        sec = SECTOR_MAP.get(sym, "Unknown")
        if pos.get("direction", "long") == "long":
            sector_long_count[sec]  = sector_long_count.get(sec, 0) + 1
        else:
            sector_short_count[sec] = sector_short_count.get(sec, 0) + 1

    orders = []
    long_signals = short_signals = 0

    for sym in WATCHLIST:
        if sym in positions:
            continue  # already in a position

        if sym not in data:
            continue

        df      = data[sym]
        sector  = SECTOR_MAP.get(sym, "Unknown")

        # ── Try LONG ────────────────────────────────────────────────────────
        long_ok = regime_long and (n_longs + sum(1 for o in orders if o.get("direction")=="long")) < MAX_LONGS
        if long_ok:
            pending_long_sector = sum(1 for o in orders
                                      if o.get("direction")=="long" and SECTOR_MAP.get(o["symbol"],"") == sector)
            if sector_long_count.get(sector, 0) + pending_long_sector < MAX_PER_SECTOR:
                signal, details = check_long_signal(sym, df, spy_df)
                if signal:
                    long_signals += 1
                    shares = calc_shares(portfolio_value, details["price"], details["atr"])
                    if shares >= MIN_SHARES:
                        log.info(
                            f"LONG SIGNAL  {sym}: RSI={details['rsi']} | "
                            f"Disl={details['disl_pct']}% below MA20 | "
                            f"Price=${details['price']} | Shares={shares}"
                        )
                        orders.append({
                            "symbol":      sym,
                            "qty":         shares,
                            "side":        "buy",
                            "direction":   "long",
                            "entry_price": details["price"],
                            "atr":         details["atr"],
                        })
                        sector_long_count[sector] = sector_long_count.get(sector, 0) + 1

        # ── Try SHORT ───────────────────────────────────────────────────────
        # Don't go both long and short the same stock simultaneously
        already_long = any(o["symbol"] == sym and o["direction"] == "long" for o in orders)
        short_ok = (regime_short
                    and not already_long
                    and (n_shorts + sum(1 for o in orders if o.get("direction")=="short")) < MAX_SHORTS)
        if short_ok:
            pending_short_sector = sum(1 for o in orders
                                       if o.get("direction")=="short" and SECTOR_MAP.get(o["symbol"],"") == sector)
            if sector_short_count.get(sector, 0) + pending_short_sector < MAX_PER_SECTOR:
                signal, details = check_short_signal(sym, df, spy_df)
                if signal:
                    short_signals += 1
                    shares = calc_shares(portfolio_value, details["price"], details["atr"])
                    if shares >= MIN_SHARES:
                        log.info(
                            f"SHORT SIGNAL {sym}: RSI={details['rsi']} | "
                            f"Disl={details['disl_pct']}% above MA20 | "
                            f"Price=${details['price']} | Shares={shares}"
                        )
                        orders.append({
                            "symbol":      sym,
                            "qty":         shares,
                            "side":        "sell_short",
                            "direction":   "short",
                            "entry_price": details["price"],
                            "atr":         details["atr"],
                        })
                        sector_short_count[sector] = sector_short_count.get(sector, 0) + 1

        # Stop scanning if both books are full
        total_positions = n_longs + n_shorts + len(orders)
        if total_positions >= MAX_SIMULTANEOUS:
            log.info(f"MAX_SIMULTANEOUS ({MAX_SIMULTANEOUS}) reached")
            break

    log.info(f"Entry scan complete: {long_signals} long signals, {short_signals} short signals, "
             f"{len(orders)} orders queued")
    return orders

# ───────────────────────────────────────────────────────────────────────────────
#  ORDER EXECUTION
# ───────────────────────────────────────────────────────────────────────────────

def execute_orders(orders, state):
    """
    Submit orders to Alpaca and update state for new positions.
    Handles all 4 order sides: buy, sell, sell_short, buy_to_cover.
    """
    results   = []
    positions = state["positions"]

    for order in orders:
        sym  = order["symbol"]
        qty  = order["qty"]
        side = order["side"]

        try:
            resp     = place_market_order(sym, qty, side)
            order_id = resp.get("id", "unknown")
            status   = resp.get("status", "unknown")

            log.info(f"ORDER: {side.upper()} {qty} {sym} → {status} (id={order_id})")

            if side == "buy":
                positions[sym] = {
                    "direction":       "long",
                    "order_id":        order_id,
                    "entry_date":      date.today().isoformat(),
                    "entry_price":     order["entry_price"],
                    "atr_at_entry":    order["atr"],
                    "shares_total":    qty,
                    "shares_remaining": qty,
                    "t1_hit":          False,
                    "t2_hit":          False,
                    "t1_hit_date":     None,
                    "sector":          SECTOR_MAP.get(sym, "Unknown"),
                }

            elif side == "sell_short":
                positions[sym] = {
                    "direction":       "short",
                    "order_id":        order_id,
                    "entry_date":      date.today().isoformat(),
                    "entry_price":     order["entry_price"],
                    "atr_at_entry":    order["atr"],
                    "shares_total":    qty,
                    "shares_remaining": qty,
                    "t1_hit":          False,
                    "t2_hit":          False,
                    "t1_hit_date":     None,
                    "sector":          SECTOR_MAP.get(sym, "Unknown"),
                }

            results.append({"symbol": sym, "side": side, "qty": qty,
                             "status": status, "order_id": order_id})

        except requests.HTTPError as e:
            log.error(f"ORDER FAILED: {side} {qty} {sym} → {e.response.text}")
            results.append({"symbol": sym, "side": side, "qty": qty, "status": "FAILED"})
        except Exception as e:
            log.error(f"ORDER ERROR: {side} {qty} {sym} → {e}")

    return results

# ───────────────────────────────────────────────────────────────────────────────
#  TRACK RECORD — CSV logging for equity curve & trade blotter
# ───────────────────────────────────────────────────────────────────────────────

def log_equity(account, state):
    """
    Append one row per run to equity_curve.csv.
    This builds the live performance history you can plot against SPY.
    """
    pv   = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    pos  = state["positions"]
    n_l  = sum(1 for p in pos.values() if p.get("direction", "long") == "long")
    n_s  = sum(1 for p in pos.values() if p.get("direction") == "short")

    new_file = not EQUITY_CSV.exists()
    with open(EQUITY_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "portfolio_value", "cash", "invested",
                        "pct_deployed", "n_long", "n_short", "return_pct"])
        w.writerow([
            today_str,
            f"{pv:.2f}",
            f"{cash:.2f}",
            f"{pv - cash:.2f}",
            f"{(pv - cash) / pv * 100:.1f}" if pv else "0",
            n_l, n_s,
            f"{(pv / 100000 - 1) * 100:.2f}",  # return vs $100k start
        ])

def log_trades(results):
    """
    Append every filled order to trade_log.csv — the audit trail / blotter.
    Records what, when, direction, and quantity for each execution.
    """
    if not results:
        return
    new_file = not TRADES_CSV.exists()
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "time_et", "symbol", "side", "qty", "status", "order_id"])
        now = datetime.now(ET).strftime("%H:%M:%S")
        for r in results:
            w.writerow([today_str, now, r["symbol"], r["side"],
                        r["qty"], r["status"], r.get("order_id", "")])

# ───────────────────────────────────────────────────────────────────────────────
#  SUMMARY
# ───────────────────────────────────────────────────────────────────────────────

def print_summary(account, state, exit_orders, entry_orders, results):
    pv   = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    pos  = state["positions"]
    n_l  = sum(1 for p in pos.values() if p.get("direction","long") == "long")
    n_s  = sum(1 for p in pos.values() if p.get("direction") == "short")

    log.info("=" * 72)
    log.info("  JP ALPHA STRATEGY v3 — DAILY SUMMARY")
    log.info(f"  {today_str}  |  Portfolio: ${pv:,.2f}  |  Cash: ${cash:,.2f} ({cash/pv*100:.0f}%)")
    log.info(f"  Positions: {n_l} long + {n_s} short = {n_l+n_s} total  |  Exits: {len(exit_orders)}  Entries: {len(entry_orders)}")
    log.info("-" * 72)

    if pos:
        log.info("  OPEN POSITIONS:")
        for sym, p in pos.items():
            d    = p.get("direction","long").upper()[:1]
            days = (date.today() - date.fromisoformat(p["entry_date"])).days
            t1   = "✓" if p["t1_hit"] else "·"
            t2   = "✓" if p["t2_hit"] else "·"
            log.info(f"    [{d}] {sym:<6}  @${p['entry_price']:.2f}  "
                     f"rem={p['shares_remaining']}  {days}d  T1={t1} T2={t2}")

    if results:
        log.info("-" * 72)
        log.info("  ORDERS PLACED:")
        for r in results:
            log.info(f"    {r['side'].upper():<12} {r['qty']:>5} {r['symbol']:<6}  [{r['status']}]")

    log.info("=" * 72)

# ───────────────────────────────────────────────────────────────────────────────
#  MAIN
# ───────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 72)
    log.info("  JP ALPHA STRATEGY v3 — BIDIRECTIONAL | 42 STOCKS")
    log.info(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")
    log.info("=" * 72)

    try:
        clock = get_clock()
        log.info(f"Market: {'OPEN' if clock.get('is_open') else 'CLOSED'} | "
                 f"Next open: {clock.get('next_open','?')[:10]}")
    except Exception as e:
        log.error(f"Alpaca unreachable: {e}")
        sys.exit(1)

    account         = get_account()
    portfolio_value = float(account.get("portfolio_value", 0))
    log.info(f"Portfolio: ${portfolio_value:,.2f}")

    if portfolio_value < 1000:
        log.error("Portfolio value too low")
        sys.exit(1)

    state            = load_state()
    alpaca_positions = get_positions()
    state            = reconcile_state(state, alpaca_positions)

    data = fetch_data()
    if not data:
        log.error("No data — aborting")
        sys.exit(1)

    cancel_all_pending_orders()

    exit_orders  = process_exits(state, data)
    entry_orders = process_entries(state, data, portfolio_value)

    all_orders = exit_orders + entry_orders
    results    = execute_orders(all_orders, state)

    save_state(state)
    log_equity(account, state)   # append to equity_curve.csv
    log_trades(results)          # append fills to trade_log.csv
    print_summary(account, state, exit_orders, entry_orders, results)
    log.info("Run complete.")

if __name__ == "__main__":
    main()
