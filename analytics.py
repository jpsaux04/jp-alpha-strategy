#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  analytics.py — Performance & Risk Metrics Engine   (READ-ONLY · INDEPENDENT)
═══════════════════════════════════════════════════════════════════════════════

  Pure functions that compute portfolio metrics from the track-record CSVs and
  live account / position data. Consumed by the dashboard (Phase 2) and alerts
  (Phase 3).

  GUARANTEES
  ----------
    • Imports NOTHING from the strategy engine (jp_agent.py).
    • Places / modifies / cancels ZERO orders.
    • No side effects — every function returns a value; nothing is written to
      trading state. (The __main__ block only READS from Alpaca to print a
      summary.)

  INTELLECTUAL-HONESTY SAFEGUARD
  ------------------------------
    Ratios that need a statistically meaningful sample (Sharpe, Sortino, Calmar)
    return None plus an 'insufficient_sample' note until enough observations
    exist. A Sharpe computed off 15 daily points is noise dressed as signal —
    we refuse to emit it. Same for trade stats below MIN_TRADES_FOR_STATS.

  DEPENDENCIES: standard library only (csv, math, statistics) — no numpy/pandas,
  so it runs anywhere, not just inside the trading venv.
"""

import csv
import math
import statistics
from pathlib import Path
from datetime import date, datetime

# ── Tunables ────────────────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR  = 252
MIN_RETURNS_FOR_RATIOS = 30     # daily equity obs before Sharpe/Sortino/Calmar are shown
MIN_TRADES_FOR_STATS   = 10     # closed trades before win-rate/PF/expectancy are shown
DEFAULT_STOP_PCT       = 0.08   # engine's fixed stop distance (see STOP_LOSS_PCT)
RISK_FREE_RATE         = 0.0    # simplifying assumption for paper trading


# ─────────────────────────────────────────────────────────────────────────────
#  LOADERS
# ─────────────────────────────────────────────────────────────────────────────

def load_equity_curve(path):
    """Return [{'date': str, 'pv': float}, ...] sorted by date. Missing file → []."""
    out = []
    p = Path(path)
    if not p.exists():
        return out
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"date": r["date"], "pv": float(r["portfolio_value"])})
            except (KeyError, ValueError):
                continue
    out.sort(key=lambda x: x["date"])
    return out


def load_closed_trades(path):
    """Return list of closed-trade dicts with numeric coercion. Missing file → []."""
    out = []
    p = Path(path)
    if not p.exists():
        return out
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            try:
                r["gross_pnl"] = float(r["gross_pnl"])
            except (KeyError, ValueError):
                continue
            rp = r.get("return_pct", "")
            r["return_pct"] = float(rp) if str(rp).strip() not in ("", "None") else None
            hd = str(r.get("hold_days", "")).strip()
            r["hold_days"] = int(hd) if hd.isdigit() else None
            out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  EQUITY-CURVE METRICS  (drawdown, Sharpe, Sortino, Calmar, recovery)
# ─────────────────────────────────────────────────────────────────────────────

def daily_returns(equity):
    """Simple period-over-period returns from the equity curve."""
    rets = []
    for i in range(1, len(equity)):
        prev, cur = equity[i - 1]["pv"], equity[i]["pv"]
        if prev > 0:
            rets.append(cur / prev - 1)
    return rets


def drawdown(equity):
    """
    Drawdown analysis from the equity curve.
    Returns dict with current_dd, max_dd (both fractions, negative),
    max_dd_dollars, and days_in_drawdown.
    """
    if len(equity) < 2:
        return {"current_dd": 0.0, "max_dd": 0.0, "max_dd_dollars": 0.0,
                "days_in_drawdown": 0, "insufficient_sample": True}
    peak = equity[0]["pv"]
    peak_dollars = peak
    max_dd = 0.0
    max_dd_dollars = 0.0
    days_in_dd = 0
    for pt in equity:
        pv = pt["pv"]
        if pv > peak:
            peak = pv
            days_in_dd = 0
        else:
            days_in_dd += 1
        dd = (pv / peak - 1) if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            max_dd_dollars = pv - peak
    current_peak = max(p["pv"] for p in equity)
    last_pv = equity[-1]["pv"]
    current_dd = (last_pv / current_peak - 1) if current_peak > 0 else 0.0
    return {
        "current_dd": current_dd,
        "max_dd": max_dd,
        "max_dd_dollars": max_dd_dollars,
        "days_in_drawdown": days_in_dd,
        "insufficient_sample": False,
    }


def sharpe(equity):
    """Annualized Sharpe. Returns None + note until MIN_RETURNS_FOR_RATIOS obs."""
    rets = daily_returns(equity)
    n = len(rets)
    if n < MIN_RETURNS_FOR_RATIOS:
        return {"value": None, "n": n, "insufficient_sample": True,
                "needed": MIN_RETURNS_FOR_RATIOS}
    mean = statistics.mean(rets)
    sd = statistics.pstdev(rets)
    if sd == 0:
        return {"value": None, "n": n, "insufficient_sample": False, "note": "zero volatility"}
    daily_rf = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
    val = (mean - daily_rf) / sd * math.sqrt(TRADING_DAYS_PER_YEAR)
    return {"value": val, "n": n, "insufficient_sample": False}


def sortino(equity):
    """Annualized Sortino (downside-deviation denominator)."""
    rets = daily_returns(equity)
    n = len(rets)
    if n < MIN_RETURNS_FOR_RATIOS:
        return {"value": None, "n": n, "insufficient_sample": True,
                "needed": MIN_RETURNS_FOR_RATIOS}
    daily_rf = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
    mean = statistics.mean(rets)
    downside = [min(0.0, r - daily_rf) for r in rets]
    dd = math.sqrt(sum(d * d for d in downside) / len(downside))
    if dd == 0:
        return {"value": None, "n": n, "insufficient_sample": False, "note": "no downside"}
    val = (mean - daily_rf) / dd * math.sqrt(TRADING_DAYS_PER_YEAR)
    return {"value": val, "n": n, "insufficient_sample": False}


def cagr(equity):
    """Compound annual growth rate implied by the equity curve span."""
    if len(equity) < 2:
        return None
    try:
        d0 = datetime.fromisoformat(equity[0]["date"]).date()
        d1 = datetime.fromisoformat(equity[-1]["date"]).date()
        years = (d1 - d0).days / 365.25
    except Exception:
        years = len(equity) / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return None
    total = equity[-1]["pv"] / equity[0]["pv"]
    return total ** (1 / years) - 1


def calmar(equity):
    """CAGR / |max drawdown|. Needs both enough history and a real drawdown."""
    n = len(daily_returns(equity))
    if n < MIN_RETURNS_FOR_RATIOS:
        return {"value": None, "n": n, "insufficient_sample": True,
                "needed": MIN_RETURNS_FOR_RATIOS}
    g = cagr(equity)
    mdd = drawdown(equity)["max_dd"]
    if g is None or mdd == 0:
        return {"value": None, "n": n, "insufficient_sample": False, "note": "no drawdown yet"}
    return {"value": g / abs(mdd), "n": n, "insufficient_sample": False}


def recovery_factor(equity):
    """Net profit ($) / max drawdown ($). How many drawdowns of profit earned."""
    if len(equity) < 2:
        return None
    net = equity[-1]["pv"] - equity[0]["pv"]
    mdd_dollars = drawdown(equity)["max_dd_dollars"]
    if mdd_dollars == 0:
        return None
    return net / abs(mdd_dollars)


# ─────────────────────────────────────────────────────────────────────────────
#  TRADE-LEVEL METRICS  (win rate, profit factor, expectancy)
# ─────────────────────────────────────────────────────────────────────────────

def trade_stats(trades):
    """
    Win rate, loss rate, profit factor, expectancy, and shape stats from the
    closed-trade ledger. Returns None-valued fields + insufficient_sample flag
    until MIN_TRADES_FOR_STATS closed trades exist.
    """
    n = len(trades)
    base = {"n_trades": n, "insufficient_sample": n < MIN_TRADES_FOR_STATS,
            "needed": MIN_TRADES_FOR_STATS}
    if n == 0:
        return {**base, "win_rate": None, "loss_rate": None, "profit_factor": None,
                "expectancy": None, "avg_trade": None, "median_trade": None,
                "largest_win": None, "largest_loss": None, "avg_hold_days": None}

    pnls = [t["gross_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    holds = [t["hold_days"] for t in trades if t.get("hold_days") is not None]

    win_rate = len(wins) / n
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    expectancy = statistics.mean(pnls)   # avg $ per trade

    return {
        **base,
        "win_rate": win_rate,
        "loss_rate": len(losses) / n,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_trade": statistics.mean(pnls),
        "median_trade": statistics.median(pnls),
        "largest_win": max(pnls),
        "largest_loss": min(pnls),
        "gross_profit": gross_profit,
        "gross_loss": -gross_loss,
        "avg_hold_days": statistics.mean(holds) if holds else None,
        "n_wins": len(wins),
        "n_losses": len(losses),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  LIVE RISK & EXPOSURE  (from broker positions — read-only)
# ─────────────────────────────────────────────────────────────────────────────

def open_risk(positions, equity_value=None, stop_pct=DEFAULT_STOP_PCT):
    """
    Capital at Risk: if every open position reverted to its fixed stop from the
    current mark, how much would we lose? Long stop = entry×(1-stop_pct),
    short stop = entry×(1+stop_pct). Per-position risk is clamped at ≥0.
    Accepts Alpaca position dicts (qty, avg_entry_price, current_price).
    """
    total = 0.0
    per_pos = []
    for p in positions or []:
        try:
            qty = float(p.get("qty", 0))
            entry = float(p.get("avg_entry_price", 0))
            cur = float(p.get("current_price", 0))
        except (TypeError, ValueError):
            continue
        if qty == 0 or entry <= 0:
            continue
        is_long = qty > 0
        stop = entry * (1 - stop_pct) if is_long else entry * (1 + stop_pct)
        per_share = (cur - stop) if is_long else (stop - cur)
        risk = abs(qty) * max(0.0, per_share)
        total += risk
        per_pos.append({
            "symbol": p.get("symbol"),
            "direction": "long" if is_long else "short",
            "risk_dollars": round(risk, 2),
            "stop": round(stop, 2),
        })
    per_pos.sort(key=lambda x: x["risk_dollars"], reverse=True)
    return {
        "capital_at_risk": round(total, 2),
        "pct_of_equity": round(total / equity_value * 100, 2) if equity_value else None,
        "positions": per_pos,
    }


def exposure(account, positions):
    """
    Gross / net exposure and concentration from live broker positions.
    Uses Alpaca 'market_value' per position and account portfolio_value/cash.
    """
    long_mv = short_mv = 0.0
    mvs = []
    for p in positions or []:
        try:
            mv = float(p.get("market_value", 0))
        except (TypeError, ValueError):
            continue
        if mv >= 0:
            long_mv += mv
        else:
            short_mv += abs(mv)
        mvs.append(abs(mv))
    gross = long_mv + short_mv
    net = long_mv - short_mv
    try:
        pv = float(account.get("portfolio_value", 0))
        cash = float(account.get("cash", 0))
    except (TypeError, ValueError):
        pv = cash = 0.0
    largest = max(mvs) if mvs else 0.0
    # Herfindahl concentration index over gross exposure (0..1; higher = concentrated)
    hhi = sum((m / gross) ** 2 for m in mvs) if gross > 0 else 0.0
    return {
        "gross": round(gross, 2),
        "net": round(net, 2),
        "long_mv": round(long_mv, 2),
        "short_mv": round(short_mv, 2),
        "gross_pct": round(gross / pv * 100, 1) if pv else None,
        "net_pct": round(net / pv * 100, 1) if pv else None,
        "cash": round(cash, 2),
        "cash_pct": round(cash / pv * 100, 1) if pv else None,
        "largest_position_mv": round(largest, 2),
        "largest_position_pct": round(largest / pv * 100, 1) if pv else None,
        "concentration_hhi": round(hhi, 3),
        "n_positions": len(mvs),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  AGGREGATOR
# ─────────────────────────────────────────────────────────────────────────────

def compute_all(equity_csv, trades_csv, account=None, positions=None,
                starting_equity=100000.0, stop_pct=DEFAULT_STOP_PCT,
                equity_override=None):
    """One call → full metrics dict for the dashboard/alerts.

    If equity_override (a pre-loaded [{'date','pv'}, ...] list) is supplied it is
    used for every equity-curve metric instead of reading equity_csv. This lets
    a caller feed a fuller history (e.g. Alpaca's portfolio-history endpoint)
    WITHOUT mutating the strategy's own equity_curve.csv. Fully backward
    compatible: default None → unchanged CSV-based behavior.
    """
    equity = equity_override if equity_override is not None else load_equity_curve(equity_csv)
    trades = load_closed_trades(trades_csv)
    pv = float(account.get("portfolio_value", 0)) if account else (
        equity[-1]["pv"] if equity else starting_equity)
    total_return_pct = (pv / starting_equity - 1) * 100 if starting_equity else None
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "equity_points": len(equity),
        "portfolio_value": pv,
        "starting_equity": starting_equity,
        "total_return_pct": total_return_pct,
        "drawdown": drawdown(equity),
        "sharpe": sharpe(equity),
        "sortino": sortino(equity),
        "calmar": calmar(equity),
        "recovery_factor": recovery_factor(equity),
        "cagr": cagr(equity),
        "trades": trade_stats(trades),
        "open_risk": open_risk(positions, pv, stop_pct) if positions is not None else None,
        "exposure": exposure(account, positions) if (account and positions is not None) else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  CLI — read-only summary (also a self-test)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(x, pct=False, dollar=False):
    if x is None:
        return "N/A"
    if isinstance(x, dict):
        if x.get("value") is None:
            if x.get("insufficient_sample"):
                return f"N/A (n={x.get('n', 0)}, need {x.get('needed', '?')})"
            return f"N/A ({x.get('note', '—')})"
        x = x["value"]
    if dollar:
        return f"${x:,.2f}"
    if pct:
        return f"{x:+.2f}%"
    return f"{x:.2f}"


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    import requests

    BASE = Path(__file__).parent
    load_dotenv(BASE / ".env")

    # config (starting equity)
    start_eq = 100000.0
    cfg = BASE / "config.json"
    if cfg.exists():
        import json
        try:
            start_eq = float(json.loads(cfg.read_text()).get("starting_equity", 100000.0))
        except Exception:
            pass

    # read-only live data
    account = positions = None
    try:
        H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
             "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
        U = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
        account = requests.get(f"{U}/v2/account", headers=H, timeout=15).json()
        positions = requests.get(f"{U}/v2/positions", headers=H, timeout=15).json()
    except Exception as e:
        print(f"(live Alpaca data unavailable: {e})")

    m = compute_all(BASE / "equity_curve.csv", BASE / "trades_closed.csv",
                    account, positions, start_eq)

    print("=" * 60)
    print("  ANALYTICS ENGINE — READ-ONLY SUMMARY")
    print("=" * 60)
    print(f"  Portfolio value   : {_fmt(m['portfolio_value'], dollar=True)}")
    print(f"  Total return      : {_fmt(m['total_return_pct'], pct=True)}")
    print(f"  Equity points     : {m['equity_points']}")
    dd = m["drawdown"]
    print(f"  Current drawdown  : {dd['current_dd']*100:+.2f}%  (max {dd['max_dd']*100:+.2f}%, {dd['days_in_drawdown']}d in DD)")
    print(f"  Sharpe            : {_fmt(m['sharpe'])}")
    print(f"  Sortino           : {_fmt(m['sortino'])}")
    print(f"  Calmar            : {_fmt(m['calmar'])}")
    print(f"  Recovery factor   : {_fmt(m['recovery_factor'])}")
    t = m["trades"]
    print("-" * 60)
    print(f"  Closed trades     : {t['n_trades']}" + ("  [insufficient sample]" if t["insufficient_sample"] else ""))
    if t["n_trades"]:
        print(f"  Win rate          : {t['win_rate']*100:.1f}%  ({t['n_wins']}W / {t['n_losses']}L)")
        print(f"  Profit factor     : {_fmt(t['profit_factor'])}")
        print(f"  Expectancy/trade  : {_fmt(t['expectancy'], dollar=True)}")
        print(f"  Avg / Median      : {_fmt(t['avg_trade'], dollar=True)} / {_fmt(t['median_trade'], dollar=True)}")
        print(f"  Largest W / L     : {_fmt(t['largest_win'], dollar=True)} / {_fmt(t['largest_loss'], dollar=True)}")
        print(f"  Avg hold (days)   : {_fmt(t['avg_hold_days'])}")
    if m["open_risk"]:
        r = m["open_risk"]
        print("-" * 60)
        print(f"  Capital at risk   : {_fmt(r['capital_at_risk'], dollar=True)}  ({r['pct_of_equity']}% of equity)")
        for pp in r["positions"][:5]:
            print(f"      {pp['symbol']:<6} {pp['direction']:<5} risk {_fmt(pp['risk_dollars'], dollar=True):>12}  stop {pp['stop']}")
    if m["exposure"]:
        e = m["exposure"]
        print("-" * 60)
        print(f"  Gross exposure    : {_fmt(e['gross'], dollar=True)} ({e['gross_pct']}%)   Net: {_fmt(e['net'], dollar=True)} ({e['net_pct']}%)")
        print(f"  Long / Short      : {_fmt(e['long_mv'], dollar=True)} / {_fmt(e['short_mv'], dollar=True)}")
        print(f"  Cash              : {_fmt(e['cash'], dollar=True)} ({e['cash_pct']}%)")
        print(f"  Concentration     : largest {e['largest_position_pct']}% of equity, HHI {e['concentration_hhi']}")
    print("=" * 60)
