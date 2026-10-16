#!/usr/bin/env python3
"""Follow-ups the first table cannot answer.

A. Are the performance differences distinguishable from noise? A raw mean gap
   invites a conclusion the sample may not support, in either direction.

B. Could the portfolio have TAKEN the extra trades? "Drop VOL and get 9x the
   trades" is only meaningful if there were slots free. With MAX_SIMULTANEOUS
   positions and ~21-day holds, the cap may already be the binding constraint,
   in which case VOL is not costing profit -- it is just one arbitrary way of
   choosing among more candidates than the book can hold. Those are very
   different findings.
"""
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, "/root/jp_strategy")
from dotenv import load_dotenv
load_dotenv("/root/jp_strategy/.env")
import jp_agent as A

R = pd.read_csv("/tmp/cond_recs.csv", parse_dates=["date"])
KEYS = ("RSI", "MA20", "VOL", "CLOSE", "REGIME")
sig = R[R.blocker.isna()]

print("=" * 78)
print("A. IS THE DIFFERENCE DISTINGUISHABLE FROM NOISE?")
print("   Welch two-sample t-test on per-trade return vs ACTUAL SIGNALS")
print("=" * 78)
m0, s0, n0 = sig.ret.mean(), sig.ret.std(ddof=1), len(sig)
print(f"{'set':<14}{'n':>7}{'mean':>9}{'1 s.e.':>9}{'diff':>9}{'t':>7}  verdict")
print(f"{'ACTUAL':<14}{n0:>7,}{m0 * 100:>8.2f}%{s0 / np.sqrt(n0) * 100:>8.2f}%")
for k in KEYS:
    d = R[R.blocker == k]
    if len(d) < 3:
        print(f"{'drop ' + k:<14}{len(d):>7} too few")
        continue
    m1, s1, n1 = d.ret.mean(), d.ret.std(ddof=1), len(d)
    se = np.sqrt(s0**2 / n0 + s1**2 / n1)
    t = (m1 - m0) / se
    v = ("WORSE" if t < -1.96 else "BETTER" if t > 1.96
         else "indistinguishable")
    print(f"{'drop ' + k:<14}{n1:>7,}{m1 * 100:>8.2f}%"
          f"{s1 / np.sqrt(n1) * 100:>8.2f}%{(m1 - m0) * 100:>8.2f}%{t:>7.2f}  {v}")

print("\n" + "=" * 78)
print("B. COULD THE BOOK HAVE HELD THEM?")
print(f"   caps: {A.MAX_SIMULTANEOUS} open, {A.MAX_LONGS} long, "
      f"{A.MAX_PER_SECTOR}/sector; hold up to {A.TIME_STOP_DAYS}d")
print("=" * 78)
#  Concurrency = how many of these trades are open on an average day if every
#  one were taken. Compare with the cap.
days = pd.bdate_range(R.date.min(), R.date.max())


def concurrency(d):
    if not len(d):
        return 0.0, 0
    #  Each trade occupies its entry date + up to HOLD business days.
    cnt = pd.Series(0, index=days, dtype=int)
    for dt in d.date:
        lo = dt
        hi = dt + pd.tseries.offsets.BDay(A.TIME_STOP_DAYS)
        cnt.loc[lo:hi] += 1
    return cnt.mean(), int(cnt.max())


a_mean, a_max = concurrency(sig)
print(f"{'set':<24}{'avg open':>10}{'peak':>7}{'vs cap':>9}")
print(f"{'ACTUAL SIGNALS':<24}{a_mean:>10.1f}{a_max:>7}"
      f"{a_mean / A.MAX_SIMULTANEOUS:>8.0%}")
for k in ("VOL", "CLOSE"):
    d = pd.concat([sig, R[R.blocker == k]])
    m, mx = concurrency(d)
    print(f"{'+ drop ' + k:<24}{m:>10.1f}{mx:>7}{m / A.MAX_SIMULTANEOUS:>8.0%}")
d = pd.concat([sig, R[R.blocker.isin(["VOL", "CLOSE"])]])
m, mx = concurrency(d)
print(f"{'+ drop both':<24}{m:>10.1f}{mx:>7}{m / A.MAX_SIMULTANEOUS:>8.0%}")

print("\n" + "=" * 78)
print("C. SIGNAL SCARCITY OVER TIME (actual signals per year)")
print("=" * 78)
yr = sig.groupby(sig.date.dt.year).size()
for y, n in yr.items():
    print(f"  {y}  {n:>4}  {'#' * int(n / 3)}")
print(f"\n  mean {yr.mean():.0f}/yr over {len(yr)} calendar years")
