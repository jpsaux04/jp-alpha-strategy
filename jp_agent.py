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

import os, sys, json, logging, csv, errno, fcntl, atexit, shutil, tempfile, requests
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
LOCK_FILE  = BASE_DIR / ".jp_agent.lock"
STATE_BAK  = BASE_DIR / "state_backups"
STATE_BACKUP_KEEP = 30   # keep the last N state snapshots
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

# Monitoring artifacts (additive — NOT part of the trading logic)
CONFIG_FILE    = BASE_DIR / "config.json"
POSHIST_CSV    = BASE_DIR / "positions_history.csv"
HEARTBEAT_JSON = BASE_DIR / "heartbeat.json"


def load_config():
    """
    Load monitoring config (starting_equity, cashflows).
    Falls back to safe defaults if config.json is absent or unreadable, so the
    agent behaves EXACTLY as before when the file is missing (backward compatible).
    Used only for reporting/return calculations — never for trading decisions.
    """
    defaults = {"starting_equity": 100000.0, "cashflows": []}
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for k in defaults:
                if k in cfg:
                    defaults[k] = cfg[k]
    except Exception as e:
        log.warning(f"config.json unreadable ({e}) — using defaults")
    return defaults

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
#  STRATEGY VERSION GATE  (docs/RESEARCH_REPORT.md — PART 5 §7)
# ───────────────────────────────────────────────────────────────────────────────
#  JP_ALPHA_V3_FROZEN              original live logic. Immutable reference.
#  JP_ALPHA_V4_LONGONLY_STOPATR2   long-only + 2.0x ATR stop + EXEC-2 fill
#                                  anchor fix. Backtest equivalent: research
#                                  variant `lo_atr2` with ANCHOR_FILL=1.
#
#  Override at runtime with STRATEGY_VERSION=JP_ALPHA_V3_FROZEN to restore V3
#  exactly; every behavioural difference below is gated on this flag.
#
#  DEPLOYED AGAINST ADVICE. See docs/RESEARCH_REPORT.md PART 6 — independent
#  methods find no statistically distinguishable edge over passive beta, and
#  Phase 10 shows an honest walk-forward never selects this variant.
STRATEGY_VERSION = os.environ.get("STRATEGY_VERSION", "JP_ALPHA_V4_LONGONLY_STOPATR2")
_V4 = STRATEGY_VERSION == "JP_ALPHA_V4_LONGONLY_STOPATR2"

ALLOW_SHORTS   = not _V4      # V4 is long-only (Phase 7: short book has no edge)
STOP_ATR_MULT  = 2.0 if _V4 else 0.0   # >0 ⇒ ATR stop replaces fixed -8%
ANCHOR_ON_FILL = _V4          # EXEC-2 fix: anchor exits to the actual fill

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

def get_fill_price(order_id, tries=5, delay=2.0):
    """Actual average fill price, polled from the broker.

    EXEC-2: the agent previously recorded only the reference price it sized the
    order from, and anchored every exit level to it. Market orders do not fill
    at the reference price. Returns None if the fill cannot be confirmed, in
    which case the caller falls back to the reference price and the position is
    no worse off than under V3.
    """
    import time
    for i in range(tries):
        try:
            o = alpaca_get(f"/v2/orders/{order_id}")
            p = o.get("filled_avg_price")
            if p:
                return float(p)
            if o.get("status") in ("canceled", "expired", "rejected"):
                return None
        except Exception as e:
            log.warning(f"fill lookup {order_id} attempt {i+1}: {e}")
        time.sleep(delay)
    log.warning(f"fill price unconfirmed for {order_id}; falling back to ref price")
    return None


COID_PREFIX = "JPV4"          # ownership tag; scopes cancellation (EXEC-3)


def make_coid(symbol, direction, tag, seq=0, run_date=None):
    """Deterministic client_order_id (EXEC-1).

    JPV4-{SYM}-{L|S}-{TAG}-{YYYYMMDD}-{seq}

    Deterministic in the run's INTENT, not in wall-clock time, so re-running
    the agent on the same day for the same intent regenerates the same id.
    Alpaca rejects duplicates, which is what makes submission idempotent: a
    cron double-fire or a manual re-run can no longer double-fill.
    """
    d = (run_date or date.today()).strftime("%Y%m%d")
    t = "".join(ch for ch in str(tag).upper() if ch.isalnum())[:12] or "ORDER"
    s = "L" if direction == "long" else "S"
    return f"{COID_PREFIX}-{symbol}-{s}-{t}-{d}-{seq}"


def get_order_by_coid(coid):
    """Fetch an order we already submitted, by client_order_id. None if absent."""
    try:
        return alpaca_get(f"/v2/orders:by_client_order_id?client_order_id={coid}")
    except Exception:
        return None


def place_market_order(symbol, qty, side, coid=None):
    """
    Alpaca v2 only accepts side='buy' or side='sell'.
    Shorting is implicit: sell with no position = open short.
    Covering is implicit: buy when short = close short.
    We normalize our internal labels here.

    EXEC-1: carries a deterministic client_order_id. If the broker rejects it
    as a duplicate, the order already exists and we return THAT order rather
    than submitting a second one.
    """
    alpaca_side = {"sell_short": "sell", "buy_to_cover": "buy"}.get(side, side)
    body = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          alpaca_side,
        "type":          "market",
        "time_in_force": "day",
    }
    if coid:
        body["client_order_id"] = coid
    try:
        return alpaca_post("/v2/orders", body)
    except requests.HTTPError as e:
        dup = (e.response is not None and e.response.status_code in (409, 422)
               and "client_order_id" in (e.response.text or ""))
        if dup and coid:
            log.warning(f"IDEMPOTENT: {coid} already submitted — reusing existing order")
            existing = get_order_by_coid(coid)
            if existing:
                return existing
        raise


def confirm_order(order_id, tries=6, delay=2.0):
    """Poll an order to a decision (EXEC-5, EXEC-7).

    Returns {status, filled_qty, filled_avg_price, terminal}. Never guesses:
    if the broker will not confirm, filled_qty is 0 and terminal is False, and
    the caller must NOT record a position on that basis.
    """
    import time
    last = {"status": "unknown", "filled_qty": 0, "filled_avg_price": None,
            "terminal": False}
    for i in range(tries):
        try:
            o = alpaca_get(f"/v2/orders/{order_id}")
            st = o.get("status", "unknown")
            fq = int(float(o.get("filled_qty") or 0))
            fp = o.get("filled_avg_price")
            last = {"status": st, "filled_qty": fq,
                    "filled_avg_price": float(fp) if fp else None,
                    "terminal": st in ("filled", "canceled", "expired",
                                       "rejected", "done_for_day")}
            if st == "filled" or last["terminal"]:
                return last
        except Exception as e:
            log.warning(f"order confirm {order_id} attempt {i+1}: {e}")
        time.sleep(delay)
    log.warning(f"order {order_id} unconfirmed after {tries} polls "
                f"(status={last['status']}, filled={last['filled_qty']})")
    return last


def get_fill_price_from(conf, fallback=None):
    """Fill price from a confirm_order result, else fallback."""
    return conf.get("filled_avg_price") or fallback


def list_open_orders():
    try:
        return alpaca_get("/v2/orders?status=open&limit=500")
    except Exception as e:
        log.error(f"could not list open orders: {e}")
        return []


def cancel_all_pending_orders():
    """EXEC-3: cancel only orders WE own, identified by client_order_id prefix.

    The previous implementation issued a blanket DELETE /v2/orders, which would
    silently destroy any order placed by a human or another process on the same
    account. Blanket cancellation is still available but must be asked for
    explicitly via EMERGENCY_CANCEL_ALL=1.
    """
    if os.environ.get("EMERGENCY_CANCEL_ALL") == "1":
        status = alpaca_delete("/v2/orders")
        log.warning(f"EMERGENCY blanket cancel of ALL orders → HTTP {status}")
        return

    ours = foreign = 0
    for o in list_open_orders():
        coid = o.get("client_order_id") or ""
        if coid.startswith(COID_PREFIX) or coid.startswith("JPV3"):
            try:
                alpaca_delete(f"/v2/orders/{o['id']}")
                ours += 1
            except Exception as e:
                log.error(f"cancel {o.get('id')} failed: {e}")
        else:
            foreign += 1
    log.info(f"Cancelled {ours} of our pending orders "
             f"({foreign} foreign order(s) left untouched)")

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
    """Durably persist state.json.

    OPS-2: previously this truncated state.json and wrote in place, so a crash
    or a full disk mid-write left a truncated file that load_state() cannot
    parse -- the agent would come up believing it holds no positions while the
    broker holds real risk. Now: write to a temp file in the same directory,
    fsync it, then os.replace() (atomic rename on POSIX). A reader either sees
    the whole old file or the whole new file, never a partial one.

    A timestamped copy of the PREVIOUS good state is kept in state_backups/ so
    a bad state can be rolled back by hand.
    """
    state["last_run"] = datetime.now(ET).isoformat()

    # Back up the previous good state before overwriting it (best effort --
    # never let a backup problem stop us from persisting the new state).
    if STATE_FILE.exists():
        try:
            STATE_BAK.mkdir(exist_ok=True)
            stamp = datetime.now(ET).strftime("%Y%m%dT%H%M%S")
            shutil.copy2(STATE_FILE, STATE_BAK / f"state_{stamp}.json")
            backups = sorted(STATE_BAK.glob("state_*.json"))
            for old in backups[:-STATE_BACKUP_KEEP]:   # prune oldest
                old.unlink()
        except Exception as e:
            log.warning(f"state backup failed (non-fatal): {e}")

    payload = json.dumps(state, indent=2)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(BASE_DIR), prefix=".state.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())          # bytes are on disk before the rename
        os.replace(tmp_path, STATE_FILE)  # atomic swap
        tmp_path = None
        # fsync the directory so the rename itself survives a power loss
        try:
            dfd = os.open(str(BASE_DIR), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    log.info(f"State saved atomically: {len(state['positions'])} open positions")

def reconcile_state(state, alpaca_positions):
    """
    Sync state.json with what Alpaca actually holds, and REPORT divergence.

    EXEC-4: previously this repaired share counts, deleted absent positions and
    said nothing. It never looked at positions the broker holds that the state
    file does not know about, so an orphan was invisible forever.

    EXEC-6: divergence is now classified and returned. The caller decides
    whether to halt. Returns (state, report).
    """
    alpaca_map = {p["symbol"]: float(p["qty"]) for p in alpaca_positions}
    report = {"missing_at_broker": [], "qty_mismatch": [], "orphans": [],
              "direction_conflict": []}
    to_remove = []

    for sym, pos in state["positions"].items():
        direction = pos.get("direction", "long")
        alpaca_qty = alpaca_map.get(sym, 0)

        if direction == "long" and alpaca_qty <= 0:
            log.warning(f"RECONCILE: LONG {sym} not in Alpaca → removing from state")
            report["missing_at_broker"].append(sym)
            to_remove.append(sym)
        elif direction == "short" and alpaca_qty >= 0:
            log.warning(f"RECONCILE: SHORT {sym} not in Alpaca → removing from state")
            report["missing_at_broker"].append(sym)
            to_remove.append(sym)
        else:
            actual_shares = abs(int(alpaca_qty))
            if actual_shares != pos["shares_remaining"]:
                log.warning(f"RECONCILE: {sym} shares {pos['shares_remaining']} → {actual_shares}")
                report["qty_mismatch"].append(
                    {"symbol": sym, "state": pos["shares_remaining"], "broker": actual_shares})
                pos["shares_remaining"] = actual_shares

    for sym in to_remove:
        del state["positions"][sym]

    # EXEC-4: positions the broker holds that we do not know about.
    for sym, qty in alpaca_map.items():
        if sym in state["positions"] or qty == 0:
            continue
        log.error(f"RECONCILE: ORPHAN {sym} qty={qty:g} held at broker but ABSENT from state")
        report["orphans"].append({"symbol": sym, "qty": qty,
                                  "direction": "long" if qty > 0 else "short"})

    state["orphans"] = report["orphans"]
    state["last_reconcile"] = {"at": datetime.now(ET).isoformat(), **report}
    return state, report


def reconcile_is_clean(report):
    """Divergence that must block NEW RISK. Share-count drift is benign (it is
    the normal result of a partial fill) and only adjusts sizing; an orphan or
    a position that vanished at the broker means we do not know our book."""
    return not (report["orphans"] or report["missing_at_broker"]
                or report["direction_conflict"])

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

    # EXEC-2: V4 anchors exit levels to the price actually paid; V3 uses the
    # reference price the order was sized from. See docs/RESEARCH_REPORT.md PART 1.
    entry   = (pos.get("fill_price") or pos["entry_price"]) if ANCHOR_ON_FILL \
              else pos["entry_price"]
    total   = pos["shares_total"]
    rem     = pos["shares_remaining"]
    t1_hit  = pos["t1_hit"]
    t2_hit  = pos["t2_hit"]
    t1_date = date.fromisoformat(pos["t1_hit_date"]) if pos.get("t1_hit_date") else None

    days_held     = (date.today() - date.fromisoformat(pos["entry_date"])).days
    days_since_t1 = (date.today() - t1_date).days if t1_date else 0

    _atr  = pos.get("atr_at_entry") or 0.0
    stop  = (entry - STOP_ATR_MULT * _atr) if (STOP_ATR_MULT > 0 and _atr > 0) \
            else entry * (1 - STOP_LOSS_PCT)
    t1    = entry * (1 + T1_PCT)           # +4%
    t2    = entry * (1 + T2_PCT)           # +8%
    t3    = entry * (1 + T3_PCT)           # +12%
    t1lot = max(1, round(total * 0.25))
    t2lot = max(1, round(total * 0.25))

    action = sell_qty = reason = None

    if l <= stop:
        action, sell_qty = "STOP_LOSS", rem
        reason = (f"Low {l:.2f} ≤ Stop {stop:.2f} "
                  f"({STOP_ATR_MULT}xATR)" if STOP_ATR_MULT > 0 and _atr > 0
                  else f"Low {l:.2f} ≤ Stop {stop:.2f} (-8%)")

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
            # EXEC-5: the optimistic decrement below is PRESERVED on purpose --
            # process_entries skips symbols present in `positions`, so removing
            # it here would silently change same-day re-entry behaviour, which
            # is alpha logic and frozen. Instead we snapshot first, so that
            # execute_orders can roll the position back if the exit does not
            # actually fill. Previously a rejected exit left the state claiming
            # a position was closed while the broker still held it.
            import copy
            order["_snapshot"] = copy.deepcopy(pos)
            order["_intended_qty"] = order["qty"]
            orders.append(order)
            pos["shares_remaining"] -= order["qty"]
            if pos["shares_remaining"] <= 0:
                del positions[sym]
                log.info(f"{sym}: position provisionally closed (pending fill confirmation)")

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

    # EXEC-4: a position the broker holds but state does not know about is still
    # real exposure. It must consume a slot and a sector allowance, and we must
    # not trade its symbol until it is resolved.
    #
    # This is deliberately redundant with the EXEC-6 halt gate in main(), which
    # already prevents this function from running while an orphan exists. Two
    # independent barriers, because the cost of being wrong is an unbounded
    # position and the cost of the redundancy is nine lines.
    orphans = state.get("orphans", []) or []
    orphan_syms = {o["symbol"] for o in orphans}
    for o in orphans:
        if o.get("direction") == "long":
            n_longs += 1
        else:
            n_shorts += 1
    if orphan_syms:
        log.warning(f"ORPHANS counted toward limits and blocked from entry: "
                    f"{', '.join(sorted(orphan_syms))}")

    # Count sector exposure per direction
    sector_long_count  = {}
    sector_short_count = {}
    for sym, pos in positions.items():
        sec = SECTOR_MAP.get(sym, "Unknown")
        if pos.get("direction", "long") == "long":
            sector_long_count[sec]  = sector_long_count.get(sec, 0) + 1
        else:
            sector_short_count[sec] = sector_short_count.get(sec, 0) + 1
    for o in orphans:
        sec = SECTOR_MAP.get(o["symbol"], "Unknown")
        if o.get("direction") == "long":
            sector_long_count[sec]  = sector_long_count.get(sec, 0) + 1
        else:
            sector_short_count[sec] = sector_short_count.get(sec, 0) + 1

    orders = []
    long_signals = short_signals = 0

    for sym in WATCHLIST:
        if sym in positions:
            continue  # already in a position
        if sym in orphan_syms:
            continue  # EXEC-4: unreconciled broker position in this symbol

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
        short_ok = (ALLOW_SHORTS
                    and regime_short
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
    Submit orders to Alpaca and update state FROM CONFIRMED FILLS ONLY.
    Handles all 4 order sides: buy, sell, sell_short, buy_to_cover.

    EXEC-5: previously a position was written immediately after submission and
    the returned `status` was logged but never branched on, so a rejected order
    still created a position in state. Now nothing is written unless the broker
    confirms a fill.
    EXEC-7: partial fills are honoured -- the position records the quantity
    actually filled, not the quantity requested.
    EXEC-1: every order carries a deterministic client_order_id.
    """
    results   = []
    positions = state["positions"]

    for seq, order in enumerate(orders):
        sym  = order["symbol"]
        qty  = order["qty"]
        side = order["side"]
        direction = order.get("direction", "long" if side in ("buy", "sell") else "short")
        tag  = order.get("action") or ("ENTRY" if side in ("buy", "sell_short") else "EXIT")
        coid = make_coid(sym, direction, tag, seq)

        def _rollback(reason):
            """EXEC-5: restore the position process_exits optimistically closed."""
            snap = order.get("_snapshot")
            if snap is not None:
                positions[sym] = snap
                log.error(f"ROLLBACK {sym}: exit did not fill ({reason}) — "
                          f"position restored to {snap.get('shares_remaining')} shares")

        try:
            resp     = place_market_order(sym, qty, side, coid=coid)
            order_id = resp.get("id", "unknown")
            sub_stat = resp.get("status", "unknown")
            log.info(f"ORDER: {side.upper()} {qty} {sym} → {sub_stat} "
                     f"(id={order_id}, coid={coid})")

            conf   = confirm_order(order_id)
            filled = conf["filled_qty"]
            status = conf["status"]

            if filled <= 0:
                log.error(f"NO FILL: {side} {qty} {sym} → status={status} — "
                          f"state NOT updated")
                _rollback(status)
                results.append({"symbol": sym, "side": side, "qty": 0,
                                "requested_qty": qty, "status": f"NOFILL_{status}",
                                "order_id": order_id, "client_order_id": coid})
                continue

            if filled < qty:
                log.warning(f"PARTIAL FILL: {sym} {filled}/{qty} — "
                            f"reconciling state to actual")

            fill_px = get_fill_price_from(conf, order.get("entry_price"))

            if side in ("buy", "sell_short"):
                is_long = side == "buy"
                positions[sym] = {
                    "direction":       "long" if is_long else "short",
                    "order_id":        order_id,
                    "client_order_id": coid,
                    "entry_date":      date.today().isoformat(),
                    "entry_price":     order["entry_price"],
                    "fill_price":      (fill_px if ANCHOR_ON_FILL else None),
                    "atr_at_entry":    order["atr"],
                    "shares_total":    filled,
                    "shares_remaining": filled,
                    "t1_hit":          False,
                    "t2_hit":          False,
                    "t1_hit_date":     None,
                    "sector":          SECTOR_MAP.get(sym, "Unknown"),
                }
            else:
                # EXIT: process_exits already decremented by the INTENDED qty.
                # If the fill was partial, undo and re-apply the actual amount.
                intended = order.get("_intended_qty", qty)
                if filled < intended:
                    snap = order.get("_snapshot")
                    if snap is not None:
                        restored = dict(snap)
                        restored["shares_remaining"] = max(
                            0, snap.get("shares_remaining", 0) - filled)
                        if restored["shares_remaining"] > 0:
                            positions[sym] = restored
                            log.warning(f"{sym}: partial exit {filled}/{intended} — "
                                        f"{restored['shares_remaining']} shares still open")
                        else:
                            positions.pop(sym, None)

            results.append({"symbol": sym, "side": side, "qty": filled,
                            "requested_qty": qty, "status": status,
                            "fill_price": fill_px,
                            "order_id": order_id, "client_order_id": coid})

        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else str(e)
            log.error(f"ORDER FAILED: {side} {qty} {sym} → {body}")
            _rollback("http_error")
            results.append({"symbol": sym, "side": side, "qty": 0,
                            "requested_qty": qty, "status": "FAILED",
                            "client_order_id": coid})
        except Exception as e:
            log.error(f"ORDER ERROR: {side} {qty} {sym} → {e}")
            _rollback("exception")
            results.append({"symbol": sym, "side": side, "qty": 0,
                            "requested_qty": qty, "status": "ERROR",
                            "client_order_id": coid})

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

    # Starting equity comes from config.json (default 100000.0 → identical to before)
    start_eq = load_config().get("starting_equity", 100000.0) or 100000.0

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
            f"{(pv / start_eq - 1) * 100:.2f}",  # return vs configured start equity
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


def log_positions_history(alpaca_positions, state):
    """
    Append a daily snapshot of every currently-held position (one row each) to
    positions_history.csv. Uses Alpaca's real current price / unrealized P&L,
    enriched with our state (entry_date, direction, T1/T2 flags).

    MONITORING ONLY — reads data, writes a CSV. No orders, no state mutation.
    """
    if not alpaca_positions:
        return
    sp = state.get("positions", {})
    new_file = not POSHIST_CSV.exists()
    with open(POSHIST_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "symbol", "direction", "qty", "entry_px", "current_px",
                        "unrealized_pl", "unrealized_plpc", "days_held", "t1_hit", "t2_hit"])
        for p in alpaca_positions:
            sym  = p.get("symbol")
            meta = sp.get(sym, {})
            qty  = float(p.get("qty", 0))
            direction = meta.get("direction", "long" if qty >= 0 else "short")
            entry_px  = meta.get("entry_price", p.get("avg_entry_price", ""))
            ed = meta.get("entry_date")
            try:
                days_held = (date.today() - date.fromisoformat(ed)).days if ed else ""
            except Exception:
                days_held = ""
            plpc = p.get("unrealized_plpc")
            plpc_str = f"{float(plpc) * 100:.2f}" if plpc not in (None, "") else ""
            w.writerow([
                today_str, sym, direction, abs(int(qty)),
                entry_px, p.get("current_price", ""),
                p.get("unrealized_pl", ""), plpc_str, days_held,
                bool(meta.get("t1_hit", False)), bool(meta.get("t2_hit", False)),
            ])


def write_heartbeat(account, results, result="RUN_OK"):
    """
    Write heartbeat.json at the end of a successful run so external monitors
    (dashboard banner, dead-man's-switch) can detect liveness and staleness.

    MONITORING ONLY — writes a status file. No orders, no state mutation.
    """
    submitted = len(results)
    # EXEC-5/7: a "no fill" is not a fill. Count what the broker actually did,
    # not what we asked for.
    filled    = sum(1 for r in results if (r.get("qty") or 0) > 0)
    partial   = sum(1 for r in results
                    if 0 < (r.get("qty") or 0) < (r.get("requested_qty") or 0))
    rejected  = submitted - filled
    hb = {
        "last_run_ts":        datetime.now(ET).isoformat(),
        "result":             result,
        "n_orders_submitted": submitted,
        "n_orders_filled":    filled,
        "n_orders_partial":   partial,
        "n_orders_rejected":  rejected,
        "n_errors":           rejected,
        "equity":             float(account.get("portfolio_value", 0)),
    }
    with open(HEARTBEAT_JSON, "w") as f:
        json.dump(hb, f, indent=2)

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
#  SINGLE-INSTANCE LOCK
# ───────────────────────────────────────────────────────────────────────────────
#
#  OPS-1: cron chains the four jobs with ';', so an overrunning agent run is
#  never waited for. If a run stalls (slow yfinance fetch, broker timeout) past
#  the next trigger, two processes would read the same state.json, both see the
#  same flat book, and both submit the SAME entry orders -- double size, real
#  money. An exclusive flock on .jp_agent.lock makes a second instance exit
#  immediately and loudly instead. The lock is advisory and held only for the
#  lifetime of the process; the OS releases it even on SIGKILL, so there is no
#  stale-lock class of failure.

_LOCK_FH = None   # module-level: keeps the fd (and thus the lock) alive


def acquire_lock():
    """Take the exclusive run lock, or exit(9) if another run holds it."""
    global _LOCK_FH
    fh = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        try:
            fh.seek(0)
            holder = fh.read().strip() or "unknown"
        except Exception:
            holder = "unknown"
        fh.close()
        log.error("=" * 72)
        log.error("  ANOTHER jp_agent RUN IS ALREADY IN PROGRESS "
                  f"(lock held by: {holder})")
        log.error("  Refusing to start: concurrent runs can double-submit "
                  "orders against the same state.")
        log.error(f"  Lock file: {LOCK_FILE}")
        log.error("  Override with --no-lock ONLY if you are certain no other "
                  "run is live.")
        log.error("=" * 72)
        sys.exit(9)

    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} started={datetime.now(ET).isoformat()}\n")
    fh.flush()
    _LOCK_FH = fh
    log.info(f"Run lock acquired (pid {os.getpid()})")

    def _release():
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        except Exception:
            pass
    atexit.register(_release)


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
    state, rec_report = reconcile_state(state, alpaca_positions)

    # EXEC-6: divergence between broker and state means we do not know our own
    # book. Exits still run -- we must ALWAYS be able to reduce risk -- but no
    # new risk is taken until reconciliation is clean.
    entries_halted = not reconcile_is_clean(rec_report)
    if entries_halted:
        log.error("=" * 72)
        log.error("  RECONCILIATION DIVERGENCE — NEW ENTRIES HALTED")
        for o in rec_report["orphans"]:
            log.error(f"    ORPHAN  {o['symbol']} qty={o['qty']:g} ({o['direction']})")
        for s in rec_report["missing_at_broker"]:
            log.error(f"    MISSING {s} in state but not at broker")
        log.error("  Exits will still be processed. Resolve before next run.")
        log.error("=" * 72)

    data = fetch_data()
    if not data:
        log.error("No data — aborting")
        sys.exit(1)

    cancel_all_pending_orders()

    exit_orders  = process_exits(state, data)
    entry_orders = [] if entries_halted else process_entries(state, data, portfolio_value)
    if entries_halted:
        log.warning("Entry scan SKIPPED (reconciliation divergence)")

    all_orders = exit_orders + entry_orders
    results    = execute_orders(all_orders, state)

    save_state(state)
    log_equity(account, state)   # append to equity_curve.csv
    log_trades(results)          # append fills to trade_log.csv

    # ── Monitoring artifacts (Phase 0) ──────────────────────────────────────
    # All of the following run AFTER trading is complete (orders submitted,
    # state saved). Each is wrapped so a monitoring failure can NEVER abort a
    # run or affect trading. They only read data and write CSV/JSON files.
    try:
        log_positions_history(alpaca_positions, state)
    except Exception as e:
        log.error(f"positions_history logging failed (non-fatal): {e}")
    try:
        from reconcile_trades import reconcile_and_write
        n_rt, n_fills = reconcile_and_write(BASE_DIR / "trades_closed.csv", HEADERS, APCA_URL)
        log.info(f"Closed-trade ledger rebuilt: {n_rt} round-trips from {n_fills} fills")
    except Exception as e:
        log.error(f"trade reconciliation failed (non-fatal): {e}")
    try:
        write_heartbeat(account, results, "RUN_OK")
    except Exception as e:
        log.error(f"heartbeat write failed (non-fatal): {e}")

    print_summary(account, state, exit_orders, entry_orders, results)
    log.info("Run complete.")

if __name__ == "__main__":
    # OPS-1: single-instance guard. --no-lock is a deliberate escape hatch for
    # manual/offline invocations; the cron path must never use it.
    if "--no-lock" in sys.argv:
        log.warning("--no-lock given: single-instance guard DISABLED")
    else:
        acquire_lock()
    main()
