#!/usr/bin/env python3
"""PHASE 5 + PHASE 7 — position-level ledger and short-book attribution.

The research question this exists to answer:

    Why does the long book work while the short book destroys the combined
    strategy? Does the short side have (1) no edge, (2) a conditional edge,
    (3) a regime-dependent edge, (4) excessive tail risk, (5) excessive
    transaction cost, or (6) structural asymmetry vs the long side?

Input : <prefix>_trades.csv  (fragment level, produced by backtest.py)
Output: <prefix>_positions.csv + a printed attribution report

IMPORTANT: the input rows are exit FRAGMENTS, not trades. A tiered exit emits
up to three fragments per position. Every statistic below is computed at
POSITION level unless explicitly labelled otherwise.

Usage:  python3 research/short_book_analysis.py f_base
"""
import sys
import numpy as np
import pandas as pd

pd.set_option("display.width", 200)

RNG = np.random.default_rng(42)          # reproducibility: fixed seed


# ── Phase 5: fragments -> positions ──────────────────────────────────────────
def build_positions(frag: pd.DataFrame) -> pd.DataFrame:
    """Collapse exit fragments into one record per initiated position."""
    frag = frag.copy()
    frag["entry_date"] = pd.to_datetime(frag["entry_date"])
    frag["exit_date"] = pd.to_datetime(frag["exit_date"])

    # terminal exit reason = the reason on the chronologically last fragment
    frag = frag.sort_values(["pos_id", "exit_date"])
    last = frag.groupby("pos_id").tail(1).set_index("pos_id")

    g = frag.groupby("pos_id")
    pos = pd.DataFrame({
        "symbol":       g["symbol"].first(),
        "direction":    g["direction"].first(),
        "sector":       g["sector"].first(),
        "entry_date":   g["entry_date"].first(),
        "exit_date":    g["exit_date"].max(),
        "entry_px":     g["entry_px"].first(),
        "shares_total": g["shares_total"].first(),
        "qty_exited":   g["qty"].sum(),
        "gross_pnl":    g["gross_pnl"].sum(),
        "n_fragments":  g.size(),
        "rsi_ent":      g["rsi_ent"].first(),
        "disl_ent":     g["disl_ent"].first(),
        "atrpct_ent":   g["atrpct_ent"].first(),
        "spydev_ent":   g["spydev_ent"].first(),
        "anchor_err":   g["anchor_err_pct"].first(),
    })
    pos["exit_reason"] = last["exit_reason"]
    pos["cost_basis"] = pos["entry_px"] * pos["shares_total"]
    pos["ret_pct"] = pos["gross_pnl"] / pos["cost_basis"] * 100
    pos["hold_days"] = (pos["exit_date"] - pos["entry_date"]).dt.days
    pos["win"] = pos["gross_pnl"] > 0
    return pos.reset_index()


# ── statistics helpers ───────────────────────────────────────────────────────
def boot_ci(x, n=10000, stat=np.mean, lo=2.5, hi=97.5):
    """Bootstrap CI. Returns (point, lo, hi) or NaNs if the sample is too thin."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 10:
        return (np.nan, np.nan, np.nan)
    idx = RNG.integers(0, len(x), size=(n, len(x)))
    d = stat(x[idx], axis=1)
    return (stat(x), np.percentile(d, lo), np.percentile(d, hi))


def block(pos, label):
    """Core stat block for a set of positions. Always reports n."""
    n = len(pos)
    if n == 0:
        return None
    pnl = pos["gross_pnl"]
    r = pos["ret_pct"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gp, gl = wins.sum(), -losses.sum()
    m, clo, chi = boot_ci(r)
    return {
        "slice": label,
        "n": n,
        "pnl": pnl.sum(),
        "win%": 100 * pos["win"].mean(),
        "avg_ret%": m,
        "ci_lo": clo,
        "ci_hi": chi,
        "med_ret%": r.median(),
        "PF": (gp / gl) if gl > 0 else np.inf,
        "expect$": pnl.mean(),
        "avg_win$": wins.mean() if len(wins) else 0,
        "avg_loss$": losses.mean() if len(losses) else 0,
        "skew": r.skew(),
        "kurt": r.kurtosis(),
        "hold_d": pos["hold_days"].median(),
    }


def table(rows, title):
    rows = [r for r in rows if r]
    if not rows:
        return
    df = pd.DataFrame(rows).set_index("slice")
    print(f"\n{'='*118}\n{title}\n{'='*118}")
    fmt = {"pnl": "{:,.0f}", "win%": "{:.1f}", "avg_ret%": "{:+.2f}",
           "ci_lo": "{:+.2f}", "ci_hi": "{:+.2f}", "med_ret%": "{:+.2f}",
           "PF": "{:.2f}", "expect$": "{:,.0f}", "avg_win$": "{:,.0f}",
           "avg_loss$": "{:,.0f}", "skew": "{:.2f}", "kurt": "{:.2f}",
           "hold_d": "{:.0f}"}
    print(df.to_string(formatters={k: v.format for k, v in fmt.items()}))


def qbucket(s, labels):
    """Quantile buckets, robust to ties."""
    try:
        return pd.qcut(s, len(labels), labels=labels, duplicates="drop")
    except Exception:
        return pd.Series(["all"] * len(s), index=s.index)


def main(prefix="f_base"):
    frag = pd.read_csv(f"{prefix}_trades.csv")
    pos = build_positions(frag)
    pos.to_csv(f"{prefix}_positions.csv", index=False)

    L = pos[pos.direction == "long"]
    S = pos[pos.direction == "short"]

    print(f"\nFragments (what earlier reports called 'trades'): {len(frag)}")
    print(f"POSITIONS actually initiated and closed          : {len(pos)}")
    print(f"   long {len(L)}   short {len(S)}")
    print(f"Fragment-level win rate : {100*(frag.gross_pnl>0).mean():.1f}%   <-- inflated by construction")
    print(f"POSITION-level win rate : {100*pos.win.mean():.1f}%   <-- the honest number")

    # ── headline ──
    table([block(pos, "ALL"), block(L, "LONG"), block(S, "SHORT")],
          "PHASE 7.1 — LONG vs SHORT (position level, 95% bootstrap CI on mean return)")

    # ── exit reasons ──
    print(f"\n{'='*118}\nPHASE 7.2 — TERMINAL EXIT REASON BY DIRECTION\n{'='*118}")
    for name, d in (("LONG", L), ("SHORT", S)):
        x = d.groupby("exit_reason").agg(n=("gross_pnl", "size"),
                                         pnl=("gross_pnl", "sum"),
                                         avg_ret=("ret_pct", "mean"))
        x["share%"] = 100 * x["n"] / len(d)
        print(f"\n{name} (n={len(d)}):")
        print(x.sort_values("pnl").to_string(
            formatters={"pnl": "{:,.0f}".format, "avg_ret": "{:+.2f}".format,
                        "share%": "{:.1f}".format}))

    # ── conditional slices ──
    for col, labels, title in [
        ("spydev_ent", ["SPY deep<MA50", "SPY <MA50", "SPY >MA50", "SPY far>MA50"],
         "PHASE 7.3 — MARKET REGIME AT ENTRY (SPY deviation from MA50)"),
        ("atrpct_ent", ["vol Q1 low", "vol Q2", "vol Q3", "vol Q4 high"],
         "PHASE 7.4 — STOCK VOLATILITY REGIME AT ENTRY (ATR/price)"),
        ("disl_ent", ["disl Q1 shallow", "disl Q2", "disl Q3", "disl Q4 deep"],
         "PHASE 7.5 — SIGNAL STRENGTH AT ENTRY (MA20 dislocation)"),
        ("rsi_ent", ["RSI Q1", "RSI Q2", "RSI Q3", "RSI Q4"],
         "PHASE 7.6 — RSI AT ENTRY"),
    ]:
        rows = []
        for name, d in (("LONG", L), ("SHORT", S)):
            b = qbucket(d[col], labels)
            for lab in [x for x in labels if x in set(b.dropna())]:
                rows.append(block(d[b == lab], f"{name} · {lab}"))
        table(rows, title)

    # ── sector ──
    rows = []
    for name, d in (("LONG", L), ("SHORT", S)):
        for sec, sub in d.groupby("sector"):
            if len(sub) >= 10:
                rows.append(block(sub, f"{name} · {sec}"))
    table(rows, "PHASE 7.7 — SECTOR (slices with n>=10 only)")

    # ── holding period ──
    rows = []
    for name, d in (("LONG", L), ("SHORT", S)):
        b = qbucket(d["hold_days"], ["hold Q1 short", "hold Q2", "hold Q3", "hold Q4 long"])
        for lab in [x for x in ["hold Q1 short", "hold Q2", "hold Q3", "hold Q4 long"]
                    if x in set(b.dropna())]:
            rows.append(block(d[b == lab], f"{name} · {lab}"))
    table(rows, "PHASE 7.8 — HOLDING PERIOD")

    # ── tail risk ──
    print(f"\n{'='*118}\nPHASE 7.9 — TAIL RISK (position return distribution)\n{'='*118}")
    tr = []
    for name, d in (("LONG", L), ("SHORT", S)):
        r = d["ret_pct"].dropna()
        tr.append({
            "slice": name, "n": len(r),
            "p1": r.quantile(.01), "p5": r.quantile(.05), "p25": r.quantile(.25),
            "median": r.median(), "p75": r.quantile(.75), "p95": r.quantile(.95),
            "p99": r.quantile(.99), "worst": r.min(), "best": r.max(),
            "CVaR5": r[r <= r.quantile(.05)].mean(),
            "skew": r.skew(), "kurt": r.kurtosis(),
        })
    print(pd.DataFrame(tr).set_index("slice").to_string(float_format=lambda x: f"{x:+.2f}"))

    print("\nWorst 10 positions overall (P&L):")
    print(pos.nsmallest(10, "gross_pnl")[
        ["symbol", "direction", "entry_date", "exit_date", "ret_pct",
         "gross_pnl", "exit_reason", "hold_days"]].to_string(index=False))

    # ── concentration: how much of the short loss is a handful of names? ──
    print(f"\n{'='*118}\nPHASE 7.10 — LOSS CONCENTRATION\n{'='*118}")
    for name, d in (("LONG", L), ("SHORT", S)):
        s = d["gross_pnl"].sort_values()
        tot = s.sum()
        print(f"\n{name}: total P&L ${tot:,.0f} over {len(s)} positions")
        for k in (1, 5, 10, 20):
            if len(s) > k:
                print(f"   worst {k:>2} positions = ${s.head(k).sum():>12,.0f}"
                      f"   |   best {k:>2} = ${s.tail(k).sum():>12,.0f}")
        by = d.groupby("symbol")["gross_pnl"].sum().sort_values()
        print(f"   worst 5 symbols: " +
              ", ".join(f"{i} ${v:,.0f}" for i, v in by.head(5).items()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "f_base")
