# ARCHITECTURE AUDIT — JP Alpha Strategy

**Audit date:** 2026-08-29
**Commit audited:** `c41b161`
**Auditor scope:** Phase 0 (repository forensics) of the institutional upgrade program.
**Status:** Phase 0 complete. Phases 1–20 outstanding.

> Nothing in this document changed `jp_agent.py`. The live agent is untouched and
> still running the frozen strategy. All findings below are evidence-backed —
> every claim cites either a line number or a measurement that can be re-run.

---

## 1. File inventory

Repository root: `/root/jp_strategy` on `46.224.12.251`. 4,126 lines of Python across 13 modules.

| File | LOC | Role | Touches broker? | Mutates state? |
|---|---|---|---|---|
| `jp_agent.py` | 1118 | **Live strategy + execution.** FROZEN. | GET/POST/DELETE | yes — `state.json` |
| `backtest.py` | 442 | Research simulator | no | no |
| `analytics.py` | 533 | Risk/perf metrics | GET only | no |
| `build_dashboard.py` | 587 | Static dashboard generator | GET only | no |
| `live_server.py` | 179 | Flask near-real-time API | GET only | no |
| `costs.py` | 291 | Transaction-cost model | GET only | no |
| `monitor.py` | 277 | Health checks / alerting | indirect | no |
| `reconcile_trades.py` | 184 | Rebuilds closed-trade ledger from fills | GET only | writes `trades_closed.csv` |
| `status.py` | 106 | CLI status | GET only | no |
| `export_pages.py` | 103 | GitHub Pages static export | no | writes `docs/` |
| `make_tearsheet.py` | 140 | Backtest tear sheet | no | writes PNG |

**Only `jp_agent.py` can place, modify, or cancel an order.** Verified by grep for
`alpaca_post` / `alpaca_delete` / `requests.post` / `requests.delete` across all modules.
Every other module is GET-only. This is a genuine strength and should be preserved.

## 2. Write map — who mutates what

| Artifact | Written by | Line |
|---|---|---|
| `state.json` | `jp_agent.save_state` | 269–272 |
| `equity_curve.csv` | `jp_agent.log_equity` | 920 (append) |
| `trade_log.csv` | `jp_agent.log_trades` | 943 (append) |
| `positions_history.csv` | `jp_agent.log_positions_history` | 965 (append) |
| `heartbeat.json` | `jp_agent.write_heartbeat` | 1010 |
| `trades_closed.csv` | `reconcile_trades.reconcile_and_write` | — |
| `docs/` | `export_pages.py` | — |

`state.json` is the single canonical trading-state file and has exactly one writer.

## 3. Broker API surface

All calls route through three helpers (`jp_agent.py:211–224`):

- `alpaca_get` → `/v2/account`, `/v2/positions`, `/v2/clock`
- `alpaca_post` → `/v2/orders` (**the only order-creation path**, line 250)
- `alpaca_delete` → `/v2/orders` (**blanket cancel-all**, line 253)

Data feed: `data.alpaca.markets/v2/stocks/*` (quotes for cost model, SPY bars for benchmark).

---

## 4. FINDINGS

Severity: **C**ritical / **H**igh / **M**edium.

### EXEC-1 (C) — State is written on *submission*, not on *fill*

`execute_orders` (line 840) writes `positions[sym] = {...}` immediately after
`place_market_order` returns. For a market DAY order submitted after the close,
the returned `status` is `accepted` / `pending_new` — **never** `filled`. The
strategy therefore books a position that does not yet exist.

The response's `status` is logged but **never branched on**. A rejection
discovered asynchronously leaves a phantom position in `state.json`.

*Mitigating factor:* `reconcile_state` on the next run deletes positions absent
from the broker, so a phantom self-heals within one session. The un-healed
consequence is EXEC-2.

### EXEC-2 (C) — `entry_price` is never reconciled to the actual fill

This is the highest-impact defect in the system.

`state.json` stores `"entry_price": order["entry_price"]` — the **reference
price** (prior close) the order was *sized* from, not the price paid at the next
open. `reconcile_state` (line 275) repairs `shares_remaining` only; it never
touches `entry_price`. Every T1/T2/T3 target and every stop derives from this
stale number (`process_long_exits`, line 564).

**Live evidence, 2026-08-28:**

| Symbol | `state.json` entry | broker `avg_entry_price` | error |
|---|---|---|---|
| QQQ | 675.49 | 675.07 | +0.06% |
| UNH | 433.24 | 433.14 | +0.02% |
| **WMT** | **103.70** | **105.05** | **−1.29%** |

For WMT the strategy believes it is down 0.59% when it is actually down 1.87%.
Its "+4% target" sits at 107.85 = **+2.66%** on the real fill; its "−8% stop"
sits at 95.40 = **−9.19%** on the real fill.

**Backtest evidence (n=1,279 closed fragments, 2019–2026):**

| | mean | median | std | \|err\|>1% | \|err\|>2% | p95 |
|---|---|---|---|---|---|---|
| Long (744) | +0.191% | +0.135% | 2.030% | 35.1% | 14.4% | +3.05% |
| Short (535) | +0.079% | +0.040% | 1.831% | 35.5% | 10.5% | +2.20% |

Against a design grid of **+4% / −8%**, a ±2% standard deviation on the anchor is
not a rounding error — it materially randomises the intended risk/reward on
roughly a third of all trades.

The bias is *adverse by construction* for a dip-buying strategy: entries are
signalled on oversold closes, and an overnight bounce means you pay above the
anchor, which **tightens the target and loosens the stop**.

**Crucially, `backtest.py` reproduces this behaviour faithfully** (`check_exit`
reads `pos["entry_price"]`; cash and P&L book at the true `fill_price`,
lines 274–286). The backtest is therefore *not* overstating live performance on
this axis. This reclassifies EXEC-2 from "backtest bug" to **strategy design
defect that the backtest already prices in** — an important distinction under
the bug-fix-vs-optimization rule.

### EXEC-3 (H) — No client order IDs

`place_market_order` (line 235) submits `symbol / qty / side / type /
time_in_force`. No `client_order_id`. Consequences: orders cannot be attributed
to a strategy version, replays cannot be deduplicated, and the strategy cannot
distinguish its own orders from any other activity on the account.

### EXEC-4 (H) — Blanket `DELETE /v2/orders` on every run

`cancel_all_pending_orders` (line 252) is called unconditionally in `main`
(line 1084) and cancels **every open order on the account**, not just this
strategy's. Harmless on a single-strategy paper account; unacceptable on any
shared or funded account. Cannot be scoped correctly until EXEC-3 is fixed.

### EXEC-5 (C) — Divergence is logged, then trading continues

`reconcile_state` silently deletes divergent positions and adjusts share counts,
returns, and `main` proceeds directly into `process_entries`. There is no halt,
no divergence record, no alert gate. This is precisely the behaviour the brief
prohibits: *"Do not merely alert while continuing to trade."*

### EXEC-6 (C) — Orphan broker positions are invisible to every risk limit

`reconcile_state` iterates only over `state["positions"]`. A position the broker
holds but state does not know about is never examined. Worse, `process_entries`
(line 743) derives **all** portfolio limits — `n_longs`, `n_shorts`, sector
counts, and the duplicate-symbol guard — from `state["positions"]` alone.

An orphan therefore:
- does not count toward `MAX_LONGS` / `MAX_SHORTS` / `MAX_SIMULTANEOUS`
- does not count toward `MAX_PER_SECTOR`
- **does not prevent a second position being opened in the same symbol**

### EXEC-7 (M) — No partial-fill handling anywhere

`shares_total` and `shares_remaining` are set to the *requested* qty. A partial
fill is only detected on the next run, and only as a `shares_remaining`
correction with no record that it was a partial.

### RISK-1 (H) — Sizing risk and stop risk are different numbers

`calc_shares` (line 545) sizes on `1% risk / (1.5 × ATR)`. The stop is a fixed
−8%. These agree only when `1.5 × ATR ≈ 8% × price`. For a low-vol name the
true risk is far below 1%; for a high-vol name, far above. Intended risk ≠ actual
risk on essentially every position. (Phase 2.)

### REPRO-1 (H) — Backtests are not reproducible

No data cache existed: every run re-downloaded from yfinance with
`auto_adjust=True`. Adjusted history is **revised retroactively** on every
dividend and split, so the same commit produces different results over time.
No manifest records data range, universe version, or cost model. (Phase 19.)

*Partially remediated during this audit — see §5.*

### DATA-1 (H) — Universe is survivorship-biased

The 42-symbol `WATCHLIST` (`jp_agent.py:117–138`) is a fixed present-day list
applied to 2019. Every member survived to 2026 and was selected with that
knowledge. No point-in-time membership. (Phase 4.)

---

## 5. Changes made during Phase 0

All changes are to `backtest.py` only. `jp_agent.py` is **unmodified**.

1. **Instrumentation (inert).** Added `entry_ref` and `anchor_err_pct` to the
   closed-trade CSV. Regression-verified: baseline returns −23.83%, 1,279 trades,
   PF 0.94, Sharpe −0.14 — **identical** to the pre-instrumentation run.
2. **Price cache.** `data/prices_<sha256>.pkl`, keyed on universe + window + data
   semantics. `BT_NOCACHE=1` bypasses. Addresses REPRO-1 partially.
3. **`ANCHOR_FILL=1` toggle (default OFF).** Gated correction for EXEC-2.
   Default-off keeps the baseline bit-identical to `JP_ALPHA_V3_FROZEN`.

### Measured effect of the EXEC-2 correction (2019-01-01 → 2026-08-29)

| Config | Return | Sharpe | MaxDD | PF | Trades | Win% |
|---|---|---|---|---|---|---|
| baseline (frozen) | −23.8% | −0.14 | −36.1% | 0.94 | 1279 | 65.4 |
| `ANCHOR_FILL=1` | **−12.6%** | −0.03 | −28.9% | 0.97 | 1274 | 66.9 |
| long-only | **+141.2%** | 0.83 | −25.6% | 1.55 | 780 | 73.7 |
| long-only + `ANCHOR_FILL=1` | **+104.8%** | 0.68 | −26.4% | 1.41 | 793 | 73.6 |

**This result is uncomfortable and is reported unmodified.** Correcting the
defect *improves* the bidirectional book (−23.8% → −12.6%) but *degrades* the
long-only book (+141.2% → +104.8%, Sharpe 0.83 → 0.68).

Interpretation: the anchor error acts as a ±2% random jitter on the exit grid.
Because entries are dip-buys that frequently bounce overnight, the jitter
systematically **tightens targets and loosens stops** — which flatters a
high-win-rate mean-reversion book in a bull market. Part of the long-only
"edge" reported earlier is therefore an artifact of an implementation defect,
not a property of the signal.

**Benchmark context that was previously under-stated:** SPY returned **+229%
(Sharpe 0.93)** over this same window. Long-only at +141% (Sharpe 0.83) —
and +105% (Sharpe 0.68) once corrected — **underperforms buy-and-hold on the
full sample on both raw and risk-adjusted terms.** The earlier "beats SPY"
claim held only inside the 2024–2026 sub-window.

---

## 6. Phase 1 remediation — IMPLEMENTED

All six items are implemented in `jp_agent.py` and covered by
`tests/test_execution.py` (22 assertions, `FakeBroker` mock, no network).

**Rule #1 compliance.** None of these changes touch alpha logic. No signal,
threshold, sizing rule, exit grid or universe member was altered. They change
only *when* state is written and *what* the system does when the broker
disagrees with it. The one behavioural judgement call is documented in §6.1.

| # | Defect | Status | Implementation |
|---|---|---|---|
| 1 | EXEC-3 | ✅ fixed | `make_coid()` → `JPV4-{SYM}-{L\|S}-{TAG}-{YYYYMMDD}-{seq}`, `COID_PREFIX = "JPV4"` |
| 2 | EXEC-1 | ✅ fixed | `execute_orders` rewritten fill-driven; `confirm_order()` polls `/v2/orders/{id}`; state written only from a confirmed fill |
| 3 | EXEC-4 | ✅ fixed | `cancel_all_pending_orders()` scoped by coid prefix; blanket DELETE moved behind `EMERGENCY_CANCEL_ALL=1` |
| 4 | EXEC-5 | ✅ fixed | `reconcile_state` returns `(state, report)`; `reconcile_is_clean()` gates `process_entries` in `main()` |
| 5 | EXEC-6 | ✅ fixed | orphans detected, direction inferred from position sign, counted toward `n_longs`/`n_shorts`/sector limits, symbol blocked |
| 6 | EXEC-7 | ✅ fixed | partial fills recorded at the filled qty; entry remainder abandoned, exit remainder retried next run |
| — | EXEC-2 | ✅ fixed | `ANCHOR_ON_FILL` (V4 gate) + `fill_price` from `filled_avg_price`; exits anchor on the fill, not the reference price |

### 6.1 A defect found *during* Phase 1 — the exit-path orphan

`process_exits` mutated `state["positions"]` (decrementing `shares_remaining`,
deleting the position on a full exit) **before** the sell order was submitted.
If that order was then rejected or went unfilled, the position vanished from
state while the broker still held it — creating a silent orphan on the exit
path, i.e. EXEC-1 in its most damaging direction.

The obvious fix — defer the mutation until after the fill — was **rejected**,
because `process_entries` runs against the post-`process_exits` state and would
therefore see different capacity in the same session. That is an alpha-logic
change and Rule #1 forbids it.

Implemented instead: **snapshot-and-rollback.** `process_exits` deep-copies the
position into `order["_snapshot"]` and keeps its optimistic mutation, so
same-session entry behaviour is bit-identical. `execute_orders` restores the
snapshot if the exit does not fill, and reconciles it to the actual filled
quantity on a partial. Test: `ROLLBACK NVDA: exit did not fill (rejected) —
position restored to 100 shares`.

### 6.2 Idempotency

Deterministic coids make submission replay-safe. On a duplicate the broker
returns 409/422; `place_market_order` catches it and retrieves the existing
order via `/v2/orders:by_client_order_id` rather than raising or re-submitting.
Test: two submit attempts → **one** broker order, one position.

### 6.3 Halt semantics

The gate halts **entries only**. Exits are never halted — a reconciliation
failure must not trap the book in its existing risk. Share-count drift alone
(the benign partial-fill case) does not halt; `missing_at_broker`, `orphans` and
`direction_conflict` do.

### 6.4 What Phase 1 does *not* fix

RISK-1 (Phase 2), REPRO-1 (Phase 19) and DATA-1 (Phase 4) are untouched. Phase 1
also cannot be validated against a live broker from the backtest: the tests
exercise a mock, so they prove the *state machine* is correct, not that Alpaca's
error codes are exactly as assumed.

## 7. Versioning convention (introduced)

- `JP_ALPHA_V3_FROZEN` — current live logic. Immutable reference. Baseline regression must reproduce −23.83% / 1279 trades / PF 0.94 on 2019-01-01 → 2026-08-29.
- Any change to alpha logic ⇒ new version id (`JP_ALPHA_V4_*`) with a documented diff and a fresh full validation run.
- Correctness fixes are gated behind an explicit env toggle and reported as a separate line in every results table, never folded into the baseline.
