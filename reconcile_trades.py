#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  reconcile_trades.py — Closed-Trade Ledger Builder  (MONITORING ONLY)
═══════════════════════════════════════════════════════════════════════════════

  Reconstructs a round-trip closed-trade ledger (trades_closed.csv) from
  Alpaca's ACTUAL fill history.

  This module is READ-ONLY with respect to trading:
    • It fetches fill activity (GET /v2/account/activities).
    • It writes a CSV.
    • It NEVER places, modifies, or cancels an order.
    • It imports NOTHING from the strategy engine (jp_agent.py).

  WHY RECONCILIATION INSTEAD OF INLINE LOGGING
  --------------------------------------------
  The agent submits exit orders after the close; they fill at the NEXT
  session's open. The true exit price is therefore unknown at the moment a
  position is closed in the engine. The only accurate source of realized P&L
  is the broker's own fill record — so we rebuild the ledger from Alpaca fills
  using average-cost accounting (the same basis Alpaca uses for avg_entry_price).

  The CSV is fully REWRITTEN each run from the source of truth, which makes the
  operation idempotent and self-healing (no dedup logic, no drift).
"""

import os
import csv
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

CLOSED_TRADES_HEADER = [
    "close_date", "symbol", "direction", "entry_date", "entry_px",
    "exit_px", "qty", "gross_pnl", "return_pct", "hold_days", "exit_reason",
]


def fetch_all_fills(headers, base_url, session=None):
    """Page through /v2/account/activities FILL records (Alpaca returns newest first)."""
    sess = session or requests
    fills = []
    page_token = None
    while True:
        url = f"{base_url}/v2/account/activities?activity_types=FILL&page_size=100"
        if page_token:
            url += f"&page_token={page_token}"
        r = sess.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        fills.extend(batch)
        if len(batch) < 100:
            break
        page_token = batch[-1].get("id")
        if not page_token:
            break
    return fills


def build_closed_trades(fills):
    """
    Average-cost round-trip reconstruction.

    Walk fills per symbol in chronological order, maintaining a signed position
    and an average entry price. Each fill that REDUCES position magnitude
    realizes P&L on the closed portion and emits one closed-trade row. Handles
    partial closes, full closes, and direction flips.
    """
    fills_sorted = sorted(fills, key=lambda a: a.get("transaction_time", ""))

    book = {}   # sym -> {"qty": signed float, "avg": float, "open_time": iso str}
    rows = []

    for a in fills_sorted:
        sym = a.get("symbol")
        if not sym:
            continue
        try:
            qty = float(a.get("qty", 0))
            price = float(a.get("price", 0))
        except (TypeError, ValueError):
            continue
        if qty <= 0 or price <= 0:
            continue

        side = a.get("side", "")
        t = a.get("transaction_time", "")
        signed = qty if side.startswith("buy") else -qty

        st = book.get(sym)
        if st is None or st["qty"] == 0:
            book[sym] = {"qty": signed, "avg": price, "open_time": t}
            continue

        cur = st["qty"]

        # Same direction → add to position, update average cost
        if (cur > 0 and signed > 0) or (cur < 0 and signed < 0):
            new_qty = cur + signed
            st["avg"] = (st["avg"] * abs(cur) + price * abs(signed)) / abs(new_qty)
            st["qty"] = new_qty
            continue

        # Opposite direction → closing (partial / full / flip)
        close_qty = min(abs(cur), abs(signed))
        direction = "long" if cur > 0 else "short"
        entry_px = st["avg"]
        pnl = (price - entry_px) * close_qty if direction == "long" \
            else (entry_px - price) * close_qty
        ret_pct = (pnl / (entry_px * close_qty) * 100) if entry_px else 0.0

        try:
            ed = datetime.fromisoformat(st["open_time"].replace("Z", "+00:00"))
            xd = datetime.fromisoformat(t.replace("Z", "+00:00"))
            entry_date = ed.astimezone(ET).date().isoformat()
            close_date = xd.astimezone(ET).date().isoformat()
            hold_days = (xd.date() - ed.date()).days
        except Exception:
            entry_date = (st["open_time"] or "")[:10]
            close_date = (t or "")[:10]
            hold_days = ""

        rows.append({
            "close_date": close_date,
            "symbol": sym,
            "direction": direction,
            "entry_date": entry_date,
            "entry_px": round(entry_px, 4),
            "exit_px": round(price, 4),
            "qty": int(close_qty) if float(close_qty).is_integer() else close_qty,
            "gross_pnl": round(pnl, 2),
            "return_pct": round(ret_pct, 2),
            "hold_days": hold_days,
            "exit_reason": "",   # not derivable from raw fills; reserved for future
        })

        remaining = cur + signed
        if remaining == 0:
            book[sym] = {"qty": 0, "avg": 0.0, "open_time": None}
        elif (cur > 0) != (remaining > 0):
            # Flipped past flat → new opposite position opens at this fill
            book[sym] = {"qty": remaining, "avg": price, "open_time": t}
        else:
            st["qty"] = remaining   # partial close; avg unchanged

    return rows


def write_closed_trades(rows, path):
    """Full idempotent rewrite from the source of truth (Alpaca fills)."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CLOSED_TRADES_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


def reconcile_and_write(path, headers, base_url, session=None):
    """Fetch fills → rebuild round-trips → write CSV. Returns (n_rows, n_fills)."""
    fills = fetch_all_fills(headers, base_url, session)
    rows = build_closed_trades(fills)
    n = write_closed_trades(rows, path)
    return n, len(fills)


if __name__ == "__main__":
    # Standalone run (also serves as the one-time backfill).
    from dotenv import load_dotenv
    BASE = Path(__file__).parent
    load_dotenv(BASE / ".env")
    H = {
        "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
    }
    U = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
    n, nf = reconcile_and_write(BASE / "trades_closed.csv", H, U)
    print(f"Reconciled {nf} fills -> {n} closed-trade rows -> trades_closed.csv")
