#!/usr/bin/env python3
"""PHASE 12 — PARAMETER ROBUSTNESS.

The question is NOT "which parameter value is best" -- asking that is how you
overfit. The question is: given that someone already chose these values, does
performance sit on a PLATEAU (the choice barely matters, so the result is a
property of the idea) or on a SPIKE (the choice is everything, so the result is
a property of the search)?

Diagnostics, in increasing order of how much they should worry you:

  RANK OF THE FROZEN VALUE   If the shipped value happens to be the best or
                             near-best of every sweep, that is the signature of
                             a value that was selected on this same data.

  NEIGHBOUR DEGRADATION      How much is lost by moving one notch either way.
                             A robust parameter loses little.

  PLATEAU WIDTH              How many swept values stay within a tolerance of
                             the frozen result.

  SIGN STABILITY             Does the strategy stay profitable across the whole
                             sweep, or does it flip?

A parameter whose frozen value ranks #1 out of 7 in a sweep, with a sharp
falloff on both sides, is not evidence of a well-chosen parameter. It is
evidence that the parameter was chosen here.

Usage: python3 research/param_robustness.py
"""
import glob
import json
import os
import re
import numpy as np
import pandas as pd

# swept family -> (label, frozen/deployed value, is this a SIGNAL or RISK knob)
FAMILIES = {
    "rsios":   ("RSI oversold threshold",     45.0,  "signal"),
    "disl":    ("min dislocation vs MA20",     0.02, "signal"),
    "volcap":  ("volume capitulation mult",    1.3,  "signal"),
    "reglong": ("regime gate: SPY vs MA50",    0.10, "signal"),
    "t1":      ("T1 target",                   0.04, "exit"),
    "t2":      ("T2 target",                   0.08, "exit"),
    "t3":      ("T3 target",                   0.12, "exit"),
    "tstop":   ("time stop (days)",           21.0,  "exit"),
    "stopatr": ("ATR stop multiple",           2.0,  "risk"),
    "sizeatr": ("ATR sizing multiple",         1.5,  "risk"),
}
METRIC = "sharpe"


def load():
    rows = []
    for p in glob.glob("p12_*_results.json"):
        tag = os.path.basename(p)[4:-13]
        m = re.match(r"^([a-z0-9]+)_(.+)$", tag)
        if not m:
            continue
        fam, val = m.group(1), m.group(2)
        if fam not in FAMILIES:
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        rows.append(dict(fam=fam, val=float(val), sharpe=d.get("sharpe"),
                         ret=d.get("total_return_pct"), cagr=d.get("cagr_pct"),
                         mdd=d.get("max_drawdown_pct"), pf=d.get("profit_factor"),
                         n=d.get("n_trades")))
    return pd.DataFrame(rows).sort_values(["fam", "val"])


def main():
    df = load()
    print("=" * 100)
    print("PHASE 12 — PARAMETER ROBUSTNESS  (one-at-a-time around the shipped values)")
    print("=" * 100)
    print("  Config: V4 deployed (LONG_ONLY, ANCHOR_FILL, 2.0xATR stop), "
          "2019-01-01 -> 2026-08-29")
    print(f"  {len(df)} runs across {df.fam.nunique()} parameters. "
          "Metric shown: Sharpe (return and MaxDD also carried).")

    summary = []
    for fam, (label, frozen, kind) in FAMILIES.items():
        g = df[df.fam == fam].copy()
        if len(g) < 3:
            continue
        print("\n" + "-" * 100)
        print(f"  {label}   [{kind}]   shipped value = {frozen:g}")
        print(f"    {'value':>10}{'Sharpe':>9}{'return':>10}{'CAGR':>9}"
              f"{'MaxDD':>9}{'PF':>7}{'trades':>8}")
        base = g[np.isclose(g.val, frozen)]
        b = base.iloc[0] if len(base) else None
        for _, r in g.iterrows():
            mark = "  <== shipped" if b is not None and np.isclose(r.val, frozen) else ""
            print(f"    {r.val:>10g}{r.sharpe:>9.2f}{r.ret:>9.1f}%{r.cagr:>8.2f}%"
                  f"{r.mdd:>8.1f}%{r.pf:>7.2f}{int(r.n):>8}{mark}")
        if b is None:
            print("    (shipped value not present in sweep — cannot rank)")
            continue

        s = g[METRIC].values
        rank = int((s > b[METRIC]).sum()) + 1
        best = g.loc[g[METRIC].idxmax()]
        # neighbour degradation
        vals = g.val.values
        i = int(np.argmin(np.abs(vals - frozen)))
        nb = [g.iloc[j][METRIC] for j in (i - 1, i + 1) if 0 <= j < len(g)]
        worst_nb = min(nb) if nb else np.nan
        drop = b[METRIC] - worst_nb if nb else np.nan
        plateau = int((s >= b[METRIC] - 0.10).sum())
        signflip = int((g.ret <= 0).sum())

        # INERTNESS. A value is byte-identical to shipped if it produced the
        # same return AND the same trade count -- that is not a plateau, it is
        # the parameter having no effect at all. Two very different causes:
        #   * the whole sweep identical  -> the knob is NOT WIRED, or is being
        #     swallowed by a cache. The instrument is broken; do not interpret.
        #   * only part identical        -> a genuine NON-BINDING range. If the
        #     shipped value sits inside it, the constraint is decorative there
        #     and could be deleted without changing a single trade.
        ident = (np.isclose(g.ret, b.ret, atol=1e-9) & (g.n == b.n))
        n_ident = int(ident.sum())
        if n_ident == len(g):
            note = ("ALL values identical -> parameter NOT WIRED or masked by "
                    "a cache. Instrument broken, results not interpretable.")
        elif n_ident > 1:
            iv = sorted(g.val[ident])
            note = (f"INERT over {n_ident}/{len(g)} values "
                    f"({iv[0]:g}..{iv[-1]:g}) incl. shipped -> the constraint "
                    f"does not bind at {frozen:g}; deleting it changes nothing.")
        else:
            note = ""

        summary.append(dict(fam=fam, label=label, kind=kind, frozen=frozen,
                            rank=rank, n=len(g), base=b[METRIC],
                            best=best[METRIC], best_at=best.val,
                            drop=drop, plateau=plateau, flip=signflip,
                            inert=n_ident, note=note,
                            spread=s.max() - s.min()))
        print(f"    -> shipped ranks {rank} of {len(g)}   "
              f"best is {best[METRIC]:.2f} at {best.val:g}   "
              f"worst neighbour {worst_nb:.2f} (drop {drop:.2f})")
        if note:
            print(f"    !! {note}")

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    S = pd.DataFrame(summary)
    print(f"  {'parameter':<28}{'kind':<8}{'shipped':>9}{'rank':>7}"
          f"{'Sharpe':>8}{'best':>7}{'@':>8}{'nb drop':>9}{'plateau':>9}{'neg':>5}")
    print("  " + "-" * 98)
    for _, r in S.iterrows():
        print(f"  {r.label:<28}{r.kind:<8}{r.frozen:>9g}{r['rank']:>4}/{r.n:<2}"
              f"{r.base:>8.2f}{r.best:>7.2f}{r.best_at:>8g}{r['drop']:>9.2f}"
              f"{r.plateau:>6}/{r.n:<2}{r.flip:>5}")

    print(f"""
  READING THE TABLE
    rank      position of the SHIPPED value among swept values, by Sharpe.
              Rank 1 on many parameters at once is the overfitting signature.
    nb drop   Sharpe lost by moving one notch to the worse neighbour.
              Large = sitting on a spike.
    plateau   how many swept values are within 0.10 Sharpe of shipped.
              Large = the choice barely matters, which is GOOD.
    neg       swept values that produce a negative total return.
""")
    inert = S[S.inert > 1]
    if len(inert):
        print("  PARAMETERS THAT DO NOT BIND AT THEIR SHIPPED VALUE")
        for _, r in inert.iterrows():
            print(f"    * {r.label}: {r.note}")
        print()

    top1 = (S["rank"] == 1).sum()
    print(f"  Shipped value ranks #1 on {top1} of {len(S)} parameters.")
    print(f"  Median plateau width {S.plateau.median():.0f} of "
          f"{S.n.median():.0f} swept values.")
    print(f"  Median neighbour drop {S['drop'].median():.2f} Sharpe.")
    print(f"  Parameters where SOME setting turns the strategy negative: "
          f"{(S.flip>0).sum()} of {len(S)}.")


if __name__ == "__main__":
    main()
