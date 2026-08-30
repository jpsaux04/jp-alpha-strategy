# JP ALPHA STRATEGY — CONSOLIDATED RESEARCH REPORT

This is the single authoritative research record for the JP Alpha strategy. It
was assembled deliberately from six separate markdown documents —
`RESEARCH_AUDIT.md`, `VALIDATION_METHODOLOGY.md`, `PERFORMANCE_ATTRIBUTION.md`,
`FACTOR_ATTRIBUTION.md`, `ARCHITECTURE_AUDIT.md` and `DEPLOYMENT_DECISION.md` —
which previously lived side by side in `docs/`. Six parallel documents meant six
places to look, six places to go stale, and six places for the same number to be
quoted differently. They are now one document, one source of truth, and the
originals have been deleted rather than left as duplicates.

Each source document survives below as its own `PART`, with its original
headings, numbering, tables and wording preserved unchanged; only heading depth
was shifted so the parts nest correctly. Nothing was summarised, shortened or
rewritten. Where two source documents stated the same fact differently, both
statements were kept — the divergence is itself part of the record.

The two raw program-output artifacts, `docs/phase7_attribution_output.txt` and
`docs/phase8_factor_output.txt`, remain separate files and are referenced from
the parts below.

## Table of contents

- [PART 7 — DEPLOYMENT AMENDMENT: V5 (1.5×ATR stop)](#part-7--deployment-amendment-v5-15atr-stop)
- [PART 1 — RESEARCH AUDIT — JP Alpha Strategy](#part-1--research-audit--jp-alpha-strategy)
  - [1. Look-ahead audit (Rule #2)](#1-look-ahead-audit-rule-2)
  - [2. Correctness of the measured edge](#2-correctness-of-the-measured-edge)
  - [3. What Phase 0 did *not* find](#3-what-phase-0-did-not-find)
  - [4. Open research questions, in priority order](#4-open-research-questions-in-priority-order)
  - [5. Standing rules adopted](#5-standing-rules-adopted)
- [PART 2 — VALIDATION METHODOLOGY — Phases 6 & 11](#part-2--validation-methodology--phases-6--11)
  - [0. What these two phases were for](#0-what-these-two-phases-were-for)
  - [1. Phase 6 — cost model](#1-phase-6--cost-model)
  - [2. Phase 11 — three resampling schemes](#2-phase-11--three-resampling-schemes)
  - [3. Phase 11b — the benchmark-relative bootstrap](#3-phase-11b--the-benchmark-relative-bootstrap)
  - [4. Statistical honesty — what these phases cannot do](#4-statistical-honesty--what-these-phases-cannot-do)
  - [5. Verdict from Phases 6 and 11](#5-verdict-from-phases-6-and-11)
- [PHASE 10 — NESTED WALK-FORWARD](#phase-10--nested-walk-forward)
  - [6. What is actually being validated](#6-what-is-actually-being-validated)
  - [7. The candidate grid — the winner already moved](#7-the-candidate-grid--the-winner-already-moved)
  - [8. Fold-by-fold results (criterion = Sharpe)](#8-fold-by-fold-results-criterion--sharpe)
  - [9. Stitched out-of-sample result](#9-stitched-out-of-sample-result)
  - [10. Findings](#10-findings)
  - [11. A finding that must NOT be over-read](#11-a-finding-that-must-not-be-over-read)
  - [12. Consequence for the V4 deployment decision](#12-consequence-for-the-v4-deployment-decision)
- [PART 3 — PERFORMANCE ATTRIBUTION — Phase 5 & Phase 7](#part-3--performance-attribution--phase-5--phase-7)
  - [0. Phase 5 first — the trade count was wrong](#0-phase-5-first--the-trade-count-was-wrong)
  - [1. Headline: long vs short](#1-headline-long-vs-short)
  - [2. The mechanism — target asymmetry, not disasters](#2-the-mechanism--target-asymmetry-not-disasters)
  - [3. Hypothesis elimination](#3-hypothesis-elimination)
  - [4. A conditioning artifact that must not be mistaken for a finding](#4-a-conditioning-artifact-that-must-not-be-mistaken-for-a-finding)
  - [5. What this does and does not license](#5-what-this-does-and-does-not-license)
  - [6. Cautions that also apply to the *long* book](#6-cautions-that-also-apply-to-the-long-book)
  - [7. Interaction with EXEC-2 (measured)](#7-interaction-with-exec-2-measured)
- [PART 4 — FACTOR AND BETA ATTRIBUTION — Phase 8](#part-4--factor-and-beta-attribution--phase-8)
  - [The headline](#the-headline)
  - [Statistical power — what could we even have detected?](#statistical-power--what-could-we-even-have-detected)
  - [Factor exposures — where the returns actually come from](#factor-exposures--where-the-returns-actually-come-from)
  - [The comparison that matters](#the-comparison-that-matters)
  - [Consequences for the deployment decision](#consequences-for-the-deployment-decision)
  - [Caveats against over-reading this](#caveats-against-over-reading-this)
- [PART 5 — ARCHITECTURE AUDIT — JP Alpha Strategy](#part-5--architecture-audit--jp-alpha-strategy)
  - [1. File inventory](#1-file-inventory)
  - [2. Write map — who mutates what](#2-write-map--who-mutates-what)
  - [3. Broker API surface](#3-broker-api-surface)
  - [4. FINDINGS](#4-findings)
  - [5. Changes made during Phase 0](#5-changes-made-during-phase-0)
  - [6. Phase 1 remediation — IMPLEMENTED](#6-phase-1-remediation--implemented)
  - [7. Versioning convention (introduced)](#7-versioning-convention-introduced)
- [PART 6 — DEPLOYMENT DECISION — JP_ALPHA_V4_LONGONLY_STOPATR2](#part-6--deployment-decision--jp_alpha_v4_longonly_stopatr2)
  - [1. What was deployed](#1-what-was-deployed)
  - [2. The evidence against deployment](#2-the-evidence-against-deployment)
  - [3. Why it was deployed anyway](#3-why-it-was-deployed-anyway)
  - [4. What must be true for this to have been the right call](#4-what-must-be-true-for-this-to-have-been-the-right-call)
  - [5. Kill criteria](#5-kill-criteria)
  - [6. Engineering risk — closed by Phase 1](#6-engineering-risk--closed-by-phase-1)
  - [7. Reverting](#7-reverting)

---

## PART 1 — RESEARCH AUDIT — JP Alpha Strategy

*Consolidated from `docs/RESEARCH_AUDIT.md`.*

**Audit date:** 2026-08-29 · **Commit:** `c41b161` · **Phase 0 of 20.**

Working assumption throughout: *the strategy is overestimating its edge until
proven otherwise.*

---

### 1. Look-ahead audit (Rule #2)

#### 1.1 Signal causality — **PASS**

Every indicator was checked for non-causal construction. Grep across
`backtest.py` and `jp_agent.py` for `shift(-n)`, `center=True`, `bfill`,
`backfill`, and forward indexing returns **zero hits**.

| Feature | Construction | Information timestamp | Causal? |
|---|---|---|---|
| `RSI(14)` | `ewm(com=13)` on Close | close of day *d* | ✅ |
| `ATR(14)` | Wilder `ewm` on H/L/C | close of *d* | ✅ |
| `MA20` | `rolling(20).mean()` | close of *d* | ✅ |
| `long_disl` / `short_disl` | `(MA20 − C)/MA20` | close of *d* | ✅ |
| `vol_exhaust_*` | `rolling(20)` volume + 3-day patterns | close of *d* | ✅ |
| `bull_id` / `bear_id` | `(C − L)/(H − L)` | close of *d* | ✅ |
| SPY regime | `MA50` on SPY Close | close of *d* | ✅ |

`add_indicators` is applied to the full series and then row-sliced with
`.loc[d]`. This is safe **only because every operator is backward-looking** —
verified above. The same-bar use of day-*d* High/Low/Close is legitimate: the
decision is taken after the close, so the full bar is observable.

#### 1.2 Execution timing — **PASS**

Decision on close of *d* → order queued → filled at **open of *d+1***
(`backtest.py:267–277`). Exits are filled before entries within the same open.
No same-bar fill uses information derived from that bar's close. This is a
genuine strength.

#### 1.3 Corporate-action leakage — **FAIL**

`yf.download(..., auto_adjust=True)` returns **back-adjusted** prices. The
adjusted price on date *t* is a function of dividends and splits paid *after*
*t*. The backtest therefore trades a price series that did not exist in real
time.

Percentage *returns* are approximately correct, but **price levels are not**,
and the system uses levels in two places:

- `MIN_PRICE = 10.0` — a stock adjusted to $8 in 2019 may have actually traded
  at $13. The filter is applied with future knowledge.
- `calc_shares` — `int(risk_dollars / (1.5 × ATR))` and the 20% notional cap use
  absolute price, so historical share counts are wrong in level terms.

Additionally, dividends are folded into the price series rather than booked as
cash. Long positions receive an implicit total-return benefit and **short
positions are never charged the dividends they would owe** — a direct,
unmodelled subsidy to the short book, which is the very book under
investigation in Phase 7.

*Not yet remediated. Requires the Phase 3D corporate-action framework.*

#### 1.4 Survivorship bias — **FAIL**

`WATCHLIST` is 42 present-day large caps applied unchanged to 2019. Every
constituent survived and was chosen knowing that. There is no point-in-time
membership. Magnitude is unmeasured; it cannot be assumed small. The repository
must not describe any result as survivorship-bias-free. (Phase 4.)

#### 1.5 Parameter-selection leakage — **FAIL (self-inflicted, prior work)**

The exit-design ablation tested 9 variants on the same 7.6-year sample and
reported the winner. The subsequent walk-forward exposed exactly this: the
in-sample champion (`longonly_noscale_trail3`, +82.5% IS) fell to 4th
out-of-sample, while the *worst* in-sample long-only variant
(`longonly_stopatr2`, +31.2%, 6th of 6) became the **best** out-of-sample
(+75.0%, Sharpe 1.59). The ranking did not merely degrade — it inverted.

Any claim resting on picking a variant by in-sample performance is void.

#### 1.6 Benchmark leakage — **PASS with a caveat**

SPY is fetched over the same window and is not in the tradable universe. But
SPY's own adjusted series carries the same §1.3 back-adjustment issue, so
strategy-vs-benchmark comparisons are internally consistent rather than
absolutely correct.

---

### 2. Correctness of the measured edge

#### 2.1 The headline result was under-stated against the benchmark

Full window 2019-01-01 → 2026-08-29:

| | Return | Sharpe | MaxDD |
|---|---|---|---|
| **SPY buy & hold** | **+229%** | **0.93** | −33.7% |
| baseline (live, frozen) | −23.8% | −0.14 | −36.1% |
| long-only | +141.2% | 0.83 | −25.6% |
| long-only, EXEC-2 corrected | +104.8% | 0.68 | −26.4% |

**The strategy has never beaten buy-and-hold on the full sample**, in raw or
risk-adjusted terms, in any configuration tested. The earlier "beats SPY"
statement was true only inside the 2024–2026 sub-window and should not have
been presented without this line beside it.

#### 2.2 Part of the long-only edge is an implementation artifact

See [PART 5 §5](#5-changes-made-during-phase-0) / EXEC-2. Exit levels are anchored to the prior close
rather than the fill. Measured anchor error: σ ≈ 2%, >1% on 35% of trades,
against a +4%/−8% grid.

Correcting it moves long-only from **+141.2% (Sharpe 0.83) → +104.8% (Sharpe
0.68)**. Roughly a quarter of the long book's reported return depends on a
defect that systematically tightens targets and loosens stops for dip-buys that
bounce overnight.

This is a strong prior that the long-only result is **partly grid-noise
interaction, not signal**. It must be re-tested under Phases 10–12 before any
edge claim survives.

#### 2.3 Trade counts are inflated

1,279 "trades" are **exit fragments**, not positions. Tiered exits generate up
to 3 fragments per position; the true figure is ~688 positions. Every
per-trade statistic quoted so far (win rate 65.4%, expectancy −$19.02) is
computed on fragments and is therefore **not** a position-level statistic.
Fragment win rate is upward-biased because T1 fragments are wins by
construction. (Phase 5.)

#### 2.4 Risk model is internally inconsistent

Sizing uses `1% / (1.5 × ATR)`; the stop is a flat −8%. Intended risk equals
realised stop risk only when `1.5 × ATR ≈ 0.08 × price`. Otherwise per-position
risk is silently mis-scaled — low-vol names under-risked, high-vol names
over-risked. Combined with EXEC-2, the *actual* risk on any given position is
knowable only after the fact. (Phase 2.)

#### 2.5 Cost model coverage is incomplete

`costs.py` models implementation shortfall vs NBBO mid, SEC §31, FINRA TAF, and
a flat short-borrow assumption. Not modelled in the backtest: market impact,
financing, dividends owed on shorts (§1.3), or borrow-rate variation. The
single flat borrow rate is inadequate for a book whose short side is the
central research question. (Phase 6.)

---

### 3. What Phase 0 did *not* find

Reporting these explicitly so absence of evidence is not read as evidence:

- No look-ahead in signal construction (§1.1) — verified, not assumed.
- No same-bar execution leakage (§1.2).
- No evidence of results being hand-edited; the baseline reproduced bit-identically after instrumentation.
- Only one module can trade; every analytics/dashboard path is GET-only.
- No secrets in git history (audited previously; all key references are `os.environ` lookups).

---

### 4. Open research questions, in priority order

1. **Why does the short book destroy the combined strategy?** Unanswered.
   Attribution not yet built. Note that shorts currently receive an unmodelled
   dividend subsidy (§1.3), meaning their *true* performance is **worse** than
   measured — which deepens rather than explains the puzzle.
2. **Does the long edge survive EXEC-2 correction plus realistic costs,
   survivorship correction, and factor attribution?** §2.2 gives grounds for
   doubt.
3. **Is any of it distinguishable from long beta in a bull market?** All
   long-only variants posted Sharpe > 1.4 in 2024–2026 while SPY posted 1.30.
   Beta is not yet estimated, so no alpha claim is currently supportable.

### 5. Standing rules adopted

- Baseline regression (−23.83% / 1279 / PF 0.94 / Sharpe −0.14) must be
  reproduced by every future commit unless a documented fix intentionally
  changes it.
- Correctness fixes ship gated OFF and are reported as separate rows.
- No result is quoted without sample size and benchmark on the same line.
- Fragment-level and position-level statistics are never mixed.


---

## PART 2 — VALIDATION METHODOLOGY — Phases 6 & 11

*Consolidated from `docs/VALIDATION_METHODOLOGY.md`.*

**Date:** 2026-08-29 · **Window:** 2019-01-01 → 2026-08-29 (1,864 daily obs, 7.4 yrs)
**Reproduce:**
```
python3 research/cost_stress.py  f_base a_lo_base s2_full_fix
python3 research/bootstrap_mc.py <prefix> [--slip 10] [--borrow 0.01]
python3 research/bench_boot.py   <prefix> [--slip 10] [--borrow 0.01]
```
Seed 42 throughout · 10,000 resamples · circular block length 21d (~1 month)

---

### 0. What these two phases were for

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

### 1. Phase 6 — cost model

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
silently *not* charged them ([PART 1 §1.3](#13-corporate-action-leakage--fail)). This column removes an
accounting subsidy; it is not a newly invented cost.

#### 1.1 Turnover is the headline

| config | two-way notional | × initial capital | slippage as % of all costs |
|---|---|---|---|
| `f_base` | $23,661,493 | **236.6×** | 72.3% |
| `a_lo_base` | $19,250,575 | **192.5×** | 98.5% |
| `s2_full_fix` | $21,537,535 | **215.4×** | 98.5% |

At ~200× turnover, **each 1bp of round-trip slippage costs ~0.13%/yr of CAGR.**
Statutory fees are irrelevant (1–1.5% of costs). Borrow is minor. The entire
cost question is *execution quality*, and the strategy is maximally exposed to it.

#### 1.2 Net CAGR by slippage (borrow 0.5%)

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

#### 1.3 The finding that matters

Costs at a defensible 10–20bps consume **1.38%–2.85%/yr** for the long
configurations. Phase 8's alpha point estimate was **+2.26%/yr (t = 0.57)**.

> **Realistic execution costs are the same size as the entire alpha point
> estimate.** Net alpha is at or below zero at any slippage assumption above
> ~16bps. This does not require the statistical argument at all.

Breakeven slippage for `s2_full_fix` vs its own gross result is ~85bps, but
breakeven *against the alpha estimate* is ~16bps — and 16bps round-trip on
mega-cap names at this size is optimistic, not conservative.

---

### 2. Phase 11 — three resampling schemes

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

#### 2.1 Block bootstrap — NET returns (10bps / 1.0% borrow)

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

#### 2.2 What the drawdown numbers actually say

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

#### 2.3 Position bootstrap (scheme A)

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

### 3. Phase 11b — the benchmark-relative bootstrap

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

#### 3.1 Results (NET, 10bps / 1.0% borrow, 10,000 paired sims)

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

#### 3.2 Reading these

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

### 4. Statistical honesty — what these phases cannot do

1. **One regime, one path.** All 10,000 resamples are drawn from a single
   2019–2026 bull market. Block bootstrap widens the CI around *that*
   distribution; it cannot generate a regime the sample never contained. The
   drawdown tails in §2.2 are therefore still **optimistic** — they exclude any
   sustained bear market.
2. **Bootstrap does not fix selection bias.** `s2_full_fix` was chosen because it
   won the OOS window ([PART 3 §7](#7-interaction-with-exec-2-measured)). Resampling its returns
   10,000 times reproduces that selection 10,000 times. Phase 10 (nested
   walk-forward) is the only remedy, and it is not yet run.
3. **Survivorship-biased universe** ([PART 1 §1.4](#14-survivorship-bias--fail)) inflates every
   number above.
4. **Cost model is static.** Slippage is applied as a constant bps haircut. Real
   impact is state-dependent — it rises exactly when a mean-reversion strategy
   most wants to trade. Phase 14 (capacity) is required to bound this.
5. **`f_base` net results assume the position can be held through a −65%
   drawdown.** In practice financing and risk limits would force liquidation
   first, so the realised outcome would be worse, not better.

---

### 5. Verdict from Phases 6 and 11

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

## PHASE 10 — NESTED WALK-FORWARD

**Reproduce:**
```
bash research/wf_grid.sh                                  # 14-variant candidate grid
python3 research/walk_forward.py [--crit sharpe|ret]
```

### 6. What is actually being validated

This strategy **fits no parameters**. Nothing is estimated from data. The only
thing ever selected using historical performance is the **variant choice** —
which is exactly how `longonly_stopatr2` became the V4 candidate
([PART 3 §7](#7-interaction-with-exec-2-measured)).

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

### 7. The candidate grid — the winner already moved

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

### 8. Fold-by-fold results (criterion = Sharpe)

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

### 9. Stitched out-of-sample result

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

### 10. Findings

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

### 11. A finding that must NOT be over-read

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

### 12. Consequence for the V4 deployment decision

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


---

## PART 3 — PERFORMANCE ATTRIBUTION — Phase 5 & Phase 7

*Consolidated from `docs/PERFORMANCE_ATTRIBUTION.md`.*

**Date:** 2026-08-29 · **Strategy:** `JP_ALPHA_V3_FROZEN` · **Window:** 2019-01-01 → 2026-08-29
**Source:** `research/short_book_analysis.py f_base` · seed 42 · 688 positions

---

### 0. Phase 5 first — the trade count was wrong

| | value |
|---|---|
| Exit **fragments** (previously reported as "trades") | 1,279 |
| **Positions** actually initiated and closed | **688** (358 long / 330 short) |
| Fragment-level win rate (previously reported) | 65.4% |
| **Position-level win rate** | **41.9%** |

Tiered exits emit up to three fragments per position, and the T1 fragment is a
win *by construction*. Every per-trade statistic published before this document
— including the 65.4% win rate — was fragment-level and upward-biased.
**The honest win rate is 41.9%.**

All figures below are position-level.

---

### 1. Headline: long vs short

95% bootstrap CI (10,000 resamples, seed 42) on mean position return.

| | n | P&L | win% | mean ret | 95% CI | median | PF | expectancy |
|---|---|---|---|---|---|---|---|---|
| **ALL** | 688 | −$24,322 | 41.9 | −0.06% | [−0.59, +0.47] | −1.21% | 0.93 | −$35 |
| **LONG** | 358 | **+$79,052** | 51.4 | **+1.53%** | **[+0.75, +2.33]** | +0.40% | 1.54 | +$221 |
| **SHORT** | 330 | **−$103,374** | 31.5 | **−1.79%** | **[−2.47, −1.08]** | −2.56% | 0.50 | −$313 |

Both CIs exclude zero. The long book is significantly positive; the short book
is significantly negative. The combined strategy is statistically
indistinguishable from zero before costs — and it is *negative* after them.

---

### 2. The mechanism — target asymmetry, not disasters

Terminal exit reason, by direction:

| exit reason | LONG n (share) | LONG avg | SHORT n (share) | SHORT avg |
|---|---|---|---|---|
| **T3_HIT** (full target) | **140 (39.1%)** | +9.42% | **54 (16.4%)** | +9.54% |
| STOP_LOSS | 122 (34.1%) | −5.69% | 140 (42.4%) | −6.63% |
| TIME_STOP | 67 (18.7%) | −1.32% | 98 (29.7%) | −2.14% |
| POST_T1_STOP | 26 (7.3%) | +0.16% | 35 (10.6%) | +0.82% |

**This is the whole story.** Conditional on reaching the full target, the two
books earn *the same* (+9.42% vs +9.54%). The short book simply gets there
**2.4× less often** (16.4% vs 39.1%), and stops out more (42.4% vs 34.1%) and
times out far more (29.7% vs 18.7%).

The payoff is symmetric. The *probability* is not.

Cause: equity drift. SPY compounded **+229%** over this window (~16%/yr). A
mean-reversion short needs −12% against that tailwind; the mirror-image long
needs +12% with it. The strategy's exit grid is direction-symmetric while the
underlying return process is not.

---

### 3. Hypothesis elimination

The brief asked which of six mechanisms explains the short book. Taking each:

#### ❌ (4) Excessive tail risk — REJECTED

| | p1 | p5 | median | p95 | worst | **CVaR₅** |
|---|---|---|---|---|---|---|
| LONG | −15.11% | −9.97% | **+0.40%** | +11.92% | −16.97% | **−12.78%** |
| SHORT | −13.71% | −10.50% | **−2.56%** | +10.12% | −18.80% | **−12.27%** |

The short book's left tail is **marginally better** than the long book's
(CVaR₅ −12.27% vs −12.78%). Worst-20 loss concentration is near-identical
(−$41,499 short vs −$43,550 long). Shorts do **not** blow up.

**They bleed through the middle of the distribution** — median −2.56% vs +0.40%.
This is death by a thousand cuts, not by tail events.

#### ❌ (5) Excessive transaction costs — REJECTED

The backtest is **gross of borrow**, and per [PART 1 §1.3](#13-corporate-action-leakage--fail) shorts are
additionally *subsidised* by never being charged the dividends they would owe.
Realistic costs make the short book **worse**, not better. Costs cannot explain
a loss that is already −$103k before charging any of them.

#### ❌ (3) Regime-dependent edge — REJECTED

SPY deviation from MA50 at entry:

| bucket | LONG mean [CI] | SHORT mean [CI] |
|---|---|---|
| SPY deep < MA50 | +2.11% [+0.34, +3.92] | −2.62% [−3.89, −1.28] |
| SPY < MA50 | +1.16% [−0.38, +2.70] | −1.43% [−2.70, −0.11] |
| SPY > MA50 | +1.21% [−0.32, +2.76] | −1.99% [−3.32, −0.54] |
| SPY far > MA50 | +1.64% [+0.36, +2.95] | −1.10% [−2.59, +0.40] |

**Shorts lose in all four regimes.** Longs are positive in all four. There is no
market state in this sample where the short book is rescued. The least-bad short
bucket (SPY far above MA50) is the only one whose CI touches zero — a weak hint
that shorting a stretched market is less punitive, nothing more.

#### ❌ (2) Conditional edge on signal strength — REJECTED, and inverted

| bucket | SHORT mean | | bucket | SHORT mean |
|---|---|---|---|---|
| RSI Q1 (least overbought) | −1.69% | | disl Q1 (shallow) | −2.04% |
| RSI Q2 | −1.84% | | disl Q2 | −1.36% |
| RSI Q3 | −0.89% | | disl Q3 | −1.73% |
| **RSI Q4 (most overbought)** | **−2.70%** | | **disl Q4 (deepest)** | **−2.00%** |

**The most extreme overbought signal is the worst-performing short.** The signal
is not merely uninformative — it is mildly *anti*-predictive. The most stretched
names are momentum leaders that keep running.

Corroborated by the symbol-level damage: worst short names are **MU −$12,313,
AMD −$11,101, LLY −$10,149, AAPL −$6,851** — precisely the secular winners of
2019–2026. The short book systematically shorted the strongest trends.

Sector-wise, shorts lose in **8 of 9** sectors (only Industrials is flat at
+0.03%); Energy −4.06%, ConDisc −3.06%, Health −3.03%.

#### ✅ (1) No edge + ✅ (6) Structural asymmetry — ACCEPTED

The short side has no edge in any regime, volatility bucket, sector, or signal
strength, and the failure mechanism is a direction-symmetric exit grid imposed
on a positively-drifting return process.

---

### 4. A conditioning artifact that must not be mistaken for a finding

Holding-period buckets look spectacular in both directions:

| | Q1 (~7d) | Q2 (~22d) | Q3 (~24d) | Q4 (~43d) |
|---|---|---|---|---|
| LONG | −1.25% | +0.21% | +2.98% | **+4.85%** (PF 12.13, win 75.6%) |
| SHORT | −3.90% | −3.14% | −0.96% | **+2.18%** (PF 2.92, win 58.0%) |

**This is not tradable and does not mean "hold longer."** Holding period is an
*outcome*, not an input: a position survives to Q4 precisely because it did not
hit its stop. The bucket is conditioned on the result. Quoting "hold Q4 has PF
12.13" as evidence of an edge would be circular reasoning.

What it *does* legitimately show: **64% of short positions resolve within ~22
days and lose −$128k doing so.** The short book dies fast.

---

### 5. What this does and does not license

**Supported:** dropping the short book is justified *by identified mechanism* —
no edge in any conditioning variable, an anti-predictive signal, and a
structural drift asymmetry — not merely because it lost money. This clears the
brief's bar against "removing shorts merely because they lose."

**Not supported — the honest limitation:** the sample is a single, historically
strong bull market (SPY +229%). "Shorts lose in a huge bull market" is close to
tautological. The four SPY-vs-MA50 buckets are *dips within a secular bull*, not
genuine bear regimes; 2022 is the only bear candidate in the sample. This
analysis therefore establishes that the short book fails **under positive
drift**, and predicts it would fail in any such regime. It cannot establish that
the short book would fail in a flat or falling market — that regime is not in
the data.

---

### 6. Cautions that also apply to the *long* book

The long book is significantly positive, but three findings temper it:

1. **It underperforms buy-and-hold.** Long-only +141% (Sharpe 0.83) vs SPY
   **+229% (Sharpe 0.93)** on the same window — worse on both raw and
   risk-adjusted terms. No beta or factor attribution has been run yet
   (Phase 8), so **no part of this is currently attributable to alpha.**
2. **Signal strength is non-monotonic for longs too.** disl Q1 +2.00%, Q2
   +1.29%, Q3 +0.60%, Q4 +2.24% — no ordering. If the dislocation signal were
   genuinely informative, deeper should be better. It isn't.
3. **Sector dependence.** LONG · Health −0.12%, LONG · Semis +0.37%,
   LONG · CommSvc +0.61% — the long edge is concentrated in Finance (+2.78%),
   Industrials (+3.01%) and Energy (+2.17%), on small samples (n=28–50).

---

### 7. Interaction with EXEC-2 (measured)

Full window / OOS window, with and without the anchor correction:

| config | window | Return | Sharpe | MaxDD | PF |
|---|---|---|---|---|---|
| long-only | full | +141.2% | 0.83 | −25.6% | 1.55 |
| long-only + fix | full | +104.8% | 0.68 | −26.4% | 1.41 |
| long-only + ATR stop | full | +132.9% | 0.82 | −21.8% | 1.50 |
| **long-only + ATR stop + fix** | full | **+152.3%** | **0.88** | −25.8% | 1.57 |
| long-only | OOS | +68.9% | 1.50 | −23.5% | 1.87 |
| long-only + fix | OOS | +66.0% | 1.47 | −22.0% | 1.82 |
| long-only + ATR stop | OOS | +75.0% | 1.59 | −18.2% | 1.98 |
| **long-only + ATR stop + fix** | OOS | **+81.5%** | **1.78** | −17.7% | 2.08 |

Notable: plain long-only *degrades* under the correction (its edge partly
depends on the defect), whereas the **ATR-stop variant improves** under it.
That is mechanically sensible — an ATR stop sets stop distance from volatility
rather than as a fixed percentage of a mis-stated anchor, so it is structurally
less sensitive to anchor error. This is a genuine robustness point in the ATR
stop's favour.

**It is not, however, sufficient to justify deployment.** See
[PART 2 §4, item 2 — selection bias](#4-statistical-honesty--what-these-phases-cannot-do): this variant was chosen because
it won the out-of-sample window, which consumed that window. Even at +152.3%
(Sharpe 0.88) it still underperforms SPY's +229% (Sharpe 0.93) on the full
sample.


---

## PART 4 — FACTOR AND BETA ATTRIBUTION — Phase 8

*Consolidated from `docs/FACTOR_ATTRIBUTION.md`.*

**Date:** 2026-08-29 · **Window:** 2019-04-01 → 2026-06-30 (1,822 daily obs, 7.2 yrs)
**Factors:** Ken French research library — MKT, SMB, HML, RMW, CMA, MOM (real FF factors, not ETF proxies)
**Estimator:** OLS with **Newey-West** HAC standard errors, 5 lags
**Reproduce:** `python3 research/factor_attribution.py f_base a_lo_base s2_full_fix s2_oos_fix`

> Newey-West rather than plain OLS because the strategy holds overlapping
> multi-day positions, which serially correlates daily returns and would
> otherwise overstate every t-stat.

---

### The headline

**No configuration of this strategy exhibits statistically significant alpha.**

| Config | Return | Sharpe | **Beta** | **Alpha (CAPM)** | **t** | 95% CI on alpha | R² |
|---|---|---|---|---|---|---|---|
| `f_base` (live, frozen) | −29.5% | −0.38 | 0.318 | **−10.77%/yr** | **−1.91*** | [−21.85%, +0.30%] | 0.152 |
| `a_lo_base` (long-only) | +122.8% | 0.60 | 0.558 | +1.67%/yr | +0.41 | [−6.29%, +9.63%] | 0.503 |
| `s2_full_fix` (**V4 candidate**) | +130.3% | 0.63 | 0.546 | **+2.26%/yr** | **+0.57** | **[−5.44%, +9.96%]** | 0.501 |
| `s2_oos_fix` (V4, OOS only, 2.5 yr) | +65.7% | 1.25 | 0.553 | +7.62%/yr | +1.21 | [−4.75%, +20.00%] | 0.457 |

Significance: `*` t>1.645 · `**` t>1.96 · `***` t>2.576

#### What this says

1. **The live strategy has significantly *negative* alpha.** Under the 3-, 4-
   and 6-factor models the baseline alpha is −11.5% / −11.4% / −11.4% per year
   with **t = −2.02, −2.00, −1.99** — significant at the 5% level. It runs
   132% gross exposure at only **9.5% net** — i.e. it is being operated as a
   quasi-market-neutral book — and in that capacity it is *actively destroying
   value*, not merely failing to add it.

2. **The long book's return is beta, not skill.** `a_lo_base` earns +122.8%
   with **beta 0.558** and **R² 0.503**. Alpha is +1.67%/yr with **t = 0.41** —
   indistinguishable from zero. SPY returned **+229%** over this window; a
   passive 0.55-beta equity exposure reproduces essentially the entire result.

3. **The V4 candidate is no different.** `s2_full_fix` (long-only + ATR stop +
   EXEC-2 fix) posts alpha **+2.26%/yr, t = 0.57**, CI **[−5.44%, +9.96%]**.
   Beta 0.546 on 68.3% average net exposure. The strategy is a partially
   invested long equity portfolio.

4. **The out-of-sample Sharpe of 1.78 is beta too.** On the 2024–2026 window
   alpha rises to +7.62%/yr but **t = 1.21** and the CI spans
   **[−4.75%, +20.00%]**. Beta is 0.553 — the same as the full sample. The
   headline OOS performance is a 0.55-beta position in a market that rose
   sharply, not evidence of edge.

---

### Statistical power — what could we even have detected?

Residual volatility is ~11.1%/yr on 7.2 years, so the standard error on
annualised alpha is ≈ **3.9%/yr**. Detecting alpha at the 95% level therefore
requires roughly **|alpha| > 7.9%/yr**.

This cuts both ways and must be stated honestly:

- We can **rule out** a large edge. Alpha above ~10%/yr is inconsistent with the data.
- We **cannot distinguish** +2.26%/yr from zero. A small genuine edge of 1–3%/yr
  would be invisible at this sample size — and would in any case be consumed by
  the realistic costs not yet modelled (Phase 6).

"Not significant" here means *not demonstrated*, not *proven absent*.

---

### Factor exposures — where the returns actually come from

Consistent, significant loadings across every long configuration:

| Factor | Loading | t | Reading |
|---|---|---|---|
| **MKT** | +0.55 to +0.59 | +11 to +12 *** | Dominant. This *is* the strategy. |
| **HML** (value) | +0.11 to +0.16 | +2.9 to +3.8 *** | Genuine value tilt |
| **SMB** (size) | −0.08 to −0.15 | −1.9 to −2.5 ** | Large-cap tilt |
| **CMA** | +0.14 | +2.5 ** | Conservative-investment tilt |
| RMW, MOM | ≈ 0 | \|t\| < 0.7 | No exposure |

The **HML loading is the most interesting genuine finding.** Buying oversold
names mechanically tilts toward value — the strategy is, in effect, an
expensive way to run a value tilt. Note that value *underperformed* over
2019–2026, so this tilt was a **drag**, not a source of return.

The negative SMB is expected and uninformative: the universe is 42 mega-caps.

**Residual (idiosyncratic) volatility is ~11.1%/yr** for statistically zero
alpha. That is a large amount of stock-specific risk being taken with no
demonstrated compensation.

---

### The comparison that matters

| | Return | Sharpe | Beta | Alpha t-stat |
|---|---|---|---|---|
| **SPY buy & hold** | **+229%** | **0.93** | 1.00 | — |
| V4 candidate | +130.3% | 0.63 | 0.55 | +0.57 (ns) |
| long-only | +122.8% | 0.60 | 0.56 | +0.41 (ns) |
| live strategy | −29.5% | −0.38 | 0.32 | −1.91 (negative) |

A 42-name universe, daily data pulls, ATR sizing, tiered exits, regime filters,
a server, a cron job and a dashboard — delivering **0.55 units of market beta
and no measurable alpha**, at a lower Sharpe than the index it is built from.

---

### Consequences for the deployment decision

[PART 3 §7](#7-interaction-with-exec-2-measured) flagged that `longonly_stopatr2` was
selected *because it won the out-of-sample window*, which consumed that window.
Phase 8 makes the point moot:

**There is no alpha to protect.** Whether or not the parameter choice is
overfit, the variant it selects has no statistically distinguishable edge over
holding 55% SPY. Deploying it would not be trading an edge — it would be paying
execution costs and idiosyncratic risk for beta obtainable for 3bp in an index
fund.

Phases 10–12 (nested walk-forward, bootstrap, parameter robustness) remain
worth running, but their job has changed: they are no longer validating a
candidate edge, they are quantifying how confident we can be that the edge is
absent.

---

### Caveats against over-reading this

1. **Gross of costs.** Adding realistic costs moves alpha *down*, not up. This
   is the strategy's best case.
2. **Survivorship-biased universe** ([PART 1 §1.4](#14-survivorship-bias--fail)) — also flatters.
3. **One regime.** 2019–2026 is a single strong bull market. A mean-reversion
   long book with 0.55 beta is the wrong instrument to evaluate in the *only*
   environment where it is guaranteed to look acceptable.
4. **Daily-return attribution can understate** a strategy trading on multi-day
   horizons. Newey-West mitigates the inference problem but not the fact that
   daily factor regressions are a coarse lens on a ~22-day holding period.
   A position-level factor attribution would be a useful cross-check.


---

## PART 5 — ARCHITECTURE AUDIT — JP Alpha Strategy

*Consolidated from `docs/ARCHITECTURE_AUDIT.md`.*

**Audit date:** 2026-08-29
**Commit audited:** `c41b161`
**Auditor scope:** Phase 0 (repository forensics) of the institutional upgrade program.
**Status:** Phase 0 complete. Phases 1–20 outstanding.

> Nothing in this document changed `jp_agent.py`. The live agent is untouched and
> still running the frozen strategy. All findings below are evidence-backed —
> every claim cites either a line number or a measurement that can be re-run.

---

### 1. File inventory

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

### 2. Write map — who mutates what

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

### 3. Broker API surface

All calls route through three helpers (`jp_agent.py:211–224`):

- `alpaca_get` → `/v2/account`, `/v2/positions`, `/v2/clock`
- `alpaca_post` → `/v2/orders` (**the only order-creation path**, line 250)
- `alpaca_delete` → `/v2/orders` (**blanket cancel-all**, line 253)

Data feed: `data.alpaca.markets/v2/stocks/*` (quotes for cost model, SPY bars for benchmark).

---

### 4. FINDINGS

Severity: **C**ritical / **H**igh / **M**edium.

#### EXEC-1 (C) — State is written on *submission*, not on *fill*

`execute_orders` (line 840) writes `positions[sym] = {...}` immediately after
`place_market_order` returns. For a market DAY order submitted after the close,
the returned `status` is `accepted` / `pending_new` — **never** `filled`. The
strategy therefore books a position that does not yet exist.

The response's `status` is logged but **never branched on**. A rejection
discovered asynchronously leaves a phantom position in `state.json`.

*Mitigating factor:* `reconcile_state` on the next run deletes positions absent
from the broker, so a phantom self-heals within one session. The un-healed
consequence is EXEC-2.

#### EXEC-2 (C) — `entry_price` is never reconciled to the actual fill

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

#### EXEC-3 (H) — No client order IDs

`place_market_order` (line 235) submits `symbol / qty / side / type /
time_in_force`. No `client_order_id`. Consequences: orders cannot be attributed
to a strategy version, replays cannot be deduplicated, and the strategy cannot
distinguish its own orders from any other activity on the account.

#### EXEC-4 (H) — Blanket `DELETE /v2/orders` on every run

`cancel_all_pending_orders` (line 252) is called unconditionally in `main`
(line 1084) and cancels **every open order on the account**, not just this
strategy's. Harmless on a single-strategy paper account; unacceptable on any
shared or funded account. Cannot be scoped correctly until EXEC-3 is fixed.

#### EXEC-5 (C) — Divergence is logged, then trading continues

`reconcile_state` silently deletes divergent positions and adjusts share counts,
returns, and `main` proceeds directly into `process_entries`. There is no halt,
no divergence record, no alert gate. This is precisely the behaviour the brief
prohibits: *"Do not merely alert while continuing to trade."*

#### EXEC-6 (C) — Orphan broker positions are invisible to every risk limit

`reconcile_state` iterates only over `state["positions"]`. A position the broker
holds but state does not know about is never examined. Worse, `process_entries`
(line 743) derives **all** portfolio limits — `n_longs`, `n_shorts`, sector
counts, and the duplicate-symbol guard — from `state["positions"]` alone.

An orphan therefore:
- does not count toward `MAX_LONGS` / `MAX_SHORTS` / `MAX_SIMULTANEOUS`
- does not count toward `MAX_PER_SECTOR`
- **does not prevent a second position being opened in the same symbol**

#### EXEC-7 (M) — No partial-fill handling anywhere

`shares_total` and `shares_remaining` are set to the *requested* qty. A partial
fill is only detected on the next run, and only as a `shares_remaining`
correction with no record that it was a partial.

#### RISK-1 (H) — Sizing risk and stop risk are different numbers

`calc_shares` (line 545) sizes on `1% risk / (1.5 × ATR)`. The stop is a fixed
−8%. These agree only when `1.5 × ATR ≈ 8% × price`. For a low-vol name the
true risk is far below 1%; for a high-vol name, far above. Intended risk ≠ actual
risk on essentially every position. (Phase 2.)

#### REPRO-1 (H) — Backtests are not reproducible

No data cache existed: every run re-downloaded from yfinance with
`auto_adjust=True`. Adjusted history is **revised retroactively** on every
dividend and split, so the same commit produces different results over time.
No manifest records data range, universe version, or cost model. (Phase 19.)

*Partially remediated during this audit — see §5.*

#### DATA-1 (H) — Universe is survivorship-biased

The 42-symbol `WATCHLIST` (`jp_agent.py:117–138`) is a fixed present-day list
applied to 2019. Every member survived to 2026 and was selected with that
knowledge. No point-in-time membership. (Phase 4.)

---

### 5. Changes made during Phase 0

All changes are to `backtest.py` only. `jp_agent.py` is **unmodified**.

1. **Instrumentation (inert).** Added `entry_ref` and `anchor_err_pct` to the
   closed-trade CSV. Regression-verified: baseline returns −23.83%, 1,279 trades,
   PF 0.94, Sharpe −0.14 — **identical** to the pre-instrumentation run.
2. **Price cache.** `data/prices_<sha256>.pkl`, keyed on universe + window + data
   semantics. `BT_NOCACHE=1` bypasses. Addresses REPRO-1 partially.
3. **`ANCHOR_FILL=1` toggle (default OFF).** Gated correction for EXEC-2.
   Default-off keeps the baseline bit-identical to `JP_ALPHA_V3_FROZEN`.

#### Measured effect of the EXEC-2 correction (2019-01-01 → 2026-08-29)

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

### 6. Phase 1 remediation — IMPLEMENTED

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

#### 6.1 A defect found *during* Phase 1 — the exit-path orphan

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

#### 6.2 Idempotency

Deterministic coids make submission replay-safe. On a duplicate the broker
returns 409/422; `place_market_order` catches it and retrieves the existing
order via `/v2/orders:by_client_order_id` rather than raising or re-submitting.
Test: two submit attempts → **one** broker order, one position.

#### 6.3 Halt semantics

The gate halts **entries only**. Exits are never halted — a reconciliation
failure must not trap the book in its existing risk. Share-count drift alone
(the benign partial-fill case) does not halt; `missing_at_broker`, `orphans` and
`direction_conflict` do.

#### 6.4 What Phase 1 does *not* fix

RISK-1 (Phase 2), REPRO-1 (Phase 19) and DATA-1 (Phase 4) are untouched. Phase 1
also cannot be validated against a live broker from the backtest: the tests
exercise a mock, so they prove the *state machine* is correct, not that Alpaca's
error codes are exactly as assumed.

### 7. Versioning convention (introduced)

- `JP_ALPHA_V3_FROZEN` — current live logic. Immutable reference. Baseline regression must reproduce −23.83% / 1279 trades / PF 0.94 on 2019-01-01 → 2026-08-29.
- Any change to alpha logic ⇒ new version id (`JP_ALPHA_V4_*`) with a documented diff and a fresh full validation run.
- Correctness fixes are gated behind an explicit env toggle and reported as a separate line in every results table, never folded into the baseline.


---

## PART 6 — DEPLOYMENT DECISION — JP_ALPHA_V4_LONGONLY_STOPATR2

*Consolidated from `docs/DEPLOYMENT_DECISION.md`.*

**Date:** 2026-08-28 · **Decided by:** JP (commissioner) · **Implemented by:** research/engineering
**Status:** DEPLOYED · **Recommendation at time of deployment: DO NOT DEPLOY**

This document exists so that the decision cannot later be misremembered. The
evidence below was produced *before* deployment and was known at the time.

---

### 1. What was deployed

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
([PART 3 §7](#7-interaction-with-exec-2-measured)).

#### Implementation

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

### 2. The evidence against deployment

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
  window, which consumed that window ([PART 3 §7](#7-interaction-with-exec-2-measured)). Phase 10
  is the remedy, and it does not vindicate the choice.

#### What the evidence does *not* say

It does not say the variant is broken or that it will lose money. Its point
estimates are positive and its risk profile is reasonable for a long equity
book. The finding is narrower and more specific: **it is not distinguishable
from 0.55–0.60 beta, and its estimated edge is the same size as its costs.**

Statistical power is the binding constraint. Residual vol ~11.1%/yr over 7.2
years gives SE ≈ 3.9%/yr on annualised alpha, so only |alpha| > 7.9%/yr was
ever detectable. A genuine 1–3%/yr edge would be invisible here. **"Not
significant" means not demonstrated, not proven absent.**

---

### 3. Why it was deployed anyway

The commissioner instructed deployment after being shown the above. That is a
legitimate exercise of authority over a paper-trading account: the downside is
bounded, the configuration is strictly less risky than the V3 book it replaces
(long-only, no borrow, no unlimited-loss exposure), and running it forward
generates genuinely out-of-sample data that no amount of further backtesting
can produce.

**The honest framing is that this is not a bet on demonstrated edge. It is the
start of a live out-of-sample test**, and it should be judged as such.

---

### 4. What must be true for this to have been the right call

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

### 5. Kill criteria

Retire V4 and revert to `STRATEGY_VERSION=JP_ALPHA_V3_FROZEN` (or to cash) if
any of the following occurs:

- realised slippage sustained above **20bps/leg**
- drawdown beyond **−50%** (outside the bootstrap 95% band)
- cumulative underperformance vs 0.6× SPY exceeding **−15%** over any rolling
  12-month period
- any execution or reconciliation defect from [PART 5 §4](#4-findings)
  (EXEC-1, EXEC-3..7) causing a position the state file does not know about

---

### 6. Engineering risk — closed by Phase 1

> **Updated after Phase 1 landed.** This section previously recorded EXEC-1 and
> EXEC-3…7 as carried into production unfixed. They are now remediated in
> `jp_agent.py` and covered by `tests/test_execution.py` (22 assertions).
> See [PART 5 §6](#6-phase-1-remediation--implemented) for implementation detail.

| id | issue | status |
|---|---|---|
| EXEC-1 | state written on submission, not on fill | ✅ fill-driven `execute_orders` |
| EXEC-3 | no `client_order_id` — no idempotency; a retry can double-fill | ✅ deterministic `JPV4-…` coids |
| EXEC-4 | blanket `DELETE /v2/orders` on every run | ✅ scoped by coid prefix |
| EXEC-5 | divergence logged, then trading continues | ✅ halt gate on new entries |
| EXEC-6 | broker-only (orphan) positions invisible to risk limits | ✅ counted and symbol blocked |
| EXEC-7 | partial fills not modelled | ✅ accounted at filled qty |

**The fail-safe anchor fallback described in the original version of this
section no longer applies.** V4 change 3 previously depended on
`get_fill_price` succeeding and silently reverted to V3 anchoring when it did
not. Under Phase 1 an entry is written only once `confirm_order` reports a
terminal fill, so `fill_price` is always present for any position that exists.

#### Residual risk Phase 1 does *not* close

1. **The tests exercise a mock, not Alpaca.** They prove the state machine is
   internally correct; they *assume* Alpaca returns 409/422 on a duplicate
   `client_order_id`. The first live duplicate is the real test.
2. **`STRATEGY_VERSION` is an env var.** A mis-set environment silently reverts
   the book to V3 semantics, shorts included.
3. **The halt gate is one-directional by design.** Exits are never halted. If
   reconciliation is wrong in a way that makes an exit inappropriate, nothing
   stops it.
4. **RISK-1, REPRO-1 and DATA-1 remain open** — Phases 2, 19 and 4.

Legacy short positions opened under V3 are **not** force-liquidated. They wind
down through the unchanged V3 short-exit path. At deployment the book held one:
**UNH −23 @ 433.14**.

---

### 7. Reverting

```bash
# immediate revert, no code change
STRATEGY_VERSION=JP_ALPHA_V3_FROZEN /root/jp_strategy/venv/bin/python jp_agent.py
# or persistently, in the cron entry / .env
```


---

## PART 7 — DEPLOYMENT AMENDMENT: V5 (1.5×ATR stop)

*Added after the twenty-phase programme closed. Supersedes the deployment
decision in PART 6, which selected V4 with a 2.0×ATR stop.*

### 7.1 What changed

`JP_ALPHA_V5_LONGONLY_STOPATR15` is deployed. It is V4 with one change: the
stop moves from 2.0×ATR to 1.5×ATR. V3 and V4 remain selectable and unchanged.

### 7.2 Why — the argument is correctness, not performance

Realised risk per position is `shares × stop_distance / PV`. With ATR sizing,
`shares = RISK_PER_TRADE_PCT × PV / (ATR_MULTIPLIER × ATR)`, so a `k×ATR` stop
risks `RISK_PER_TRADE_PCT × k / ATR_MULTIPLIER` of equity — **the volatility
term cancels exactly**. With `ATR_MULTIPLIER = 1.5`:

| stop | realised risk | vs. the 1% the system claims |
|---|---:|---|
| V3 fixed −8% | median 1.95%, p95/p5 spread 3.53× | uncontrolled |
| V4 2.0×ATR | 1.333% | 33% more than advertised |
| **V5 1.5×ATR** | **1.000%** | **exact** |

1.5 is the only multiple under which the risk model is true. That argument was
made in Phase 2, **before the Phase 12 sweep existed**, and it predicted the
sweep result. Two later, independent lines agree:

| Evidence | V4 (2.0×ATR) | V5 (1.5×ATR) |
|---|---:|---:|
| Phase 10 nested walk-forward | dominated | dominates on all three metrics |
| Phase 12 sweep — Sharpe | 0.88 | **0.98** |
| Phase 12 sweep — MaxDD | −25.8% | **−19.4%** |
| Full backtest 2019-01-01→2026-08-29 — total return | +152.74% | **+159.57%** |
| — CAGR | +13.32% | **+13.73%** |
| — Calmar | 0.52 | **0.71** |
| — closed trades | 842 | 871 |

### 7.3 What this does NOT change

**The verdict of PART 6 stands.** V5 does not create an edge; it makes the
system risk what it says it risks. V5 still returns 13.73% CAGR against SPY's
17.60% on the same window, and the alpha measured against the strategy's own
universe remains +1.51%/yr (t = +0.38) — not statistically distinguishable from
zero. A better Sharpe obtained by taking less risk is not evidence of skill.

### 7.4 Two defects found while deploying it

1. **Live stops were rewritten retroactively.** `process_long_exits` computed
   the stop from the *current* `STOP_ATR_MULT` at exit-check time, so changing
   the constant moved the stop on positions already open — positions sized
   under a different risk contract. The multiple is now pinned on each position
   at entry. Checked before deploying: no open position was breached by the
   tighter stop (QQQ 716.43 vs 654.01; WMT 103.09 vs 99.20; UNH is a legacy
   short and unaffected), so nothing was force-liquidated.
2. **The version gate failed open.** `_V4 = STRATEGY_VERSION == "..."` meant any
   unrecognised value — a typo, a stale deploy script — fell through to V3
   behaviour silently, **re-enabling the short book and the fixed −8% stop**.
   An unknown version now refuses to start.

### 7.5 Reverting

```bash
# revert to V4, or to the frozen V3 control, with no code change
STRATEGY_VERSION=JP_ALPHA_V4_LONGONLY_STOPATR2 venv/bin/python jp_agent.py
STRATEGY_VERSION=JP_ALPHA_V3_FROZEN            venv/bin/python jp_agent.py
```
