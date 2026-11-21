#!/usr/bin/env python3
"""AFTERTAX-1 — lot accounting, holding period, wash sales, netting.

Every expected number here is hand-computed in the comment above it. A test
that recomputes the answer with the code under test proves only that the code
is self-consistent, which is exactly how a wrong tax model passes its suite.
"""
import os
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("APCA_API_KEY_ID", "x")
os.environ.setdefault("APCA_API_SECRET_KEY", "x")

import aftertax as A

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if not cond else ""))


def fill(t, sym, side, qty, px):
    return {"transaction_time": t + "T15:00:00Z", "symbol": sym,
            "side": side, "qty": qty, "price": px}


R = {"federal_short_term": 0.24, "federal_long_term": 0.15, "state": 0.05,
     "qualified_dividend": 0.15}

# ── FIFO, not average cost ──────────────────────────────────────────────────
#  Buy 100 @ 10 (Jan), buy 100 @ 20 (Feb), sell 100 @ 30 (Mar).
#  FIFO sells the Jan lot: gain = (30-10)*100 = +2000, held 59 days = SHORT.
#  Average cost would say (30-15)*100 = +1500. The IRS default is FIFO.
f = [fill("2026-01-01", "X", "buy", 100, 10),
     fill("2026-02-01", "X", "buy", 100, 20),
     fill("2026-03-01", "X", "sell", 100, 30)]
dz, open_lots = A.build_disposals(f)
check("FIFO consumes the oldest lot first",
      len(dz) == 1 and abs(dz[0]["pnl"] - 2000.0) < 1e-6,
      [round(d["pnl"], 2) for d in dz])
check("the unsold lot stays open at its own cost",
      len(open_lots) == 1 and open_lots[0]["px"] == 20 and open_lots[0]["qty"] == 100,
      open_lots)

# ── holding period boundary ─────────────────────────────────────────────────
#  Held exactly 365 days is still SHORT term; 366 is long. "More than one
#  year" means more than, and off-by-one here moves income between a 29% and
#  a 20% rate.
f365 = [fill("2025-01-01", "Y", "buy", 10, 100), fill("2026-01-01", "Y", "sell", 10, 110)]
f366 = [fill("2025-01-01", "Y", "buy", 10, 100), fill("2026-01-02", "Y", "sell", 10, 110)]
check("exactly 365 days is short-term", A.build_disposals(f365)[0][0]["term"] == "short")
check("366 days is long-term", A.build_disposals(f366)[0][0]["term"] == "long")

# ── short sales are always short-term (IRC §1233) ───────────────────────────
#  Shorted 2024-01, covered 2026-01: two years open, still short-term.
fs = [fill("2024-01-01", "Z", "sell_short", 10, 100),
      fill("2026-01-05", "Z", "buy", 10, 90)]
ds = A.build_disposals(fs)[0]
check("a short held two years is still short-term",
      ds[0]["term"] == "short" and abs(ds[0]["pnl"] - 100.0) < 1e-6,
      (ds[0]["term"], ds[0]["pnl"]))

# ── wash sale: the shares you sold are not their own replacement ────────────
#  Buy 100 @ 10, sell all 100 @ 8 nine days later, never re-buy.
#  A real loss of -200. The opening purchase is within 30 days before the
#  sale, but it IS the lot being sold. Nothing was replaced -> ALLOWED.
fw = [fill("2026-01-01", "W", "buy", 100, 10), fill("2026-01-10", "W", "sell", 100, 8)]
dw = A.build_disposals(fw)[0]
A.apply_wash_sales(dw, fw)
check("an ordinary quick losing trade is NOT a wash sale",
      dw[0]["wash_disallowed"] == 0.0, dw[0]["wash_disallowed"])

#  Now re-buy 100 @ 8.50 five days after the sale -> replaced -> DISALLOWED.
fw2 = fw + [fill("2026-01-15", "W", "buy", 100, 8.5)]
dw2 = A.build_disposals(fw2)[0]
A.apply_wash_sales(dw2, fw2)
loss = [d for d in dw2 if d["pnl"] < 0][0]
check("re-buying inside 30 days disallows the loss",
      abs(loss["wash_disallowed"] - 200.0) < 1e-6, loss["wash_disallowed"])

#  Partial sale of a recent purchase while keeping the rest: the retained
#  shares were acquired inside the window, so the loss on the sold ones is
#  disallowed. Buy 100 @ 10, sell 40 @ 8 -> -80 loss, all disallowed.
fw3 = [fill("2026-01-01", "V", "buy", 100, 10), fill("2026-01-10", "V", "sell", 40, 8)]
dw3 = A.build_disposals(fw3)[0]
A.apply_wash_sales(dw3, fw3)
check("selling part of a recent lot at a loss is a wash sale",
      abs(dw3[0]["wash_disallowed"] - 80.0) < 1e-6, dw3[0]["wash_disallowed"])

# ── the deferral comes back ─────────────────────────────────────────────────
#  Buy 100 @ 10, sell 100 @ 8 (-200, disallowed, re-bought), buy 100 @ 8.50,
#  sell 100 @ 9.50 (+100 economic). Basis of the replacement is 8.50 + 2.00 =
#  10.50, so the second disposal is a 100-dollar LOSS for tax, not a gain.
#  Taxable across the pair: -100, matching the true economics (-200 + 100).
fr = [fill("2026-01-01", "U", "buy", 100, 10),
      fill("2026-01-10", "U", "sell", 100, 8),
      fill("2026-01-15", "U", "buy", 100, 8.5),
      fill("2026-03-01", "U", "sell", 100, 9.5)]
dr = A.build_disposals(fr)[0]
A.apply_wash_sales(dr, fr)
pool = A.apply_basis_recovery(dr)
taxable = sum(d["taxable_pnl"] for d in dr)
economic = sum(d["pnl"] for d in dr)
check("a closed-out wash chain nets to its true economics",
      abs(taxable - economic) < 1e-6 and abs(economic + 100.0) < 1e-6,
      (taxable, economic))
check("nothing is left deferred once the chain is closed", pool == {}, pool)

# ── §1211(b): a net capital loss offsets only $3,000 of ordinary income ─────
#  One losing trade of -10,000, no gains. Tax is 0 and 7,000 carries forward.
fl = [fill("2026-01-01", "T", "buy", 100, 200), fill("2026-06-01", "T", "sell", 100, 100)]
dl = A.build_disposals(fl)[0]
A.apply_wash_sales(dl, fl)
A.apply_basis_recovery(dl)
yrs = A.tax_by_year(dl, R)
check("a net loss year owes no tax", yrs[0]["tax"] == 0.0, yrs[0])
check("the loss above $3,000 carries forward",
      abs(yrs[0]["carryforward"] - 7000.0) < 1e-6, yrs[0]["carryforward"])

# ── rates applied to the right bucket ───────────────────────────────────────
#  +10,000 short-term at 24%+5% = 2,900.
fg = [fill("2026-01-01", "S", "buy", 100, 100), fill("2026-03-01", "S", "sell", 100, 200)]
dg = A.build_disposals(fg)[0]
A.apply_wash_sales(dg, fg); A.apply_basis_recovery(dg)
check("short-term gain is taxed at ordinary + state",
      abs(A.tax_by_year(dg, R)[0]["tax"] - 2900.0) < 1e-6,
      A.tax_by_year(dg, R)[0]["tax"])

#  Same gain held 400 days: 15%+5% = 2,000.
fg2 = [fill("2025-01-01", "S", "buy", 100, 100), fill("2026-02-05", "S", "sell", 100, 200)]
dg2 = A.build_disposals(fg2)[0]
A.apply_wash_sales(dg2, fg2); A.apply_basis_recovery(dg2)
check("long-term gain is taxed at the LT rate",
      abs(A.tax_by_year(dg2, R)[0]["tax"] - 2000.0) < 1e-6,
      A.tax_by_year(dg2, R)[0]["tax"])

# ── the benchmark is taxed too, and differently ─────────────────────────────
#  SPY +12% gross of which 2pp is dividends, held 200 days.
#  price 10% taxed ST at 29% = 2.9pp; dividends 2pp at 15%+5% = 0.4pp.
#  After tax 12 - 3.3 = 8.7%.
sp = A.spy_after_tax(12.0, R, held_days=200, dividend_pct=2.0)
check("SPY after-tax nets dividend and liquidation tax",
      abs(sp["after_tax_return_pct"] - 8.7) < 0.01, sp)
#  Held over a year the price gain is taxed at 20% -> 12 - 0.4 - 2.0 = 9.6%.
sp2 = A.spy_after_tax(12.0, R, held_days=400, dividend_pct=2.0)
check("a year-plus hold moves SPY to the long-term rate",
      abs(sp2["after_tax_return_pct"] - 9.6) < 0.01, sp2)
check("the benchmark's advantage is deferral, not a lower gain",
      sp2["gross_return_pct"] == sp["gross_return_pct"]
      and sp2["after_tax_return_pct"] > sp["after_tax_return_pct"])

#  REGRESSION: the panel must tax the number the page already shows, never
#  re-derive its own. Feeding the headline's figure in must give it back
#  untaxed at a zero rate -- if this drifts, the page carries two SPY numbers.
Z0 = {"federal_short_term": 0.0, "federal_long_term": 0.0, "state": 0.0,
      "qualified_dividend": 0.0}
check("at 0% the benchmark's after-tax return IS the headline figure",
      A.spy_after_tax(0.13, Z0, 180)["after_tax_return_pct"] == 0.13,
      A.spy_after_tax(0.13, Z0, 180))
check("a negative benchmark return is not 'taxed' into a smaller loss",
      A.spy_after_tax(-5.0, R, 180)["after_tax_return_pct"] == -5.0,
      A.spy_after_tax(-5.0, R, 180))

# ── a zero-rate reader gets the pre-tax number back, unchanged ─────────────
Z = {"federal_short_term": 0.0, "federal_long_term": 0.0, "state": 0.0,
     "qualified_dividend": 0.0}
out = A.compute(fg, {}, 100000.0, rates=Z)
check("at a 0% rate after-tax equals pre-tax",
      out["after_tax_pnl"] == out["pre_tax_pnl"], out["after_tax_pnl"])

#  NEGATIVE: costs are deducted before tax, and must actually move the number.
out_c = A.compute(fg, {}, 100000.0, rates=R, costs={"total_cost": 500.0})
out_n = A.compute(fg, {}, 100000.0, rates=R)
check("frictional costs reduce the pre-tax figure",
      abs((out_n["pre_tax_pnl"] - out_c["pre_tax_pnl"]) - 500.0) < 1e-6,
      (out_n["pre_tax_pnl"], out_c["pre_tax_pnl"]))

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for x in FAIL:
        print(f"    FAILED: {x}")
    sys.exit(1)
print("  AFTER-TAX MODEL TESTS PASSED")
