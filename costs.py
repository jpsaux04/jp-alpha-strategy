#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  costs.py — Transaction-Cost Accounting                    (READ-ONLY)
═══════════════════════════════════════════════════════════════════════════════

  Turns the *gross* track record into an honest *net-of-cost* one. Answers the
  question a fund always asks: "what does this look like after real trading
  frictions?"  Three cost layers, all measured from ACTUAL executed fills:

    1. Slippage (implementation shortfall) — for every fill we pull the NBBO
       quote prevailing at the fill timestamp (SIP feed, IEX fallback) and
       measure fill_price vs mid. This is the *real* cost of crossing the
       spread with market orders, not a bps assumption. Fills with no
       retrievable quote fall back to FALLBACK_SLIP_BPS.
    2. Regulatory fees — SEC Section 31 fee + FINRA TAF, on sell-side only,
       per the published schedule.
    3. Short borrow — modeled at BORROW_RATE on the notional × days-held of
       every reconstructed short round-trip (plus still-open shorts to now).

  Round-trips are rebuilt from fills by direction-aware FIFO matching (the same
  reconstruction trade-level analytics uses), so borrow days are real holding
  periods, not guesses.

  GUARANTEES: every Alpaca call is a GET. Places / modifies / cancels ZERO
  orders. Writes only costs.json (a metrics snapshot) — never trading state.
  Imports nothing from jp_agent.py.

  USAGE
    python costs.py            # full run over all fills, writes costs.json
    python costs.py --limit 5  # quote-lookup only first N fills (quick test)
"""

import os
import sys
import json
import time
from collections import deque, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

API = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA = "https://data.alpaca.markets"
H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
     "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}

# ── Cost model parameters (all documented, all overridable) ──────────────────
SEC_FEE_RATE   = 27.8 / 1_000_000   # $ per $ of sell principal (SEC §31, 2026)
TAF_PER_SHARE  = 0.000166           # FINRA TAF, $ per share sold
TAF_CAP        = 8.30               # FINRA TAF cap, $ per trade
BORROW_RATE    = 0.005              # annual borrow, easy-to-borrow large caps
FALLBACK_SLIP_BPS = 5.0             # per-side slippage when no quote available
STARTING_EQUITY   = 100_000.0


# ─────────────────────────────────────────────────────────────────────────────
#  DATA (read-only GETs)
# ─────────────────────────────────────────────────────────────────────────────

def get_fills():
    """All FILL activities, oldest → newest."""
    out, page = [], None
    while True:
        p = {"page_size": 100}
        if page:
            p["page_token"] = page
        j = requests.get(f"{API}/v2/account/activities/FILL",
                         headers=H, params=p, timeout=20).json()
        if not j:
            break
        out += j
        page = j[-1]["id"]
        if len(j) < 100:
            break
    out.sort(key=lambda x: x["transaction_time"])
    return out


def get_nbbo(sym, t_iso):
    """NBBO mid prevailing at (or just before) a fill timestamp. Tries SIP then
    IEX. Returns (mid, spread) or (None, None)."""
    # window: 3s before the fill up to the fill instant → take the last quote
    try:
        end = t_iso
        start = _shift(t_iso, -3)
    except Exception:
        return None, None
    for feed in ("sip", "iex"):
        try:
            r = requests.get(f"{DATA}/v2/stocks/{sym}/quotes", headers=H,
                             params={"start": start, "end": end, "limit": 50,
                                     "feed": feed}, timeout=15)
            if r.status_code != 200:
                continue
            qs = r.json().get("quotes") or []
            if not qs:
                # nothing just before → grab first quote at/after fill
                r2 = requests.get(f"{DATA}/v2/stocks/{sym}/quotes", headers=H,
                                  params={"start": t_iso, "limit": 1, "feed": feed},
                                  timeout=15)
                qs = r2.json().get("quotes") or []
            if not qs:
                continue
            q = qs[-1]
            bp, ap = float(q.get("bp", 0)), float(q.get("ap", 0))
            if bp > 0 and ap > 0 and ap >= bp:
                return (bp + ap) / 2, ap - bp
        except Exception:
            continue
    return None, None


def _shift(t_iso, secs):
    """Shift an ISO-8601 'Z' timestamp by secs, return ISO-8601 'Z'."""
    s = t_iso.rstrip("Z")
    if "." in s:
        s = s[:s.index(".") + 7]  # trim to microseconds
    dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    dt = dt.fromtimestamp(dt.timestamp() + secs, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ─────────────────────────────────────────────────────────────────────────────
#  FIFO ROUND-TRIP RECONSTRUCTION  (direction-aware; drives borrow + analytics)
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct(fills):
    """Match fills into closed round-trips via FIFO. Returns (closed, open_lots).
    closed: list of {symbol,side,qty,entry_t,exit_t,entry_px,exit_px,pnl}.
    open_lots: {symbol: [[signed_qty, px, t], ...]} still open at the end."""
    books = defaultdict(deque)
    closed = []
    for f in fills:
        sym, t = f["symbol"], f["transaction_time"]
        qty, px = float(f["qty"]), float(f["price"])
        signed = qty if f["side"] == "buy" else -qty
        book = books[sym]
        while signed != 0 and book and (book[0][0] > 0) != (signed > 0):
            lot = book[0]
            match = min(abs(lot[0]), abs(signed))
            side = "long" if lot[0] > 0 else "short"
            entry_px = lot[1]
            pnl = (px - entry_px) * match if side == "long" else (entry_px - px) * match
            closed.append({"symbol": sym, "side": side, "qty": match,
                           "entry_t": lot[2], "exit_t": t,
                           "entry_px": entry_px, "exit_px": px, "pnl": pnl})
            lot[0] += -match if lot[0] > 0 else match
            signed += -match if signed > 0 else match
            if lot[0] == 0:
                book.popleft()
        if signed != 0:
            book.append([signed, px, t])
    return closed, books


def _days_between(a_iso, b_iso):
    def p(x):
        s = x.rstrip("Z")
        if "." in s:
            s = s[:s.index(".") + 7]
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return max(0.0, (p(b_iso) - p(a_iso)).total_seconds() / 86400.0)


# ─────────────────────────────────────────────────────────────────────────────
#  COST COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute(limit=None):
    fills = get_fills()
    n = len(fills)
    print(f"fills: {n}  ({fills[0]['transaction_time'][:10]} → "
          f"{fills[-1]['transaction_time'][:10]})", file=sys.stderr)

    gross_notional = sum(abs(float(f["price"]) * float(f["qty"])) for f in fills)
    sell_notional = sum(float(f["price"]) * float(f["qty"]) for f in fills
                        if f["side"] in ("sell", "sell_short"))
    sell_shares = sum(float(f["qty"]) for f in fills
                      if f["side"] in ("sell", "sell_short"))
    n_sells = sum(1 for f in fills if f["side"] in ("sell", "sell_short"))

    # ── 1. Slippage / implementation shortfall (per-fill NBBO) ──
    slip_total, measured, fallback = 0.0, 0, 0
    scan = fills if limit is None else fills[:limit]
    for i, f in enumerate(scan):
        qty, px = float(f["qty"]), float(f["price"])
        mid, _ = get_nbbo(f["symbol"], f["transaction_time"])
        if mid:
            sign = 1.0 if f["side"] == "buy" else -1.0
            slip_total += sign * (px - mid) * qty
            measured += 1
        else:
            slip_total += abs(px * qty) * FALLBACK_SLIP_BPS / 1e4
            fallback += 1
        if (i + 1) % 25 == 0:
            print(f"  ...{i+1}/{len(scan)} quotes", file=sys.stderr)
    # scale a --limit sample up to the full population for a headline estimate
    if limit is not None and scan:
        slip_total *= n / len(scan)

    # ── 2. Regulatory fees (sell-side only) ──
    sec_fee = sell_notional * SEC_FEE_RATE
    taf = min(sell_shares * TAF_PER_SHARE, TAF_CAP * n_sells)
    reg_total = sec_fee + taf

    # ── 3. Short borrow, from reconstructed short round-trips + open shorts ──
    closed, open_lots = reconstruct(fills)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    borrow_total = 0.0
    short_trips = 0
    for tr in closed:
        if tr["side"] == "short":
            d = _days_between(tr["entry_t"], tr["exit_t"])
            borrow_total += tr["entry_px"] * tr["qty"] * BORROW_RATE * d / 360.0
            short_trips += 1
    for sym, lots in open_lots.items():
        for lot in lots:
            if lot[0] < 0:  # still-open short
                d = _days_between(lot[2], now_iso)
                borrow_total += abs(lot[0]) * lot[1] * BORROW_RATE * d / 360.0

    total_cost = slip_total + reg_total + borrow_total

    # ── net-of-cost return ──
    gross_pnl = sum(tr["pnl"] for tr in closed)
    gross_ret_pct = gross_pnl / STARTING_EQUITY * 100
    net_ret_pct = (gross_pnl - total_cost) / STARTING_EQUITY * 100
    cost_bps = total_cost / gross_notional * 1e4 if gross_notional else 0.0

    out = {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "n_fills": n,
        "gross_traded_notional": round(gross_notional, 2),
        "slippage_cost": round(slip_total, 2),
        "slippage_measured_fills": measured,
        "slippage_fallback_fills": fallback,
        "reg_fees": round(reg_total, 2),
        "sec_fee": round(sec_fee, 2),
        "taf": round(taf, 2),
        "borrow_cost": round(borrow_total, 2),
        "short_roundtrips": short_trips,
        "total_cost": round(total_cost, 2),
        "cost_bps_of_notional": round(cost_bps, 2),
        "closed_roundtrips": len(closed),
        "gross_pnl": round(gross_pnl, 2),
        "gross_return_pct": round(gross_ret_pct, 2),
        "net_return_pct": round(net_ret_pct, 2),
        "cost_drag_pct": round(gross_ret_pct - net_ret_pct, 2),
        "params": {"sec_fee_rate": SEC_FEE_RATE, "taf_per_share": TAF_PER_SHARE,
                   "borrow_rate": BORROW_RATE, "fallback_slip_bps": FALLBACK_SLIP_BPS},
    }
    return out


def _print(o):
    print("=" * 60)
    print("  TRANSACTION-COST ACCOUNTING  (read-only)")
    print("=" * 60)
    print(f"  Fills                : {o['n_fills']}")
    print(f"  Gross traded notional: ${o['gross_traded_notional']:,.0f}")
    print("-" * 60)
    print(f"  Slippage (shortfall) : ${o['slippage_cost']:,.2f}  "
          f"({o['slippage_measured_fills']} measured / {o['slippage_fallback_fills']} fallback)")
    print(f"  Regulatory (SEC+TAF) : ${o['reg_fees']:,.2f}")
    print(f"  Short borrow         : ${o['borrow_cost']:,.2f}  "
          f"({o['short_roundtrips']} short round-trips)")
    print(f"  TOTAL COST           : ${o['total_cost']:,.2f}  "
          f"({o['cost_bps_of_notional']:.2f} bps of notional)")
    print("-" * 60)
    print(f"  Closed round-trips   : {o['closed_roundtrips']}")
    print(f"  Gross return         : {o['gross_return_pct']:+.2f}%")
    print(f"  Net return           : {o['net_return_pct']:+.2f}%")
    print(f"  Cost drag            : -{o['cost_drag_pct']:.2f} pts")
    print("=" * 60)


if __name__ == "__main__":
    lim = None
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit") + 1])
    o = compute(limit=lim)
    _print(o)
    if lim is None:
        (BASE / "costs.json").write_text(json.dumps(o, indent=2))
        print(f"\nwrote {BASE/'costs.json'}", file=sys.stderr)
