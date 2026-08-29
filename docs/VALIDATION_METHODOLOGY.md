# VALIDATION METHODOLOGY — Phases 6 & 11

**Date:** 2026-08-29 · **Window:** 2019-01-01 → 2026-08-29 (1,864 daily obs, 7.4 yrs)
**Reproduce:**
```
python3 research/cost_stress.py  f_base a_lo_base s2_full_fix
python3 research/bootstrap_mc.py <prefix> [--slip 10] [--borrow 0.01]
python3 research/bench_boot.py   <prefix> [--slip 10] [--borrow 0.01]
```
Seed 42 throughout · 10,000 resamples · circular block length 21d (~1 month)

---

## 0. What these two phases were for

Phase 8 established that no configuration shows statistically significant alpha.
Phases 6 and 11 ask the two follow-up questions that determine whether that
verdict is *soft* (no edge demonstrated) or *hard* (edge affirmatively ruled out):

- **Phase 6 — do realistic costs eliminate the point estimate?** Phase 8's
  best case was +2.26%/yr alpha. If costs exceed that, the point estimate is
  gone before any inference is needed.
- **Phase 11 — how wide is the uncertainty on the observed path?** A single
  7.4-year backtest is one draw. Resampling asks what else could plausibly
  have happened.

Both are run on **net** returns. A distribution built on gross returns answers
a question nobody can trade.

---

## 1. Phase 6 — cost model

Costs are decomposed rather than applied as a single blended haircut, so each
can be stressed independently and the dominant term identified.

| component | treatment | stressed? |
|---|---|---|
| commission | Alpaca equities $0 | no (kept explicit in case venue changes) |
| SEC §31 | 27.8 / $1,000,000 principal, **sells only** | no (statutory) |
| FINRA TAF | $0.000166/share, cap $8.30/trade, sells only | no (statutory) |
| slippage/impact | symmetric adverse bps on **both** legs | 0–100 bps |
| short borrow | annual_rate × notional × days/360 | 0.5%–25% |
| short dividends | yield 1.55% × notional × days/365 | subsidy removal |

**On the dividend term.** `backtest.py` uses `auto_adjust` prices, so dividends
are folded into the price series: longs silently receive them, shorts are
silently *not* charged them (`RESEARCH_AUDIT.md §1.3`). This column removes an
accounting subsidy; it is not a newly invented cost.

### 1.1 Turnover is the headline

| config | two-way notional | × initial capital | slippage as % of all costs |
|---|---|---|---|
| `f_base` | $23,661,493 | **236.6×** | 72.3% |
| `a_lo_base` | $19,250,575 | **192.5×** | 98.5% |
| `s2_full_fix` | $21,537,535 | **215.4×** | 98.5% |

At ~200× turnover, **each 1bp of round-trip slippage costs ~0.13%/yr of CAGR.**
Statutory fees are irrelevant (1–1.5% of costs). Borrow is minor. The entire
cost question is *execution quality*, and the strategy is maximally exposed to it.

### 1.2 Net CAGR by slippage (borrow 0.5%)

| slip | `f_base` | `a_lo_base` | `s2_full_fix` |
|---|---|---|---|
| 0 bp | −4.92% | +12.62% | +13.31% |
| 5 bp | −7.32% | +12.00% | +12.64% |
| **10 bp** | **−10.18%** | **+11.36%** | **+11.95%** |
| **20 bp** | **−18.75%** | **+10.00%** | **+10.48%** |
| 50 bp | capital exhausted | +5.12% | +5.09% |
| 100 bp | capital exhausted | −9.34% | −12.69% |

The `nan` CAGRs in the raw output are not a bug: equity goes **≤ 0**. Under 50bps
the live configuration does not underperform — it is wiped out. Sharpe becomes
meaningless there and should be ignored (it turns *positive* at 100bps purely
because the curve is monotonically destroyed).

### 1.3 The finding that matters

Costs at a defensible 10–20bps consume **1.38%–2.85%/yr** for the long
configurations. Phase 8's alpha point estimate was **+2.26%/yr (t = 0.57)**.

> **Realistic execution costs are the same size as the entire alpha point
> estimate.** Net alpha is at or below zero at any slippage assumption above
> ~16bps. This does not require the statistical argument at all.

Breakeven slippage for `s2_full_fix` vs its own gross result is ~85bps, but
breakeven *against the alpha estimate* is ~16bps — and 16bps round-trip on
mega-cap names at this size is optimistic, not conservative.

---

## 2. Phase 11 — three resampling schemes

Each answers a different question and each has a different failure mode. Using
only one would be misleading.

| | scheme | question | destroys |
|---|---|---|---|
| **A** | iid bootstrap of position returns | is mean position return ≠ 0? | serial dependence, overlap |
| **B** | circular block bootstrap of daily returns, block 21d | what is the distribution of *path* outcomes? | nothing material |
| **C** | Monte Carlo reorder of realised daily returns | how much of the drawdown was path luck? | nothing (holds distribution fixed) |

**B is the honest basis for path statistics.** iid resampling destroys
volatility clustering and thereby materially *understates* drawdown.

**A note on scheme C.** Its CAGR distribution is degenerate — 5th = 50th = 95th
percentile — and this is correct, not a bug: reordering a set of returns
preserves their product, hence the terminal value and hence CAGR. Scheme C
therefore isolates **MaxDD as the only path-dependent statistic**, which is
exactly its purpose. Any implementation showing dispersion in reordered CAGR
has a bug.

### 2.1 Block bootstrap — NET returns (10bps / 1.0% borrow)

| | `f_base` | `a_lo_base` | `s2_full_fix` |
|---|---|---|---|
| observed CAGR | −10.65% | +11.36% | +11.95% |
| mean CAGR | −10.40% | +11.54% | +12.12% |
| 5th pct CAGR | −20.51% | +0.57% | +1.61% |
| 95th pct CAGR | +0.38% | +22.29% | +22.56% |
| **P(CAGR > 0)** | **5.5%** | **96.0%** | **97.0%** |
| P(Sharpe > 0.5) | 0.3% | 73.6% | 76.9% |
| mean MaxDD | −64.4% | −30.2% | −28.8% |
| 95th-worst MaxDD | −84.3% | −48.0% | −45.7% |
| **P(MaxDD worse −35%)** | 97.5% | **26.9%** | **21.6%** |
| **P(ruin, −50% DD)** | **83.5%** | 3.7% | 2.6% |

### 2.2 What the drawdown numbers actually say

The observed MaxDD is **not** a reasonable planning figure.

| config (net) | observed MaxDD | median bootstrap MaxDD | 95th-worst |
|---|---|---|---|
| `a_lo_base` | −26.5% | −28.7% | **−48.0%** |
| `s2_full_fix` | −26.6% | −27.6% | **−45.7%** |
| `f_base` | −64.8% | −66.0% | −84.3% |

Comparing scheme B against scheme C separates the two causes:

- **C (reorder only):** `s2_full_fix` median −25.5%, 95th-worst −37.1%
- **B (reorder + resample):** median −27.6%, 95th-worst **−45.7%**

The gap between −37.1% and −45.7% is the contribution of **volatility
clustering and return-sequence dependence** — i.e. the part an iid model would
have missed. Roughly **one fifth of the tail drawdown risk is structural, not
sampling noise.**

**Planning implication:** a −45% drawdown is a 1-in-20 outcome for the V4
candidate, against an observed −26.6%. The backtest's drawdown understates the
risk by a factor of ~1.7 at the 5% tail.

### 2.3 Position bootstrap (scheme A)

| config | book | n | mean ret | 95% CI | P(mean>0) | win rate |
|---|---|---|---|---|---|---|
| `f_base` | ALL | 688 | −0.058% | [−0.591, +0.492] | 41.3% | 41.9% |
| `f_base` | LONG | 358 | +1.536% | **[+0.755, +2.310]** | 100.0% | 51.4% |
| `f_base` | SHORT | 330 | −1.787% | **[−2.474, −1.093]** | 0.0% | 31.5% |
| `s2_full_fix` | LONG | 420 | +1.422% | **[+0.754, +2.097]** | 100.0% | 50.7% |

Confirms Phase 7 at position level: long significantly positive, short
significantly negative, combined indistinguishable from zero. **These CIs are
gross of costs and assume iid positions — they are the strategy's best case and
should not be read as evidence of edge.** Section 3 is the test that matters.

---

## 3. Phase 11b — the benchmark-relative bootstrap

**This is the section that resolves the brief's core question.**

P(CAGR > 0) = 97% for the V4 candidate reads like a strong result. It is not a
result at all. A 0.60-beta long book in the 2019–2026 market clears that bar
mechanically. The right test is a **paired** block bootstrap — the same block
indices applied to strategy and benchmark, preserving their contemporaneous
correlation — on the *difference*.

Two benchmarks are used:

1. **SPY buy & hold** — the comparison a retail investor actually faces.
2. **Beta-matched passive** = β × SPY + (1−β) cash — the honest comparison,
   since a 0.6-beta book should not be charged for failing to deliver 1.0 beta.

### 3.1 Results (NET, 10bps / 1.0% borrow, 10,000 paired sims)

| config | est. β | strat − SPY | 95% CI | P(>0) | strat − β-matched | 95% CI | P(>0) |
|---|---|---|---|---|---|---|---|
| `s2_full_fix` (**V4**) | 0.603 | **−4.05%** | [−14.77, +6.58] | **23.5%** | **+2.24%** | **[−6.35, +11.53]** | 68.0% |
| `a_lo_base` | 0.613 | −4.66% | [−14.72, +5.14] | 17.8% | +1.46% | [−6.94, +10.49] | 62.1% |
| `f_base` (live) | 0.395 | **−26.65%** | **[−42.48, −10.86]** | **0.1%** | **−16.98%** | **[−28.46, −4.48]** | **0.3%** |

Sharpe, paired:

| config | strat Sharpe | SPY Sharpe | difference | 95% CI | P(>0) |
|---|---|---|---|---|---|
| `s2_full_fix` | +0.80 | +0.89 | −0.09 | [−0.59, +0.44] | 36.7% |
| `a_lo_base` | +0.76 | +0.89 | −0.13 | [−0.61, +0.36] | 29.9% |
| `f_base` | −0.43 | +0.89 | **−1.33** | **[−2.18, −0.53]** | 0.1% |

### 3.2 Reading these

1. **The live strategy is significantly worse than passive.** Both CIs exclude
   zero against both benchmarks, and the Sharpe deficit CI is
   **[−2.18, −0.53]** — excludes zero comfortably. This is not "failed to add
   value"; it is *destroying* value at a statistically detectable rate. It
   corroborates the Phase 8 negative-alpha finding (t ≈ −2.0) via a completely
   independent, distribution-free route.

2. **The V4 candidate does not beat SPY.** P(beating SPY) = 23.5%; the CI on the
   difference is [−14.77%, +6.58%] and centred at −4.05%. The Sharpe difference
   is centred negative.

3. **The V4 candidate does not beat its own replicating portfolio either.**
   +2.24%/yr with CI **[−6.35%, +11.53%]** — spanning zero, P(>0) = 68%. This is
   the same conclusion as Phase 8's +2.26%/yr, t = 0.57, reached without any
   parametric assumption. Two independent methods agreeing on the same number is
   the strongest statement available here: **the effect is real in the point
   estimate and indistinguishable from zero in the inference.**

4. **The beta-matched book has the same Sharpe as SPY by construction** (0.89 in
   both rows) because cash is modelled at 0% and scaling does not change Sharpe.
   That is expected; it is listed for transparency, not as a finding.

---

## 4. Statistical honesty — what these phases cannot do

1. **One regime, one path.** All 10,000 resamples are drawn from a single
   2019–2026 bull market. Block bootstrap widens the CI around *that*
   distribution; it cannot generate a regime the sample never contained. The
   drawdown tails in §2.2 are therefore still **optimistic** — they exclude any
   sustained bear market.
2. **Bootstrap does not fix selection bias.** `s2_full_fix` was chosen because it
   won the OOS window (`PERFORMANCE_ATTRIBUTION.md §7`). Resampling its returns
   10,000 times reproduces that selection 10,000 times. Phase 10 (nested
   walk-forward) is the only remedy, and it is not yet run.
3. **Survivorship-biased universe** (`RESEARCH_AUDIT.md §1.4`) inflates every
   number above.
4. **Cost model is static.** Slippage is applied as a constant bps haircut. Real
   impact is state-dependent — it rises exactly when a mean-reversion strategy
   most wants to trade. Phase 14 (capacity) is required to bound this.
5. **`f_base` net results assume the position can be held through a −65%
   drawdown.** In practice financing and risk limits would force liquidation
   first, so the realised outcome would be worse, not better.

---

## 5. Verdict from Phases 6 and 11

**On the live strategy (`JP_ALPHA_V3_FROZEN`):** the evidence is now conclusive
and comes from three independent methods — factor regression (t ≈ −2.0), cost
analysis (net CAGR −10.65%), and distribution-free paired bootstrap
(P(beating passive) = 0.3%). It underperforms a beta-matched passive book with
83.5% probability of a −50% drawdown. **There is no defensible case for
continuing to run it other than as an instrumented control.**

**On the V4 candidate (`s2_full_fix`):** it is a competent 0.6-beta long equity
book. It does not beat SPY (P = 23.5%), does not beat its own replicating
portfolio at any conventional confidence (P = 68%, CI spans zero), and carries a
1-in-20 drawdown of −45.7% versus an observed −26.6%. Its entire alpha point
estimate (+2.24%/yr) is inside the range consumed by realistic execution costs
at 16bps.

The V4 candidate is not *broken*. It is **not distinguishable from beta**, and
its costs are the same size as its estimated edge. Phases 10, 12 and 14 remain
worth running, but — as Phase 8 already noted — their job is now to quantify how
confident we can be that the edge is absent, not to validate it.

---

# PHASE 10 — NESTED WALK-FORWARD

**Reproduce:**
```
bash research/wf_grid.sh                                  # 14-variant candidate grid
python3 research/walk_forward.py [--crit sharpe|ret]
```

## 6. What is actually being validated

This strategy **fits no parameters**. Nothing is estimated from data. The only
thing ever selected using historical performance is the **variant choice** —
which is exactly how `longonly_stopatr2` became the V4 candidate
(`PERFORMANCE_ATTRIBUTION.md` §7).

So the object under test in Phase 10 is not a strategy. It is a **selection
procedure**:

> "look at history, pick the best-performing variant, trade it forward"

Two lookback rules are tested, because the choice of lookback is itself a
researcher degree of freedom and hiding it would be dishonest:

- **A. EXPANDING** — select on all history strictly before the test window
- **B. TRAILING12** — select on the 12 months before the test window

Both are compared against committing to a single variant, against the frozen
live control, against SPY, and against an **ORACLE** (best variant per fold
chosen with hindsight) which is an unattainable upper bound included to show how
much of any in-sample result is pure hindsight.

**Design:** 14 variants, each simulated once continuously over the full window
with `ANCHOR_FILL=1`; 8 **non-overlapping** 6-month test folds (2022-07 →
2026-07); folds sliced from the continuous path rather than re-run, so no fold
pays the 60-session indicator warmup and position state is not artificially
reset 8 times. All figures **net** of Phase 6 costs (10bps/leg, 1% borrow).

## 7. The candidate grid — the winner already moved

| variant | full-sample return | Sharpe | MaxDD |
|---|---|---|---|
| `lo_atr15` (STOP_ATR 1.5) | **+160.5%** | **0.98** | −19.4% |
| `lo_atr2_nsc` | +188.0% | 0.84 | −31.3% |
| `lo_nsc` | +156.3% | 0.73 | −34.1% |
| **`lo_atr2` (= longonly_stopatr2)** | **+152.4%** | **0.88** | −25.8% |
| `lo_atr2_t316` | +149.7% | 0.88 | −27.2% |
| `lo_trail3` | +137.2% | 0.83 | −23.3% |
| `lo_atr25` | +113.4% | 0.70 | −25.3% |
| `lo` (plain long-only) | +104.8% | 0.68 | −26.4% |
| `base_ls` (frozen live) | −12.6% | −0.03 | −28.9% |

**On a grid this size, `longonly_stopatr2` is no longer the best variant.**
Merely widening the search to include STOP_ATR = 1.5 produces a variant that
beats it on return, Sharpe *and* drawdown. The original selection of ATR 2.0 was
an artifact of which values happened to be tried.

## 8. Fold-by-fold results (criterion = Sharpe)

| test fold | A: pick, sel→test | B: pick, sel→test | oracle | `lo_atr2` | SPY |
|---|---|---|---|---|---|
| 2022-07→2023-01 | `lo_atr15` +0.58→**−0.61** | `lo_atr15` +0.64→**−0.61** | `lo_t320` +0.61 | −0.45 | +0.31 |
| 2023-01→2023-07 | `lo_atr15` +0.41→+0.96 | `ls_atr2` +0.22→−0.08 | `lo_nsc` +2.37 | +0.98 | +2.26 |
| 2023-07→2024-01 | `lo_atr15` +0.47→+0.82 | `lo_trail4` +0.98→+0.24 | `lo_nsc` +1.98 | +0.91 | +1.40 |
| 2024-01→2024-07 | `lo_atr15` +0.49→+2.48 | `lo_nsc` +2.18→+3.04 | `lo_trail4` +3.67 | +2.39 | +2.76 |
| 2024-07→2025-01 | `lo_atr15` +0.61→+4.30 | `lo_nsc` +2.46→+2.97 | `lo_atr2` +4.56 | +4.56 | +1.19 |
| 2025-01→2025-07 | `lo_atr15` +0.83→−0.05 | `lo_atr2` +3.42→**−0.15** | `lo_atr2_t316` +0.62 | −0.15 | +0.60 |
| 2025-07→2026-01 | `lo_atr15` +0.72→+3.02 | `lo_atr15` +1.21→+3.02 | `lo_atr2_t316` +3.29 | +2.93 | +1.96 |
| 2026-01→2026-07 | `lo_atr15` +0.81→−0.04 | `lo_atr2_t316` +1.44→+1.22 | `lo_atr2_nsc` +1.22 | +0.94 | +1.46 |

## 9. Stitched out-of-sample result

8 non-overlapping folds concatenated — a genuine tradeable path, no day reused.

| | CAGR | Sharpe | MaxDD | total |
|---|---|---|---|---|
| selection procedure A (expanding) | +13.06% | +0.95 | −19.2% | +62.9% |
| selection procedure B (trailing 12m) | +14.41% | +0.95 | −20.8% | +70.8% |
| **always `lo_atr2` (longonly_stopatr2)** | **+15.34%** | **+1.03** | −20.2% | +76.4% |
| always `base_ls` (frozen live control) | −13.73% | −0.60 | −48.1% | −44.4% |
| **SPY buy & hold** | **+20.34%** | **+1.21** | **−18.8%** | **+108.8%** |
| ORACLE — hindsight best per fold | +33.41% | +1.96 | −13.4% | +214.6% | *unattainable* |

Paired daily differences vs SPY over the stitched OOS path:

| | vs SPY | t |
|---|---|---|
| procedure A | −6.61%/yr | −1.01 |
| procedure B | −5.21%/yr | −0.75 |
| always `lo_atr2` | −4.47%/yr | −0.70 |
| **`base_ls` (live)** | **−32.41%/yr** | **−2.82** |
| procedure A **minus always-`lo_atr2`** | **−2.14%/yr** | −0.97 |

## 10. Findings

1. **The honest selection procedure never picks `longonly_stopatr2`.**
   Under criterion = Sharpe it chose `lo_atr15` in **8 of 8** folds and
   `lo_atr2` in **0 of 8**. Under criterion = return it chose `lo_atr2` in
   **0 of 8** under both rules. The V4 candidate is not what a
   disciplined walk-forward would have selected — it is what a single
   backward-looking pass over one OOS window happened to surface.

2. **The selection procedure is worth less than nothing.** Procedure A returns
   **−2.14%/yr versus simply committing to `lo_atr2`** and −6.61%/yr versus
   SPY. Searching the variant space and acting on the result actively destroyed
   value. This is the cleanest available demonstration that the apparent
   superiority of any single variant in this family is hindsight.

3. **Nothing beats SPY out of sample.** Best achievable stitched OOS Sharpe is
   1.03 (`always lo_atr2`) against SPY's **1.21**, at a similar drawdown. Every
   configuration trails on both raw and risk-adjusted terms, though — note —
   none of the long variants trails *significantly* (|t| < 1.0).

4. **The live strategy is significantly worse than SPY out of sample**:
   −32.41%/yr, **t = −2.82**, MaxDD −48.1%. This is now the fourth independent
   method returning the same verdict (Phase 8 regression t ≈ −2.0; Phase 6 net
   CAGR −10.65%; Phase 11b paired bootstrap P = 0.3%).

5. **Selection instability is severe.** Procedure B changed its pick in **6 of 7**
   fold transitions and used 6 distinct variants across 8 folds. A selection
   rule that cannot hold an opinion for two consecutive periods is not
   identifying a durable property of the market.

6. **The oracle gap is the size of the entire result.** Hindsight-best gives
   Sharpe 1.96; the best honest procedure gives 0.95. **Roughly half of the
   apparent performance of any selected variant is unavailable in advance.**

## 11. A finding that must NOT be over-read

Procedure A's Sharpe *improved* from selection window (+0.61) to test window
(+1.36). This looks like negative selection bias — as if the procedure
generalised better than it fit.

**It is not evidence of edge.** It is regime. The selection windows are
anchored in 2019–2022 (including the 2022 bear); the test windows are
2023–2026, an exceptionally strong market in which almost any long-biased book
scored well. The correct reading is that **the level of Sharpe in this sample is
governed by market direction, not by variant choice** — which is the same
conclusion Phase 8 reached from factor loadings.

The internally-consistent measurement of selection bias is procedure B, which
holds regime roughly fixed by using a trailing window: it degrades **+1.57 →
+1.21** (Sharpe, −23%) and **+0.28 → +0.15** (return, −45%).

## 12. Consequence for the V4 deployment decision

Phase 10 does **not** show `longonly_stopatr2` to be broken. It shows something
more specific and more damaging to the case for deploying it:

- it is **not the best variant** even in sample once the grid is widened;
- it is **never selected** by an honest walk-forward;
- committing to it beats searching for it, which means its selection carried
  **no information**;
- and it still **trails SPY** out of sample on both return and Sharpe.

Combined with Phase 11b (does not beat its own beta-matched replicating book,
CI [−6.35%, +11.53%]) and Phase 6 (costs at 16bps consume the entire alpha point
estimate), the evidence against deployment is now consistent across five
independent methods.
