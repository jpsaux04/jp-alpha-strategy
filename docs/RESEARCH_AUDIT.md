# RESEARCH AUDIT — JP Alpha Strategy

**Audit date:** 2026-08-29 · **Commit:** `c41b161` · **Phase 0 of 20.**

Working assumption throughout: *the strategy is overestimating its edge until
proven otherwise.*

---

## 1. Look-ahead audit (Rule #2)

### 1.1 Signal causality — **PASS**

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

### 1.2 Execution timing — **PASS**

Decision on close of *d* → order queued → filled at **open of *d+1***
(`backtest.py:267–277`). Exits are filled before entries within the same open.
No same-bar fill uses information derived from that bar's close. This is a
genuine strength.

### 1.3 Corporate-action leakage — **FAIL**

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

### 1.4 Survivorship bias — **FAIL**

`WATCHLIST` is 42 present-day large caps applied unchanged to 2019. Every
constituent survived and was chosen knowing that. There is no point-in-time
membership. Magnitude is unmeasured; it cannot be assumed small. The repository
must not describe any result as survivorship-bias-free. (Phase 4.)

### 1.5 Parameter-selection leakage — **FAIL (self-inflicted, prior work)**

The exit-design ablation tested 9 variants on the same 7.6-year sample and
reported the winner. The subsequent walk-forward exposed exactly this: the
in-sample champion (`longonly_noscale_trail3`, +82.5% IS) fell to 4th
out-of-sample, while the *worst* in-sample long-only variant
(`longonly_stopatr2`, +31.2%, 6th of 6) became the **best** out-of-sample
(+75.0%, Sharpe 1.59). The ranking did not merely degrade — it inverted.

Any claim resting on picking a variant by in-sample performance is void.

### 1.6 Benchmark leakage — **PASS with a caveat**

SPY is fetched over the same window and is not in the tradable universe. But
SPY's own adjusted series carries the same §1.3 back-adjustment issue, so
strategy-vs-benchmark comparisons are internally consistent rather than
absolutely correct.

---

## 2. Correctness of the measured edge

### 2.1 The headline result was under-stated against the benchmark

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

### 2.2 Part of the long-only edge is an implementation artifact

See ARCHITECTURE_AUDIT §5 / EXEC-2. Exit levels are anchored to the prior close
rather than the fill. Measured anchor error: σ ≈ 2%, >1% on 35% of trades,
against a +4%/−8% grid.

Correcting it moves long-only from **+141.2% (Sharpe 0.83) → +104.8% (Sharpe
0.68)**. Roughly a quarter of the long book's reported return depends on a
defect that systematically tightens targets and loosens stops for dip-buys that
bounce overnight.

This is a strong prior that the long-only result is **partly grid-noise
interaction, not signal**. It must be re-tested under Phases 10–12 before any
edge claim survives.

### 2.3 Trade counts are inflated

1,279 "trades" are **exit fragments**, not positions. Tiered exits generate up
to 3 fragments per position; the true figure is ~688 positions. Every
per-trade statistic quoted so far (win rate 65.4%, expectancy −$19.02) is
computed on fragments and is therefore **not** a position-level statistic.
Fragment win rate is upward-biased because T1 fragments are wins by
construction. (Phase 5.)

### 2.4 Risk model is internally inconsistent

Sizing uses `1% / (1.5 × ATR)`; the stop is a flat −8%. Intended risk equals
realised stop risk only when `1.5 × ATR ≈ 0.08 × price`. Otherwise per-position
risk is silently mis-scaled — low-vol names under-risked, high-vol names
over-risked. Combined with EXEC-2, the *actual* risk on any given position is
knowable only after the fact. (Phase 2.)

### 2.5 Cost model coverage is incomplete

`costs.py` models implementation shortfall vs NBBO mid, SEC §31, FINRA TAF, and
a flat short-borrow assumption. Not modelled in the backtest: market impact,
financing, dividends owed on shorts (§1.3), or borrow-rate variation. The
single flat borrow rate is inadequate for a book whose short side is the
central research question. (Phase 6.)

---

## 3. What Phase 0 did *not* find

Reporting these explicitly so absence of evidence is not read as evidence:

- No look-ahead in signal construction (§1.1) — verified, not assumed.
- No same-bar execution leakage (§1.2).
- No evidence of results being hand-edited; the baseline reproduced bit-identically after instrumentation.
- Only one module can trade; every analytics/dashboard path is GET-only.
- No secrets in git history (audited previously; all key references are `os.environ` lookups).

---

## 4. Open research questions, in priority order

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

## 5. Standing rules adopted

- Baseline regression (−23.83% / 1279 / PF 0.94 / Sharpe −0.14) must be
  reproduced by every future commit unless a documented fix intentionally
  changes it.
- Correctness fixes ship gated OFF and are reported as separate rows.
- No result is quoted without sample size and benchmark on the same line.
- Fragment-level and position-level statistics are never mixed.
