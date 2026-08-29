# FACTOR AND BETA ATTRIBUTION — Phase 8

**Date:** 2026-08-29 · **Window:** 2019-04-01 → 2026-06-30 (1,822 daily obs, 7.2 yrs)
**Factors:** Ken French research library — MKT, SMB, HML, RMW, CMA, MOM (real FF factors, not ETF proxies)
**Estimator:** OLS with **Newey-West** HAC standard errors, 5 lags
**Reproduce:** `python3 research/factor_attribution.py f_base a_lo_base s2_full_fix s2_oos_fix`

> Newey-West rather than plain OLS because the strategy holds overlapping
> multi-day positions, which serially correlates daily returns and would
> otherwise overstate every t-stat.

---

## The headline

**No configuration of this strategy exhibits statistically significant alpha.**

| Config | Return | Sharpe | **Beta** | **Alpha (CAPM)** | **t** | 95% CI on alpha | R² |
|---|---|---|---|---|---|---|---|
| `f_base` (live, frozen) | −29.5% | −0.38 | 0.318 | **−10.77%/yr** | **−1.91*** | [−21.85%, +0.30%] | 0.152 |
| `a_lo_base` (long-only) | +122.8% | 0.60 | 0.558 | +1.67%/yr | +0.41 | [−6.29%, +9.63%] | 0.503 |
| `s2_full_fix` (**V4 candidate**) | +130.3% | 0.63 | 0.546 | **+2.26%/yr** | **+0.57** | **[−5.44%, +9.96%]** | 0.501 |
| `s2_oos_fix` (V4, OOS only, 2.5 yr) | +65.7% | 1.25 | 0.553 | +7.62%/yr | +1.21 | [−4.75%, +20.00%] | 0.457 |

Significance: `*` t>1.645 · `**` t>1.96 · `***` t>2.576

### What this says

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

## Statistical power — what could we even have detected?

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

## Factor exposures — where the returns actually come from

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

## The comparison that matters

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

## Consequences for the deployment decision

`docs/PERFORMANCE_ATTRIBUTION.md` §7 flagged that `longonly_stopatr2` was
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

## Caveats against over-reading this

1. **Gross of costs.** Adding realistic costs moves alpha *down*, not up. This
   is the strategy's best case.
2. **Survivorship-biased universe** (`RESEARCH_AUDIT.md §1.4`) — also flatters.
3. **One regime.** 2019–2026 is a single strong bull market. A mean-reversion
   long book with 0.55 beta is the wrong instrument to evaluate in the *only*
   environment where it is guaranteed to look acceptable.
4. **Daily-return attribution can understate** a strategy trading on multi-day
   horizons. Newey-West mitigates the inference problem but not the fact that
   daily factor regressions are a coarse lens on a ~22-day holding period.
   A position-level factor attribution would be a useful cross-check.
