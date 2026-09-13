#!/usr/bin/env python3
"""Adopt broker positions the agent opened but failed to record.

WHY THIS EXISTS
---------------
jp_agent places a market order, polls it ~14s (confirm_order), and refuses to
write state on anything short of a confirmed fill. That refusal is correct --
recording a position that does not exist is far worse than the reverse. But the
order is not cancelled, so when the run happens outside market hours the order
fills at the next open and the position exists at the broker while state.json
has never heard of it. The next run halts on reconciliation divergence, and in
the meantime nothing stops, targets or time-stops that position: jp_agent exits
only what is in state, and this system keeps no resting stop at the broker.

The schedule change (15:45 ET) removes the cause. This removes the damage.

WHAT IT WILL AND WILL NOT DO
----------------------------
Adopts a position ONLY when the broker holding can be tied to a specific FILLED
order that THIS AGENT placed, identified by its own client_order_id tag. It
will not adopt a position it cannot explain, and it will not invent an entry.

It reconstructs atr_at_entry from the signal date encoded in that
client_order_id, using the agent's own add_indicators() on bars up to and
including that date and no later -- the information the agent actually had
(Rule #2). The reconstructed close is printed next to the signal price from the
logs so the two can be eyeballed; they should match to the cent.

Anchor, stop multiple and schema are taken from jp_agent itself, never
re-declared, so an adopted position is indistinguishable from one the agent
recorded normally.

DELIBERATE OMISSION: t1_hit is set False even if the price touched T1 while the
position was unmanaged. The agent did not sell that tranche -- no such fill
exists -- so recording t1_hit=True would make state claim a sale that never
happened and desynchronise shares_remaining from the broker. The missed tranche
is a realised opportunity cost, not something to paper over in state.

Dry-run by default. Pass --commit to write.
"""
import argparse
import json
import sys
from datetime import datetime

import pandas as pd
import yfinance as yf

sys.path.insert(0, "/root/jp_strategy")
from dotenv import load_dotenv
load_dotenv("/root/jp_strategy/.env")

import jp_agent as A  # noqa: E402  (needs .env loaded first)


def filled_entry_orders(symbol):
    """Our own FILLED entry orders for `symbol`, newest first."""
    out = []
    ep = f"/v2/orders?status=closed&limit=500&symbols={symbol}"
    for o in A.alpaca_get(ep) or []:
        coid = o.get("client_order_id") or ""
        if not coid.startswith(f"{A.COID_PREFIX}-{symbol}-"):
            continue
        if "-ENTRY-" not in coid or o.get("status") != "filled":
            continue
        if o.get("side") not in ("buy", "sell_short"):
            continue
        out.append(o)
    out.sort(key=lambda o: o.get("filled_at") or "", reverse=True)
    return out


def signal_date_from_coid(coid):
    """JPV4-{SYM}-{L|S}-{TAG}-{YYYYMMDD}-{seq} → date(YYYY, MM, DD)."""
    parts = coid.split("-")
    return datetime.strptime(parts[-2], "%Y%m%d").date()


def atr_on(symbol, sig_date):
    """atr_at_entry as the agent would have computed it on `sig_date`.

    Truncated to bars <= sig_date BEFORE indicators are calculated. Wilder's EWM
    is causal so this is not strictly necessary, but doing it explicitly means
    the no-look-ahead property is visible rather than argued.
    """
    raw = yf.download(tickers=[symbol],
                      start=(sig_date - pd.Timedelta(days=310)).isoformat(),
                      end=(sig_date + pd.Timedelta(days=1)).isoformat(),
                      progress=False, auto_adjust=True)
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        #  yfinance returns (field, ticker) columns even for a single ticker.
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index)
    df = df[df.index <= sig_date.isoformat()]
    df = A.add_indicators(df)
    last = df.iloc[-1]
    return float(last["ATR"]), float(last["Close"]), df.index[-1].date()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="write state.json (default: dry run)")
    args = ap.parse_args()

    state = A.load_state()
    positions = A.alpaca_get("/v2/positions") or []
    known = state.get("positions", {})

    orphans = [p for p in positions if p["symbol"] not in known]
    if not orphans:
        print("No orphans — broker and state agree.")
        return 0

    adopted, refused = {}, []
    for p in orphans:
        sym = p["symbol"]
        qty = int(float(p["qty"]))
        cur = float(p["current_price"])
        if qty <= 0:
            refused.append((sym, "short or zero qty — not handled, resolve by hand"))
            continue

        orders = filled_entry_orders(sym)
        if not orders:
            refused.append((sym, "no FILLED entry order with our client_order_id — "
                                 "this position was not opened by the agent"))
            continue
        o = orders[0]
        fq = int(float(o["filled_qty"]))
        if fq != qty:
            refused.append((sym, f"broker holds {qty} but the matching order filled "
                                 f"{fq} — partial or multiple fills, resolve by hand"))
            continue

        coid = o["client_order_id"]
        sig = signal_date_from_coid(coid)
        atr, close, bar = atr_on(sym, sig)
        fill_px = float(o["filled_avg_price"])
        fill_dt = o["filled_at"][:10]

        anchor = fill_px if A.ANCHOR_ON_FILL else close
        stop = anchor - A.STOP_ATR_MULT * atr

        print(f"\n{sym}")
        print(f"  order        {coid}")
        print(f"               filled {fq} @ {fill_px} on {fill_dt} "
              f"({o['filled_at'][11:19]}Z)")
        print(f"  signal bar   {bar}  close {close:.2f}  ATR {atr:.4f}")
        print(f"  anchor       {anchor:.2f} "
              f"({'fill' if A.ANCHOR_ON_FILL else 'reference'} price, "
              f"ANCHOR_ON_FILL={A.ANCHOR_ON_FILL})")
        print(f"  stop         {stop:.2f}  ({A.STOP_ATR_MULT}xATR)   "
              f"current {cur:.2f}   cushion {(cur/stop-1)*100:+.2f}%")
        print(f"  targets      T1 {anchor*(1+A.T1_PCT):.2f}  "
              f"T2 {anchor*(1+A.T2_PCT):.2f}  T3 {anchor*(1+A.T3_PCT):.2f}")

        if cur <= stop:
            refused.append((sym, f"current {cur:.2f} is already at/below the "
                                 f"reconstructed stop {stop:.2f} — adopting would "
                                 f"fire an immediate exit; decide by hand"))
            continue

        adopted[sym] = {
            "direction":        "long",
            "order_id":         o["id"],
            "client_order_id":  coid,
            #  The FILL date, not the signal date: this is when the position
            #  economically began, and the time stop should count from it.
            "entry_date":       fill_dt,
            "entry_price":      round(close, 2),
            "fill_price":       (fill_px if A.ANCHOR_ON_FILL else None),
            "atr_at_entry":     round(atr, 4),
            "stop_atr_mult":    A.STOP_ATR_MULT,
            "shares_total":     fq,
            "shares_remaining": fq,
            #  False on purpose even if T1 was touched while unmanaged: no
            #  tranche was sold, so state must not claim one was.
            "t1_hit":           False,
            "t2_hit":           False,
            "t1_hit_date":      None,
            "sector":           A.SECTOR_MAP.get(sym, "Unknown"),
            "adopted_by":       "adopt_orphan_fills.py",
            "adopted_at":       datetime.now(A.ET).isoformat(),
        }

    for sym, why in refused:
        print(f"\nREFUSED {sym}: {why}")

    if not adopted:
        print("\nNothing adopted.")
        return 1

    print("\n" + "=" * 68)
    print("WOULD ADOPT:" if not args.commit else "ADOPTING:")
    print(json.dumps(adopted, indent=2))

    if not args.commit:
        print("\nDry run. Re-run with --commit to write state.json.")
        return 0

    state.setdefault("positions", {}).update(adopted)
    A.save_state(state)
    print(f"\nstate.json written — {len(adopted)} position(s) adopted, "
          f"{len(state['positions'])} open. Previous state backed up in "
          f"state_backups/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
