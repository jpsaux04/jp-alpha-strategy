# PERFORMANCE ATTRIBUTION — Phase 5 & Phase 7

**Date:** 2026-08-29 · **Strategy:** `JP_ALPHA_V3_FROZEN` · **Window:** 2019-01-01 → 2026-08-29
**Source:** `research/short_book_analysis.py f_base` · seed 42 · 688 positions

---

## 0. Phase 5 first — the trade count was wrong

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

## 1. Headline: long vs short

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

## 2. The mechanism — target asymmetry, not disasters

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

## 3. Hypothesis elimination

The brief asked which of six mechanisms explains the short book. Taking each:

### ❌ (4) Excessive tail risk — REJECTED

| | p1 | p5 | median | p95 | worst | **CVaR₅** |
|---|---|---|---|---|---|---|
| LONG | −15.11% | −9.97% | **+0.40%** | +11.92% | −16.97% | **−12.78%** |
| SHORT | −13.71% | −10.50% | **−2.56%** | +10.12% | −18.80% | **−12.27%** |

The short book's left tail is **marginally better** than the long book's
(CVaR₅ −12.27% vs −12.78%). Worst-20 loss concentration is near-identical
(−$41,499 short vs −$43,550 long). Shorts do **not** blow up.

**They bleed through the middle of the distribution** — median −2.56% vs +0.40%.
This is death by a thousand cuts, not by tail events.

### ❌ (5) Excessive transaction costs — REJECTED

The backtest is **gross of borrow**, and per `RESEARCH_AUDIT.md §1.3` shorts are
additionally *subsidised* by never being charged the dividends they would owe.
Realistic costs make the short book **worse**, not better. Costs cannot explain
a loss that is already −$103k before charging any of them.

### ❌ (3) Regime-dependent edge — REJECTED

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

### ❌ (2) Conditional edge on signal strength — REJECTED, and inverted

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

### ✅ (1) No edge + ✅ (6) Structural asymmetry — ACCEPTED

The short side has no edge in any regime, volatility bucket, sector, or signal
strength, and the failure mechanism is a direction-symmetric exit grid imposed
on a positively-drifting return process.

---

## 4. A conditioning artifact that must not be mistaken for a finding

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

## 5. What this does and does not license

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

## 6. Cautions that also apply to the *long* book

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

## 7. Interaction with EXEC-2 (measured)

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
`VALIDATION_METHODOLOGY.md` §on selection bias: this variant was chosen because
it won the out-of-sample window, which consumed that window. Even at +152.3%
(Sharpe 0.88) it still underperforms SPY's +229% (Sharpe 0.93) on the full
sample.
