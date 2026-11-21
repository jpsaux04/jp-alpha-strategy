#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  aftertax.py — what this account would ACTUALLY have kept   (READ-ONLY)
═══════════════════════════════════════════════════════════════════════════════

  The dashboard reports gross-of-cost, pre-tax returns. For a paper account
  that is the honest number. For a REAL one it is not the number you keep, and
  the gap is structural rather than incidental for this strategy:

    • Median hold is 13 days and nothing has ever been held a year, so every
      realised gain is SHORT-TERM — ordinary income rates, not 0/15/20%.
    • Tiered exits (T1/T2/T3) realise gains EARLY by design. The strategy is
      about as tax-inefficient as a long-only equity book can be.
    • The benchmark does not share that problem. SPY bought and held defers
      its entire gain; comparing a strategy that realises every two weeks
      against a buy-and-hold index PRE-tax flatters the strategy.

  So the comparison here is like-for-like: both sides liquidated on the same
  day, both taxed on what that liquidation would realise.

  WHAT IS MODELLED
    1. FIFO lot accounting (the IRS default absent specific identification).
    2. Holding period per lot → short vs long term. Short SALES are always
       short-term regardless of how long they were open (IRC §1233).
    3. Wash sales: a loss is disallowed where substantially identical stock is
       acquired within 30 days either side of the disposal, and the disallowed
       amount is added to the replacement lot's basis rather than discarded.
    4. Per-tax-year netting, the $3,000 capital-loss limitation against
       ordinary income, and loss carryforward between years.
    5. Deferred liability on open positions: tax that would fall due if the
       book were liquidated at today's marks.
    6. Frictional costs from costs.py (NBBO slippage, SEC §31, FINRA TAF,
       borrow), deducted BEFORE tax, as they would be.

  WHAT IS NOT
    Estimated-tax penalties, NIIT, AMT, state-specific capital-loss rules,
    §1091 across substantially-identical-but-not-identical securities, and the
    taxpayer's other income. Rates are assumptions, declared in config.json
    and shown on the page. A single asserted rate would be a worse answer than
    a stated one plus the sensitivity table this module also produces.

  Read-only: no orders, no state mutation, every broker call a GET.
"""
import json
import os
import sys
from collections import defaultdict, deque
from datetime import datetime, date
from pathlib import Path

BASE = Path(__file__).parent
ET_DAY = "%Y-%m-%d"

#  Declared, not hidden. Overridable in config.json -> {"tax": {...}}.
DEFAULT_RATES = {
    "federal_short_term": 0.24,   # ordinary-income bracket assumption
    "federal_long_term": 0.15,
    "state": 0.05,                # MA flat rate on capital gains
    "qualified_dividend": 0.15,
    "label": "US resident · 24% federal ordinary + 5% state (assumption)",
}

#  Scenarios shown beside the headline so no single assumption carries the
#  whole claim. A reader can find their own row.
SENSITIVITY = [
    ("non-resident, treaty-exempt", 0.00, 0.00),
    ("15% fed + 0% state", 0.15, 0.00),
    ("24% fed + 5% state (default)", 0.24, 0.05),
    ("32% fed + 5% state", 0.32, 0.05),
    ("37% fed + 13.3% state", 0.37, 0.133),
]

WASH_WINDOW_DAYS = 30
LOSS_OFFSET_LIMIT = 3000.0        # IRC §1211(b): net capital loss vs ordinary


def _d(iso):
    """ISO timestamp -> date. Fills are UTC; dates are close enough for term."""
    s = str(iso)[:10]
    return datetime.strptime(s, ET_DAY).date()


# ─────────────────────────────────────────────────────────────────────────────
#  1. FIFO LOT ACCOUNTING
# ─────────────────────────────────────────────────────────────────────────────
def build_disposals(fills):
    """FIFO-match fills into disposals. Returns (disposals, open_lots).

    Average-cost (what reconcile_trades.py uses for P&L reporting) is not a
    permitted basis method for equities. FIFO is the default where no lot was
    specifically identified, so FIFO is what a real 1099-B would show, and the
    two can disagree on WHICH gain is short-term even when the totals agree.
    """
    by_sym = defaultdict(list)
    for f in sorted(fills, key=lambda a: a.get("transaction_time", "")):
        try:
            qty, px = float(f["qty"]), float(f["price"])
        except (TypeError, ValueError):
            continue
        if qty <= 0 or px <= 0:
            continue
        by_sym[f["symbol"]].append(
            (f.get("transaction_time", ""), f.get("side", ""), qty, px))

    disposals, open_lots = [], []
    for sym, evts in by_sym.items():
        longs, shorts = deque(), deque()   # each: [qty, px, date, acq_id]
        for acq_id, (t, side, qty, px) in enumerate(evts):
            d = _d(t)
            if side == "buy":
                #  A buy first covers any open short, then opens a long lot.
                while qty > 1e-9 and shorts:
                    lot = shorts[0]
                    take = min(qty, lot[0])
                    disposals.append({
                        "symbol": sym, "qty": take, "direction": "short",
                        "open_date": lot[2], "close_date": d,
                        "basis": lot[1] * take, "proceeds": px * take,
                        #  Short gain = sale proceeds - cover cost.
                        "pnl": (lot[1] - px) * take,
                        "days": (d - lot[2]).days,
                        #  IRC §1233: gain or loss on a short sale is ALWAYS
                        #  short-term, however long the position was open.
                        "term": "short",
                    })
                    lot[0] -= take
                    qty -= take
                    if lot[0] <= 1e-9:
                        shorts.popleft()
                if qty > 1e-9:
                    longs.append([qty, px, d, (sym, acq_id)])
            elif side in ("sell", "sell_short"):
                while qty > 1e-9 and longs:
                    lot = longs[0]
                    take = min(qty, lot[0])
                    days = (d - lot[2]).days
                    disposals.append({
                        "symbol": sym, "qty": take, "direction": "long",
                        "open_date": lot[2], "close_date": d,
                        "basis": lot[1] * take, "proceeds": px * take,
                        "pnl": (px - lot[1]) * take,
                        "days": days, "acq_id": lot[3],
                        "acq_consumed": take,
                        #  >1 year is long-term; the day count is inclusive of
                        #  the disposal date, hence > 365 not >= 365.
                        "term": "long" if days > 365 else "short",
                    })
                    lot[0] -= take
                    qty -= take
                    if lot[0] <= 1e-9:
                        longs.popleft()
                if qty > 1e-9:
                    shorts.append([qty, px, d, (sym, acq_id)])
        for q, px, d, _a in longs:
            open_lots.append({"symbol": sym, "qty": q, "px": px, "date": d,
                              "direction": "long"})
        for q, px, d, _a in shorts:
            open_lots.append({"symbol": sym, "qty": -q, "px": px, "date": d,
                              "direction": "short"})
    disposals.sort(key=lambda r: r["close_date"])
    return disposals, open_lots


# ─────────────────────────────────────────────────────────────────────────────
#  2. WASH SALES
# ─────────────────────────────────────────────────────────────────────────────
def apply_wash_sales(disposals, fills):
    """Disallow losses replaced within ±30 days. Mutates and returns disposals.

    This strategy re-enters the same names repeatedly -- NKE, GOOGL, SBUX and
    AMZN all appear several times -- so the rule bites here. A disallowed loss
    is not lost: it is added to the replacement lot's basis and recovered when
    THAT lot is sold. What changes is WHEN, and at a year end that is the
    difference between a deduction and a deferral.

    THE SUBTLETY, which a first pass here got wrong: the shares you just sold
    are not their own replacement. The acquisition that FIFO consumed to make
    this disposal must be excluded from the replacement count, or every
    ordinary losing trade opened and closed inside 30 days -- which, at a
    13-day median hold, is nearly all of them -- is misread as a wash sale.
    Shares from that same purchase that remain UNSOLD do still count, as does
    any other purchase in the window: that is the "doubling up" the before-leg
    of the rule exists to catch.
    """
    buys = defaultdict(list)                 # sym -> [(date, qty, acq_id)]
    for sym_evts in (fills,):
        pass
    by_sym = defaultdict(list)
    for f in sorted(fills, key=lambda a: a.get("transaction_time", "")):
        by_sym[f.get("symbol")].append(f)
    for sym, evts in by_sym.items():
        for acq_id, f in enumerate(evts):
            if f.get("side") != "buy":
                continue
            try:
                buys[sym].append((_d(f["transaction_time"]), float(f["qty"]),
                                  (sym, acq_id)))
            except (TypeError, ValueError, KeyError):
                continue

    for r in disposals:
        r["wash_disallowed"] = 0.0
        if r["pnl"] >= 0 or r["direction"] != "long":
            continue
        sold = r["qty"]
        replaced = 0.0
        for bd, bq, aid in buys.get(r["symbol"], []):
            if abs((bd - r["close_date"]).days) > WASH_WINDOW_DAYS:
                continue
            avail = bq
            if aid == r.get("acq_id"):
                #  Not a replacement: this is the very purchase being sold.
                avail -= r.get("acq_consumed", 0.0)
            if avail > 0:
                replaced += avail
        if replaced > 0:
            frac = min(1.0, replaced / sold) if sold else 0.0
            r["wash_disallowed"] = round(-r["pnl"] * frac, 2)
    return disposals


def apply_basis_recovery(disposals):
    """A disallowed loss is DEFERRED, not destroyed. Give it back.

    §1091 disallows the loss and adds it to the basis of the replacement
    shares, so it is recovered when those shares are sold. A model that only
    disallows -- which this module did on its first pass -- books the penalty
    and never the refund, and reports a permanently higher tax bill than any
    real filer would pay. Over a closed-out chain of trades in one name the
    two legs should very nearly cancel; what survives to a year end is timing,
    which is the real cost of the rule.

    Modelled as a per-symbol pool rather than true lot-level basis tracking:
    a disallowed loss goes into the pool and is drawn down against the next
    disposals in the same name. The two agree whenever the replacement lot is
    the one subsequently sold, which under FIFO it is. Anything left in the
    pool at the end is still embedded in the basis of shares still held, and
    is returned so the deferred-liability calculation can use it.

    Sets `taxable_pnl` on every disposal. Returns the residual pool.
    """
    from collections import defaultdict as _dd
    #  amount still deferred, and how many replacement shares carry it, so a
    #  lot sold in pieces recovers its basis in pieces.
    pool = _dd(lambda: {"amt": 0.0, "shares": 0.0})
    for r in sorted(disposals, key=lambda x: (x["close_date"], x["symbol"])):
        sym = r["symbol"]
        taxable = r["pnl"] + r.get("wash_disallowed", 0.0)
        if r.get("wash_disallowed", 0.0) > 0:
            pool[sym]["amt"] += r["wash_disallowed"]
            pool[sym]["shares"] += r["qty"]
        elif pool[sym]["amt"] > 1e-9:
            p = pool[sym]
            #  Apply the whole per-share adjustment carried by the shares being
            #  sold. It is a BASIS change, so it applies in full and may turn a
            #  gain into a loss -- capping it at zero (which this module did at
            #  first) silently keeps part of the deduction forever.
            frac = min(1.0, r["qty"] / p["shares"]) if p["shares"] else 1.0
            use = p["amt"] * frac
            taxable -= use
            p["amt"] -= use
            p["shares"] = max(0.0, p["shares"] - r["qty"])
        r["taxable_pnl"] = taxable
    return {k: v["amt"] for k, v in pool.items() if v["amt"] > 1e-9}


# ─────────────────────────────────────────────────────────────────────────────
#  3. TAX BY YEAR
# ─────────────────────────────────────────────────────────────────────────────
def tax_by_year(disposals, rates):
    """Net ST/LT per tax year, apply the §1211(b) limit, carry losses forward."""
    years = defaultdict(lambda: {"st": 0.0, "lt": 0.0, "disallowed": 0.0})
    for r in disposals:
        y = r["close_date"].year
        #  taxable_pnl carries both legs of §1091: the loss disallowed this
        #  year, and losses deferred from earlier ones coming back as basis.
        pnl = r.get("taxable_pnl")
        if pnl is None:
            pnl = r["pnl"] + r.get("wash_disallowed", 0.0)
        years[y]["st" if r["term"] == "short" else "lt"] += pnl
        years[y]["disallowed"] += r.get("wash_disallowed", 0.0)

    st_rate = rates["federal_short_term"] + rates["state"]
    lt_rate = rates["federal_long_term"] + rates["state"]

    out, carry = [], 0.0
    for y in sorted(years):
        st, lt = years[y]["st"], years[y]["lt"]
        net = st + lt - carry
        if net <= 0:
            #  A net capital loss offsets at most $3,000 of ordinary income;
            #  the rest carries forward. Modelled as zero tax plus carry.
            used = min(LOSS_OFFSET_LIMIT, -net)
            carry = -net - used
            tax = 0.0
        else:
            #  Apply the carryforward to short-term first: it is taxed highest,
            #  which is what any competent preparer would do.
            st_after = max(0.0, st - carry)
            lt_after = lt - max(0.0, carry - st)
            carry = 0.0
            tax = max(0.0, st_after) * st_rate + max(0.0, lt_after) * lt_rate
        out.append({"year": y, "short_term": round(st, 2), "long_term": round(lt, 2),
                    "wash_disallowed": round(years[y]["disallowed"], 2),
                    "tax": round(tax, 2), "carryforward": round(carry, 2)})
    return out


def deferred_liability(open_lots, marks, rates, basis_pool=None):
    """Tax that falls due if the book is liquidated at today's marks."""
    st_rate = rates["federal_short_term"] + rates["state"]
    lt_rate = rates["federal_long_term"] + rates["state"]
    today = date.today()
    basis_pool = dict(basis_pool or {})
    st = lt = 0.0
    for l in open_lots:
        mk = marks.get(l["symbol"])
        if not mk:
            continue
        q = abs(l["qty"])
        pnl = (mk - l["px"]) * q if l["direction"] == "long" else (l["px"] - mk) * q
        #  Deferred wash-sale losses sit in these shares' basis and reduce the
        #  gain that liquidating them would realise.
        adj = basis_pool.get(l["symbol"], 0.0)
        if adj > 0:
            use = min(adj, max(0.0, pnl))
            pnl -= use
            basis_pool[l["symbol"]] = adj - use
        if l["direction"] == "short" or (today - l["date"]).days <= 365:
            st += pnl
        else:
            lt += pnl
    return {"unrealized_short_term": round(st, 2),
            "unrealized_long_term": round(lt, 2),
            "tax_if_liquidated": round(max(0.0, st) * st_rate
                                       + max(0.0, lt) * lt_rate, 2)}


# ─────────────────────────────────────────────────────────────────────────────
#  4. THE BENCHMARK, TAXED THE SAME WAY
# ─────────────────────────────────────────────────────────────────────────────
def spy_after_tax(gross_return_pct, rates, held_days, dividend_pct=0.0):
    """Tax the benchmark the same way, on the SAME gross number the page shows.

    This takes the headline's own SPY return rather than re-deriving one from a
    price pair. The first version here did re-derive it, off its own first and
    last bar, and produced +2.01% against the headline's +0.13% -- two SPY
    figures on one page, which is the defect this repo keeps rediscovering,
    reintroduced by me in the act of documenting it. One series, one number.

    The index's advantage is DEFERRAL: it realises nothing until sold, so it
    is taxed once, at the end, on one gain. `dividend_pct` is taxed along the
    way when supplied; left at zero the whole return is treated as deferred
    appreciation, which favours the benchmark and is the right way to err.
    """
    if gross_return_pct is None:
        return None
    gross = gross_return_pct / 100.0
    div = dividend_pct / 100.0
    price = gross - div
    div_tax = div * (rates["qualified_dividend"] + rates["state"])
    rate = (rates["federal_long_term"] if held_days > 365
            else rates["federal_short_term"]) + rates["state"]
    price_tax = max(0.0, price) * rate
    net = gross - div_tax - price_tax
    return {"gross_return_pct": round(gross * 100, 2),
            "dividend_income_pct": round(div * 100, 2),
            "dividend_tax_pct": round(div_tax * 100, 2),
            "liquidation_tax_pct": round(price_tax * 100, 2),
            "after_tax_return_pct": round(net * 100, 2),
            "term": "long" if held_days > 365 else "short"}


# ─────────────────────────────────────────────────────────────────────────────
#  5. TOP LEVEL
# ─────────────────────────────────────────────────────────────────────────────
def compute(fills, marks, starting_equity, costs=None, rates=None,
            spy=None, held_days=0):
    """Full gross -> costs -> tax -> kept waterfall. Pure: no I/O."""
    rates = {**DEFAULT_RATES, **(rates or {})}
    disposals, open_lots = build_disposals(fills)
    apply_wash_sales(disposals, fills)
    basis_pool = apply_basis_recovery(disposals)

    realized = sum(r["pnl"] for r in disposals)
    disallowed = sum(r.get("wash_disallowed", 0.0) for r in disposals)
    n_wash = sum(1 for r in disposals if r.get("wash_disallowed", 0.0) > 0)

    unreal = 0.0
    for l in open_lots:
        mk = marks.get(l["symbol"])
        if mk:
            q = abs(l["qty"])
            unreal += ((mk - l["px"]) * q if l["direction"] == "long"
                       else (l["px"] - mk) * q)

    cost_total = float((costs or {}).get("total_cost") or 0.0)
    years = tax_by_year(disposals, rates)
    tax_paid = sum(y["tax"] for y in years)
    deferred = deferred_liability(open_lots, marks, rates, basis_pool)

    gross_pnl = realized + unreal
    pre_tax = gross_pnl - cost_total
    kept = pre_tax - tax_paid - deferred["tax_if_liquidated"]

    sens = []
    for label, fed, st_ in SENSITIVITY:
        r2 = {**rates, "federal_short_term": fed,
              "federal_long_term": min(fed, 0.20), "state": st_,
              "qualified_dividend": min(fed, 0.20)}
        t2 = sum(y["tax"] for y in tax_by_year(disposals, r2))
        d2 = deferred_liability(open_lots, marks, r2,
                                basis_pool)["tax_if_liquidated"]
        kept2 = pre_tax - t2 - d2
        row = {"label": label, "tax": round(t2 + d2, 2),
               "after_tax_pnl": round(kept2, 2),
               "after_tax_return_pct": round(kept2 / starting_equity * 100, 2)}
        if spy:
            s2 = spy_after_tax(spy.get("gross_return_pct"), r2, held_days,
                               spy.get("dividend_pct", 0.0))
            row["spy_after_tax_pct"] = s2["after_tax_return_pct"] if s2 else None
            row["excess_pct"] = (round(row["after_tax_return_pct"]
                                       - row["spy_after_tax_pct"], 2)
                                 if s2 else None)
        sens.append(row)

    out = {
        "rates": rates,
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unreal, 2),
        "gross_pnl": round(gross_pnl, 2),
        "frictional_costs": round(cost_total, 2),
        "pre_tax_pnl": round(pre_tax, 2),
        "tax_realized": round(tax_paid, 2),
        "tax_deferred_if_liquidated": deferred["tax_if_liquidated"],
        "after_tax_pnl": round(kept, 2),
        "gross_return_pct": round(gross_pnl / starting_equity * 100, 2),
        "pre_tax_return_pct": round(pre_tax / starting_equity * 100, 2),
        "after_tax_return_pct": round(kept / starting_equity * 100, 2),
        "effective_tax_rate_pct": round(
            (tax_paid + deferred["tax_if_liquidated"]) / pre_tax * 100, 2)
        if pre_tax > 0 else None,
        "n_disposals": len(disposals),
        "n_short_term": sum(1 for r in disposals if r["term"] == "short"),
        "n_long_term": sum(1 for r in disposals if r["term"] == "long"),
        "wash_sales": {"n": n_wash, "disallowed_loss": round(disallowed, 2),
                       "still_deferred": round(sum(basis_pool.values()), 2),
                       "recovered": round(disallowed - sum(basis_pool.values()), 2)},
        "by_year": years,
        "deferred": deferred,
        "sensitivity": sens,
    }
    if spy:
        out["spy"] = spy_after_tax(spy.get("gross_return_pct"), rates,
                                   held_days, spy.get("dividend_pct", 0.0))
        if out["spy"]:
            out["after_tax_excess_pct"] = round(
                out["after_tax_return_pct"] - out["spy"]["after_tax_return_pct"], 2)
    return out


def load_rates():
    cfg = {}
    p = BASE / "config.json"
    if p.exists():
        try:
            cfg = json.loads(p.read_text()).get("tax") or {}
        except (ValueError, OSError):
            cfg = {}
    return {**DEFAULT_RATES, **cfg}


def main():
    sys.path.insert(0, str(BASE))
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    import reconcile_trades as RT
    import requests

    H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
         "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    U = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
    fills = RT.fetch_all_fills(H, U)
    pos = requests.get(f"{U}/v2/positions", headers=H, timeout=20).json()
    marks = {p["symbol"]: float(p["current_price"]) for p in pos}
    cfg = json.loads((BASE / "config.json").read_text())
    out = compute(fills, marks, float(cfg.get("starting_equity", 100000)),
                  costs=None, rates=load_rates())
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
