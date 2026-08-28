#!/usr/bin/env python3
"""Backtest tear sheet — equity curve, drawdown, trade P&L, yearly & exit breakdowns.
Read-only: consumes backtest_equity.csv / backtest_trades.csv, writes backtest_tearsheet.png."""
import csv, json
from collections import defaultdict
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

BASE = "/root/jp_strategy/"

# ---- load equity curve ----
eq_dates, eq_pv = [], []
with open(BASE + "backtest_equity.csv") as f:
    for r in csv.DictReader(f):
        eq_dates.append(datetime.strptime(r["date"], "%Y-%m-%d"))
        eq_pv.append(float(r["pv"]))

# drawdown
peak = eq_pv[0]; dd = []
for v in eq_pv:
    peak = max(peak, v)
    dd.append((v / peak - 1) * 100)

# ---- load trades ----
trades = []
with open(BASE + "backtest_trades.csv") as f:
    for r in csv.DictReader(f):
        r["gross_pnl"] = float(r["gross_pnl"])
        r["exit_date"] = datetime.strptime(r["exit_date"], "%Y-%m-%d")
        trades.append(r)
trades.sort(key=lambda t: t["exit_date"])

# cumulative realized P&L by trade
cum = 0; cum_pnl = []; cum_x = []
for t in trades:
    cum += t["gross_pnl"]; cum_pnl.append(cum); cum_x.append(t["exit_date"])

# by direction
by_dir = defaultdict(float)
for t in trades:
    by_dir[t["direction"]] += t["gross_pnl"]

# by exit reason
by_exit = defaultdict(float)
for t in trades:
    by_exit[t["exit_reason"]] += t["gross_pnl"]

# by year (realized on exit)
by_year = defaultdict(float)
for t in trades:
    by_year[t["exit_date"].year] += t["gross_pnl"]

results = json.load(open(BASE + "backtest_results.json"))

# ================= PLOT =================
fig = plt.figure(figsize=(16, 13))
fig.patch.set_facecolor("white")
gs = fig.add_gridspec(4, 2, height_ratios=[2.2, 1.1, 1.4, 1.4], hspace=0.45, wspace=0.22)

# 1) Equity curve
ax1 = fig.add_subplot(gs[0, :])
ax1.plot(eq_dates, eq_pv, color="#1f4e79", lw=1.6)
ax1.axhline(100000, color="#888", ls="--", lw=1, label="Starting capital $100k")
ax1.fill_between(eq_dates, eq_pv, 100000, where=[v >= 100000 for v in eq_pv],
                 color="#2e7d32", alpha=0.12)
ax1.fill_between(eq_dates, eq_pv, 100000, where=[v < 100000 for v in eq_pv],
                 color="#c62828", alpha=0.12)
ax1.set_title("JP Alpha Strategy — Backtest Equity Curve (2019–2026, out-of-sample)",
              fontsize=15, fontweight="bold")
ax1.set_ylabel("Portfolio value ($)")
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
ax1.legend(loc="upper right", fontsize=9)
ax1.grid(alpha=0.25)
txt = (f"Final: ${results['final_equity']:,.0f}   "
       f"Total: {results['total_return_pct']:+.1f}%   "
       f"CAGR: {results['cagr_pct']:+.1f}%   "
       f"Sharpe: {results['sharpe']:.2f}   "
       f"MaxDD: {results['max_drawdown_pct']:.1f}%   "
       f"PF: {results['profit_factor']:.2f}   "
       f"Win: {results['win_rate_pct']:.0f}%")
ax1.text(0.005, -0.16, txt, transform=ax1.transAxes, fontsize=10.5,
         color="#333", fontweight="bold")

# 2) Drawdown
ax2 = fig.add_subplot(gs[1, :])
ax2.fill_between(eq_dates, dd, 0, color="#c62828", alpha=0.35)
ax2.plot(eq_dates, dd, color="#c62828", lw=0.9)
ax2.set_title("Drawdown (%)", fontsize=12, fontweight="bold")
ax2.set_ylabel("%")
ax2.grid(alpha=0.25)

# 3) Cumulative realized P&L by trade
ax3 = fig.add_subplot(gs[2, :])
ax3.plot(cum_x, cum_pnl, color="#5e35b1", lw=1.4)
ax3.axhline(0, color="#888", ls="--", lw=1)
ax3.fill_between(cum_x, cum_pnl, 0, where=[v >= 0 for v in cum_pnl], color="#2e7d32", alpha=0.12)
ax3.fill_between(cum_x, cum_pnl, 0, where=[v < 0 for v in cum_pnl], color="#c62828", alpha=0.12)
ax3.set_title(f"Cumulative realized P&L across {len(trades)} closed trades",
              fontsize=12, fontweight="bold")
ax3.set_ylabel("Cumulative $")
ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:+.0f}k"))
ax3.grid(alpha=0.25)

# 4) P&L by exit reason
ax4 = fig.add_subplot(gs[3, 0])
items = sorted(by_exit.items(), key=lambda kv: kv[1])
labels = [k for k, _ in items]; vals = [v for _, v in items]
colors = ["#c62828" if v < 0 else "#2e7d32" for v in vals]
ax4.barh(labels, vals, color=colors)
ax4.axvline(0, color="#333", lw=0.8)
ax4.set_title("P&L by exit reason ($)", fontsize=12, fontweight="bold")
ax4.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1000:+.0f}k"))
for i, v in enumerate(vals):
    ax4.text(v, i, f" {v/1000:+.0f}k", va="center",
             ha="left" if v >= 0 else "right", fontsize=8.5)
ax4.grid(axis="x", alpha=0.25)

# 5) P&L by year (with direction annotation)
ax5 = fig.add_subplot(gs[3, 1])
years = sorted(by_year)
yvals = [by_year[y] for y in years]
ycolors = ["#c62828" if v < 0 else "#2e7d32" for v in yvals]
ax5.bar([str(y) for y in years], yvals, color=ycolors)
ax5.axhline(0, color="#333", lw=0.8)
ax5.set_title(f"Realized P&L by year   |   longs {by_dir['long']/1000:+.0f}k · "
              f"shorts {by_dir['short']/1000:+.0f}k", fontsize=11.5, fontweight="bold")
ax5.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1000:+.0f}k"))
ax5.tick_params(axis="x", rotation=45)
ax5.grid(axis="y", alpha=0.25)

fig.suptitle("", fontsize=1)
plt.savefig(BASE + "backtest_tearsheet.png", dpi=110, bbox_inches="tight",
            facecolor="white")
print("wrote backtest_tearsheet.png")
print("direction:", dict(by_dir))
print("exit:", {k: round(v) for k, v in by_exit.items()})
print("year:", {k: round(v) for k, v in by_year.items()})
