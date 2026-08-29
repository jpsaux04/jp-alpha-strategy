# DEPLOYMENT DECISION — JP_ALPHA_V4_LONGONLY_STOPATR2

**Date:** 2026-08-28 · **Decided by:** JP (commissioner) · **Implemented by:** research/engineering
**Status:** DEPLOYED · **Recommendation at time of deployment: DO NOT DEPLOY**

This document exists so that the decision cannot later be misremembered. The
evidence below was produced *before* deployment and was known at the time.

---

## 1. What was deployed

`JP_ALPHA_V4_LONGONLY_STOPATR2` — backtest-equivalent to research variant
`lo_atr2` with `ANCHOR_FILL=1`. Three changes from V3, of two different kinds:

| # | change | kind | rationale |
|---|---|---|---|
| 1 | **Long-only** — short entries disabled | alpha logic | Phase 7 identified the mechanism: no edge in any regime, volatility bucket, sector or signal-strength bucket; signal is mildly *anti*-predictive; direction-symmetric exit grid on a positively-drifting process |
| 2 | **Stop = 2.0 × ATR** replaces fixed −8% | alpha logic | the "stopatr2" in the name; sets stop distance from volatility rather than a fixed percentage |
| 3 | **Exit levels anchored to actual fill** | **BUG FIX (EXEC-2)** | exits were anchored to the reference price the order was sized from, not the price paid |

**Change 3 is not optional.** The backtest evidence for this variant was
generated with `ANCHOR_FILL=1`. Shipping 1 and 2 without 3 would deploy a
configuration that was never tested. It is nonetheless recorded as a bug fix,
not an optimisation, and is reported as a separate line in every results table
(`PERFORMANCE_ATTRIBUTION.md` §7).

### Implementation

All three are gated on `STRATEGY_VERSION` (`jp_agent.py`). Setting
`STRATEGY_VERSION=JP_ALPHA_V3_FROZEN` restores V3 behaviour exactly — including
making no extra broker API call for the fill lookup. Verified by
`tests_smoke_v4.py`, which stubs all order submission and asserts:

- V4 emits no short orders; V3's short path remains reachable
- the same synthetic position stops at **95.00** under V4 (fill 101.00 − 2×ATR 3.00)
  and at **92.00** under V3 (ref 100.00 × 0.92), and the trigger fires accordingly
- no order reaches the broker during the test

**Limitation of the smoke test:** 2026-08-28 was a zero-signal day, so short
suppression is verified *structurally* (flag + source gate + assertion on the
emitted order list) rather than behaviourally against a live short signal.

---

## 2. The evidence against deployment

Five independent methods. None finds a statistically distinguishable edge over
passive beta.

| phase | method | result for this variant |
|---|---|---|
| **8** | FF5+MOM regression, Newey-West | alpha **+2.26%/yr, t = 0.57**, CI [−5.44%, +9.96%]; beta 0.546, R² 0.501 |
| **6** | cost decomposition | 215× turnover; costs at 16bps consume the **entire** alpha point estimate |
| **11** | block bootstrap | 1-in-20 drawdown **−45.7%** vs observed −26.6% — backtest understates the 5% tail by ~1.7× |
| **11b** | paired block bootstrap vs beta-matched book | **+2.24%/yr, CI [−6.35%, +11.53%]**, P(>0) = 68% — does not beat its own replicating portfolio |
| **10** | nested walk-forward | an honest walk-forward selects this variant in **0 of 8** folds; the selection procedure returns **−2.14%/yr vs simply committing to it** |

Additional findings that bear directly on this variant:

- **It is not even the best variant in sample.** Widening the grid to
  `STOP_ATR = 1.5` yields `lo_atr15`: +160.5% / Sharpe 0.98 / MaxDD −19.4%,
  dominating `lo_atr2` (+152.4% / 0.88 / −25.8%) on all three. The choice of
  2.0 was an artifact of which values were tried.
- **It trails SPY out of sample**: stitched OOS Sharpe 1.03 vs SPY 1.21, CAGR
  +15.34% vs +20.34%, at comparable drawdown.
- **Selection bias was never escaped.** It was chosen because it won the OOS
  window, which consumed that window (`PERFORMANCE_ATTRIBUTION.md` §7). Phase 10
  is the remedy, and it does not vindicate the choice.

### What the evidence does *not* say

It does not say the variant is broken or that it will lose money. Its point
estimates are positive and its risk profile is reasonable for a long equity
book. The finding is narrower and more specific: **it is not distinguishable
from 0.55–0.60 beta, and its estimated edge is the same size as its costs.**

Statistical power is the binding constraint. Residual vol ~11.1%/yr over 7.2
years gives SE ≈ 3.9%/yr on annualised alpha, so only |alpha| > 7.9%/yr was
ever detectable. A genuine 1–3%/yr edge would be invisible here. **"Not
significant" means not demonstrated, not proven absent.**

---

## 3. Why it was deployed anyway

The commissioner instructed deployment after being shown the above. That is a
legitimate exercise of authority over a paper-trading account: the downside is
bounded, the configuration is strictly less risky than the V3 book it replaces
(long-only, no borrow, no unlimited-loss exposure), and running it forward
generates genuinely out-of-sample data that no amount of further backtesting
can produce.

**The honest framing is that this is not a bet on demonstrated edge. It is the
start of a live out-of-sample test**, and it should be judged as such.

---

## 4. What must be true for this to have been the right call

Pre-registered, so it cannot be rationalised later:

1. **Benchmark is not zero.** V4 must be judged against a 0.6× SPY position, not
   against "did it make money". Making money in a rising market is the null
   hypothesis, not the result.
2. **Sample size.** At SE ≈ 3.9%/yr, distinguishing a 2%/yr edge needs decades.
   No conclusion drawn before **3 years** of live data will be statistically
   meaningful. Interim reviews are for *risk and execution*, not for edge.
3. **Drawdown tolerance must be set from Phase 11, not the backtest.** Plan for
   **−45%**, not −26.6%. A −45% drawdown is a normal 1-in-20 outcome for this
   configuration and must not trigger abandonment as if it were evidence of
   failure.
4. **Costs must be measured, not assumed.** Realised slippage should be logged
   per fill and compared against the 10bps assumption. At 215× turnover, each
   1bp is ~0.13%/yr. If realised slippage exceeds ~16bps, the edge estimate is
   fully consumed and V4 should be retired.

## 5. Kill criteria

Retire V4 and revert to `STRATEGY_VERSION=JP_ALPHA_V3_FROZEN` (or to cash) if
any of the following occurs:

- realised slippage sustained above **20bps/leg**
- drawdown beyond **−50%** (outside the bootstrap 95% band)
- cumulative underperformance vs 0.6× SPY exceeding **−15%** over any rolling
  12-month period
- any execution or reconciliation defect from `ARCHITECTURE_AUDIT.md`
  (EXEC-1, EXEC-3..7) causing a position the state file does not know about

---

## 6. Open engineering risk carried into production

Deploying V4 does **not** close the Phase 1 remediation items. The following
were identified in Phase 0 and remain **unfixed** in the deployed system:

| id | issue |
|---|---|
| EXEC-1 | orders carry no `client_order_id` — no idempotency; a retry can double-fill |
| EXEC-3 | `cancel_all_pending_orders` issues a blanket `DELETE /v2/orders` |
| EXEC-4 | `reconcile_state` never repairs `entry_price` and ignores broker-only positions |
| EXEC-5 | order `status` is logged but never branched on — a rejected order still writes a position |
| EXEC-6 | no halt gate on broker/state divergence |
| EXEC-7 | partial fills are not modelled |

**EXEC-5 interacts directly with change 3.** If a fill cannot be confirmed,
`get_fill_price` returns `None` and the anchor falls back to the reference
price — i.e. silently back to V3 behaviour for that position. This is
fail-safe rather than fail-loud, and is a deliberate choice pending Phase 1.

Legacy short positions opened under V3 are **not** force-liquidated. They wind
down through the unchanged V3 short-exit path. At deployment the book held one:
**UNH −23 @ 433.14**.

---

## 7. Reverting

```bash
# immediate revert, no code change
STRATEGY_VERSION=JP_ALPHA_V3_FROZEN /root/jp_strategy/venv/bin/python jp_agent.py
# or persistently, in the cron entry / .env
```
