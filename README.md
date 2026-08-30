# JP Alpha Strategy

A systematic **mean-reversion swing-trading agent** running on Alpaca paper
trading, together with the research apparatus built to find out whether it
actually works.

> **The honest one-line summary:** after a 20-phase audit, this system has **no
> statistically significant edge**. It underperforms buy-and-hold SPY over its
> own backtest window, and roughly two-thirds of its apparent alpha is
> attributable to the hand-picked stock universe rather than to any signal.
> It remains deployed on **paper money** as a live research instrument, not
> because the edge was established.

That summary is the point of this repository. An earlier version of this README
advertised "CAGR 8.1% · Sharpe 0.66 · walk-forward validated · Monte Carlo 99.4%
profitable." Those numbers came from a shorter window, a survivorship-selected
universe, and a benchmark that was never the right one. The research programme
documented in [`docs/RESEARCH_REPORT.md`](docs/RESEARCH_REPORT.md) was
commissioned to test claims like those, and most of them did not survive.

---

## What the evidence actually says

All figures below are on the same window, **2019-01-01 → 2026-08-29**, on one
price vintage, reproducible from the manifest stamped into every `*_results.json`.

### The strategy as originally frozen (`JP_ALPHA_V3_FROZEN`) loses money

| | Frozen V3 (bidirectional, 8% stop) | V4 (long-only, 2.0×ATR) | **Deployed V5 (long-only, 1.5×ATR)** | SPY buy & hold |
|---|---:|---:|---:|---:|
| Total return | **−23.98%** | +152.74% | **+159.57%** | — |
| CAGR | **−3.63%** | +13.32% | **+13.73%** | **+17.60%** |
| Sharpe | −0.14 | 0.88 | **0.98** | — |
| Max drawdown | −36.09% | −25.77% | **−19.35%** | — |
| Calmar | −0.10 | 0.52 | **0.71** | — |
| Profit factor | 0.94 | 1.57 | **1.57** | — |
| Closed trades | 1,279 | 842 | 871 | — |
| Realised risk / position | median 1.95% | 1.333% | **exactly 1.000%** | — |

Two things follow, and they point in opposite directions:

1. The V4/V5 changes (drop the short book, replace the fixed 8% stop with an
   ATR stop, fix the fill anchor) are worth ~184 percentage points. They are
   real engineering improvements, each justified by a mechanism rather than by
   a search over outcomes. The V4→V5 step in particular was chosen because
   1.5×ATR is the **only** multiple under which realised risk equals the 1%
   the system claims to risk — a correctness argument, made in Phase 2 before
   the Phase 12 sweep existed, which then predicted the sweep result.
2. **V5 still loses to SPY** — 13.73% CAGR against 17.60%. A strategy that
   underperforms the index it filters on has not demonstrated skill, however
   good its Sharpe looks in isolation. Better risk-adjusted numbers (Sharpe
   0.98, Calmar 0.71) do not change that.

### Most of the "alpha" is the stock list, not the signal

The 42-name universe was chosen by a human, in the present, from names that
still exist. Before any signal fires, an **equal-weight buy-and-hold basket of
that same universe beat SPY by +6.28%/yr (t = +3.00)**.

Measured against the correct benchmark — its own universe, not SPY — the
strategy's alpha collapses:

| Benchmark | Alpha (%/yr) | t-stat | Significant? |
|---|---:|---:|---|
| SPY | +4.08 | — | looks impressive |
| **The strategy's own universe (equal weight)** | **+1.51** | **+0.38** | **no** |

The same test on the `a_lo_base` variant takes it from +3.28%/yr to +0.66%/yr
(t = 0.17). Phase 8's independent factor-model estimate agrees: **+2.26%/yr,
t = 0.57, not significant.**

### The live track record was overstated and has been corrected

The dashboard was reporting a **win rate of 85.4%** across "144 trades." Those
were not trades. The strategy scales out through tiered targets, so the ledger
holds *exit fragments*, and a T1 fragment is a winner by construction. Rolled up
into actual positions the record is **26 positions, 61.5% win rate**. The
headline live alpha of "+40.6%" now carries its standard error: **t = +1.41,
n = 50 — not significant.**

### What did hold up

- **No look-ahead.** 34/34 assertions pass: every indicator is truncation-
  invariant, there is no `bfill`, no interpolation and no negative shift
  anywhere, and fills are demonstrably not the signal bar's own close (mean
  overnight gap 1.259%).
- **Backtest/live parity is exact.** All four indicators agree to 0.00e+00 and
  all 12 shared constants match. The backtest really does simulate the agent.
- **Capacity is not the binding constraint** at any AUM this will ever see:
  comfortable to ~$25M, impaired near $100M.

### What broke, and was fixed

- **Position sizing did not size risk.** The V3 fixed −8% stop combined with
  ATR-based share counts meant realised risk per position had a **median of
  1.95% of equity against an intended 1.00%**, a 3.53× spread between the 5th
  and 95th percentiles, and 47.6% of positions risking over 2%. An ATR-based
  stop makes the volatility term cancel exactly; at 1.5×ATR realised risk is
  **exactly 1.00%**, by construction. **This is why V5 is deployed** — V4's
  2.0×ATR risked 1.333%, a third more than advertised.
- **A version change rewrote live stops retroactively.** The stop was computed
  from the *current* `STOP_ATR_MULT` at exit-check time, so changing it moved
  the stop on positions already open — trades sized under a different risk
  contract. The multiple is now pinned on each position at entry.
- **An unknown `STRATEGY_VERSION` failed open.** A typo in the environment
  variable silently fell through to V3 behaviour, **re-enabling the short book
  and the fixed −8% stop with no warning**. It now refuses to start.
- **The regime filter does nothing.** Sweeping the SPY-vs-MA50 gate shows
  0.10 (shipped), 0.15, 0.25 and 99 (gate fully disabled) produce *byte-
  identical* results. At its deployed value the filter never binds; deleting it
  would not change a single trade.
- **The data cache silently served wrong results.** The cache key covered the
  universe and window but not the indicator and regime constants — which are
  computed *before* the frame is pickled. Sweeping those parameters returned the
  frame built under the first value, so two whole parameter families looked like
  perfectly flat plateaus when in fact nothing had been applied. Fixed, and the
  affected work re-run.

---

## Design philosophy

1. **The strategy is frozen; everything else is additive.** Alpha logic lives in
   `jp_agent.py` and is not edited to make reporting look better. Bug fixes are
   permitted but must be documented, mechanism-justified, separated from
   optimisation, re-tested and versioned. Monitoring is strictly read-only: it
   places no orders and writes no trading state, so a bug in a chart can never
   move the portfolio.

2. **Try to disprove your own results.** Favourable findings get attacked
   hardest. The universe-bias test, the beta-matched benchmark, the
   fragment-to-position rollup and the parameter-inertness detector all exist
   because a number looked too good and turned out to be measuring something
   other than skill.

3. **An instrument that cannot fail loudly is not an instrument.** Two of the
   most important findings in this repo are bugs in the *research code*, not the
   strategy. Both produced plausible-looking output. Hence the manifests, the
   parity tests and the inertness detector.

---

## Reproducibility

Every `*_results.json` carries a manifest recording the git SHA, whether the
tree was dirty, the data cache key, a **content fingerprint of the price frame
actually consumed**, the Python/pandas/numpy versions, the environment
overrides, and the full effective parameter vector.

This is not ceremony. Re-deriving the caches during this audit moved the frozen
control from −23.83% to −23.98% with an **identical trade count of 1,279** —
signals unchanged, prices changed, because the data vendor had revised its
adjusted history. Two results are comparable only if their `code_sha` and
`data_fingerprint` match, and that is now checkable rather than assumed.

```bash
venv/bin/pip install -r requirements.lock   # 43 pinned packages, Python 3.12.3
```

---

## Tests

One command, four suites, and a partial pass counts as a failure:

```bash
venv/bin/python tests/run_tests.py
```

| Suite | Defends |
|---|---|
| `tests/test_execution.py` | idempotent client order IDs, fill-driven state, snapshot-and-rollback on exit failure, reconciliation halt gate |
| `tests/test_lookahead.py` | Rule #2 (no look-ahead) and exact backtest/live indicator parity |
| `tests/test_phase17_ops.py` | single-instance lock, atomic state write, no secrets on disk |
| `tests/test_strategy_versions.py` | every version resolves to the constants it claims, stops fire at the right level, long-only versions cannot emit a short |

Passing certifies broker correctness, absence of look-ahead, backtest/live
parity and operational safety. It certifies **nothing** about profitability.

---

## Strategy specification (deployed V5)

**Long entry** — all must hold:
- Wilder RSI(14) < 45
- Price ≥ 2% below its 20-day MA
- Volume exhaustion (capitulation spike ≥ 1.3× or multi-day dry-up)
- Close in the upper half of the day's range
- Regime: SPY not more than 10% above its 50-day MA *(measured to be inert — see above)*

**Short entry:** removed in V4 and still absent in V5. The short book was a
persistent loser in a secular bull market and its removal is the single largest
contributor to the V3 → V4 improvement.

**Versions.** `STRATEGY_VERSION` selects one row of a single table in
`jp_agent.py`; nothing else forks on version. `JP_ALPHA_V3_FROZEN` and
`JP_ALPHA_V4_LONGONLY_STOPATR2` remain selectable and byte-identical to how they
ran, so every result produced under them stays reproducible.
`JP_ALPHA_V5_LONGONLY_STOPATR15` is current. An unrecognised value refuses to
start rather than falling back.

**Exits:**

| Level | Trigger | Action |
|---|---|---|
| T1 | +4% | trim 25% |
| T2 | +8% | trim 25% |
| T3 | +12% | close remainder |
| Stop | 1.5 × ATR adverse | exit all |
| Time stop | 21 days without T1 | exit |
| Post-T1 stop | 30 days after T1 without T2 | exit remainder |

**Sizing & limits:** 1% of equity risked per 1.5-ATR move; max 10 simultaneous
positions (≤7 long), ≤2 per sector per direction; minimum price $10.

**Universe:** 42 stocks and ETFs across 9 sectors. SPY is the regime benchmark
and is not tradeable. **This list is the single largest known bias in the
system** — see the universe section of the research report.

---

## Repository layout

| Path | Purpose | Writes trading state? |
|---|---|:---:|
| `jp_agent.py` | Strategy engine — signals, risk, execution | **yes (the only one)** |
| `backtest.py` | Historical simulator; parity-tested against the agent | no |
| `analytics.py` | Read-only performance & risk metrics | no |
| `build_dashboard.py` | Generates `dashboard.html` | no |
| `monitor.py` | Watchdog, dead-man's switch, alerting | no |
| `reconcile_trades.py` | Rebuilds the closed-trade ledger from fills | no |
| `export_pages.py` / `publish_pages.sh` | Public snapshot; secret gate + push gate | no |
| `research/` | The 20-phase research programme | no |
| `tests/` | The four suites above | no |
| `docs/RESEARCH_REPORT.md` | **The single consolidated research record** | no |

---

## Operations

Runs once daily at 4:30 PM ET, Monday–Friday, chained with `;` so a failing
stage never suppresses the health verdict.

Two safety properties are enforced rather than assumed:

- **Single instance.** An exclusive `flock` means an overrunning run cannot be
  joined by the next cron tick — two processes reading the same flat book would
  otherwise submit the same entries at double size.
- **The cron never publishes research.** `publish_pages.sh` stages only `docs/`,
  and refuses to `git push` if any unpushed commit touches a path outside
  `docs/`. Publishing anything else is a deliberate human act.

---

## Disclaimer

This is a personal research project running on **paper trading**. It is not
investment advice and not a solicitation. Backtested performance is
hypothetical. The central finding of this repository is that the strategy's edge
is **not statistically distinguishable from zero** once measured against the
right benchmark. Do not deploy it with real capital.
