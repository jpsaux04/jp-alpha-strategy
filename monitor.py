#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  monitor.py — Watchdog, Dead-Man's Switch & Alerting   (READ-ONLY · Phase 3)
═══════════════════════════════════════════════════════════════════════════════

  A standalone health monitor for the JP Alpha Strategy v3 trading agent. It is
  intentionally SEPARATE from the trading engine (jp_agent.py) and the dashboard:
  its only job is to watch, judge, and alert.

  WHAT IT CHECKS  (each produces an Alert with a severity)
    • DEAD-MAN'S SWITCH  heartbeat.json missing / stale → the agent may be down
    • RUN RESULT         last run result != RUN_OK  or  n_errors > 0
    • REJECTED ORDERS    broker rejected one or more orders last run
    • DRAWDOWN BREACH    current drawdown worse than the configured limit
    • STATE DIVERGENCE   positions at the broker not tracked in state.json
                         (orphans → unmanaged) and vice-versa (ghosts)

  WHAT IT PRODUCES
    • A daily digest (equity, P&L, risk, positions, alerts) — and a weekly
      roll-up on Fridays.
    • monitor_status.json  — machine-readable snapshot the dashboard can surface.
    • logs/alerts.log      — an append-only audit trail of every alert.
    • Optional push to a webhook (Slack/Telegram/Discord) IF the environment
      variable ALERT_WEBHOOK_URL is set. No credentials live in this repo.

  HARD GUARANTEES
    • Imports only analytics.py + build_dashboard.py (both read-only) + stdlib
      + requests.
    • Places / modifies / cancels ZERO orders. Writes NO trading state
      (state.json, equity_curve.csv, etc. are never touched).
    • Every Alpaca call is a GET.

  Tunables live in config.json under the "monitor" key so thresholds can change
  without editing code:
      "monitor": {"heartbeat_stale_hours": 26, "max_drawdown_pct": 8.0}
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import analytics
from build_dashboard import fetch_live, fetch_portfolio_history, load_json

BASE = Path(__file__).parent
ET = ZoneInfo("America/New_York")

# ── Severity ladder (higher = louder) ────────────────────────────────────────
CRITICAL, HIGH, MEDIUM, INFO = "CRITICAL", "HIGH", "MEDIUM", "INFO"
_RANK = {CRITICAL: 3, HIGH: 2, MEDIUM: 1, INFO: 0}

# ── Defaults (overridable via config.json → "monitor") ───────────────────────
DEFAULTS = {
    "heartbeat_stale_hours": 26.0,   # a daily agent should beat ≤ 26h ago
    "max_drawdown_pct": 8.0,         # alert if current drawdown worse than this
}


class Alert:
    """A single monitoring finding."""
    __slots__ = ("severity", "code", "message", "detail")

    def __init__(self, severity, code, message, detail=None):
        self.severity = severity
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_dict(self):
        return {"severity": self.severity, "code": self.code,
                "message": self.message, "detail": self.detail}

    def line(self):
        return f"[{self.severity}] {self.code}: {self.message}"


# ─────────────────────────────────────────────────────────────────────────────
#  CHECKS  — each returns a list[Alert]  (empty = healthy)
# ─────────────────────────────────────────────────────────────────────────────

def check_heartbeat(hb, cfg, now_et):
    """Dead-man's switch: is the agent beating?"""
    if not hb or not hb.get("last_run_ts"):
        return [Alert(CRITICAL, "NO_HEARTBEAT",
                      "No heartbeat.json found — agent has never reported a run.")]
    try:
        last = datetime.fromisoformat(hb["last_run_ts"]).astimezone(ET)
    except Exception:
        return [Alert(HIGH, "HEARTBEAT_UNREADABLE",
                      f"heartbeat.json last_run_ts unparseable: {hb.get('last_run_ts')!r}")]
    age_h = (now_et - last).total_seconds() / 3600.0
    limit = float(cfg["heartbeat_stale_hours"])
    if age_h > limit:
        return [Alert(CRITICAL, "AGENT_STALE",
                      f"Agent last ran {age_h:.1f}h ago (limit {limit:.0f}h) — it may be down.",
                      {"age_hours": round(age_h, 1), "last_run": last.isoformat()})]
    return []


def check_run_result(hb):
    """Did the last run finish cleanly?"""
    out = []
    if not hb:
        return out
    result = hb.get("result", "?")
    if result and result != "RUN_OK":
        out.append(Alert(HIGH, "RUN_NOT_OK", f"Last run result was '{result}' (expected RUN_OK).",
                         {"result": result}))
    n_err = int(hb.get("n_errors", 0) or 0)
    if n_err > 0:
        out.append(Alert(HIGH, "RUN_ERRORS", f"Last run reported {n_err} error(s).",
                         {"n_errors": n_err}))
    n_rej = int(hb.get("n_orders_rejected", 0) or 0)
    if n_rej > 0:
        out.append(Alert(HIGH, "ORDERS_REJECTED",
                         f"Broker rejected {n_rej} order(s) on the last run.",
                         {"n_orders_rejected": n_rej}))
    return out


def check_drawdown(equity, cfg):
    """Is the strategy in a drawdown deeper than we tolerate?"""
    dd = analytics.drawdown(equity)
    if dd.get("insufficient_sample"):
        return []
    cur_pct = dd["current_dd"] * 100.0        # negative
    limit = float(cfg["max_drawdown_pct"])
    if cur_pct <= -abs(limit):
        return [Alert(HIGH, "DRAWDOWN_BREACH",
                      f"Current drawdown {cur_pct:+.2f}% breaches the {-abs(limit):.1f}% limit.",
                      {"current_dd_pct": round(cur_pct, 2),
                       "max_dd_pct": round(dd["max_dd"] * 100, 2)})]
    return []


def check_divergence(positions, state):
    """Broker vs strategy-memory reconciliation."""
    broker = {p.get("symbol") for p in (positions or []) if p.get("symbol")}
    tracked = set((state or {}).get("positions", {}).keys())
    out = []
    orphans = sorted(broker - tracked)   # held at broker, NOT managed by agent
    ghosts = sorted(tracked - broker)    # agent thinks it holds these; broker doesn't
    if orphans:
        out.append(Alert(HIGH, "ORPHAN_POSITIONS",
                         f"{len(orphans)} position(s) held at broker but untracked in "
                         f"state.json — the agent will NEVER manage or exit them: "
                         f"{', '.join(orphans)}.",
                         {"symbols": orphans}))
    if ghosts:
        out.append(Alert(HIGH, "GHOST_POSITIONS",
                         f"{len(ghosts)} position(s) tracked in state.json but NOT held at "
                         f"broker: {', '.join(ghosts)}.",
                         {"symbols": ghosts}))
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  DIGEST
# ─────────────────────────────────────────────────────────────────────────────

def build_digest(m, acct, positions, equity, alerts, now_et):
    """Human-readable daily digest (with a weekly roll-up on Fridays)."""
    pv = m["portfolio_value"]
    try:
        last_eq = float(acct.get("last_equity", pv)) if acct else pv
    except (TypeError, ValueError):
        last_eq = pv
    day_pl = pv - last_eq
    day_pl_pct = (day_pl / last_eq * 100) if last_eq else 0
    dd = m["drawdown"]
    car = m["open_risk"] or {}

    lines = []
    lines.append(f"JP ALPHA v4 — DAILY DIGEST · {now_et:%Y-%m-%d %H:%M ET}")
    lines.append("=" * 60)
    lines.append(f"Equity          : ${pv:,.2f}  ({m['total_return_pct']:+.2f}% since start)")
    lines.append(f"Day P&L         : ${day_pl:+,.2f}  ({day_pl_pct:+.2f}%)")
    lines.append(f"Open risk (CaR) : ${car.get('capital_at_risk', 0):,.2f}  "
                 f"({car.get('pct_of_equity', '—')}% of equity)")
    lines.append(f"Drawdown        : {dd['current_dd']*100:+.2f}% "
                 f"(max {dd['max_dd']*100:+.2f}%)")
    lines.append(f"Open positions  : {len(positions or [])}")

    # Weekly roll-up on Fridays (weekday 4)
    if now_et.weekday() == 4 and len(equity) >= 6:
        wk = equity[-6:]                       # ~5 trading days back
        wk_ret = (wk[-1]["pv"] / wk[0]["pv"] - 1) * 100 if wk[0]["pv"] else 0
        lines.append("-" * 60)
        lines.append(f"WEEK ({wk[0]['date']} → {wk[-1]['date']}): {wk_ret:+.2f}%")

    lines.append("-" * 60)
    if alerts:
        lines.append(f"ALERTS ({len(alerts)}):")
        for a in alerts:
            lines.append(f"  • {a.line()}")
    else:
        lines.append("ALERTS: none — all systems nominal. ✓")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  NOTIFY  (webhook optional; file output always)
# ─────────────────────────────────────────────────────────────────────────────

def notify(digest, alerts, now_et):
    """Persist to files always; push to a webhook only if one is configured."""
    # 1. audit trail
    logs = BASE / "logs"
    logs.mkdir(exist_ok=True)
    with open(logs / "alerts.log", "a") as f:
        for a in alerts:
            f.write(f"{now_et.isoformat()} {a.line()}\n")

    # 2. machine-readable status for the dashboard
    top = max((_RANK[a.severity] for a in alerts), default=-1)
    status = "OK" if top < 0 else next(k for k, v in _RANK.items() if v == top)
    (BASE / "monitor_status.json").write_text(json.dumps({
        "checked_at": now_et.isoformat(),
        "status": status,
        "n_alerts": len(alerts),
        "alerts": [a.as_dict() for a in alerts],
    }, indent=2))

    # 3. optional push
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if url and alerts:
        try:
            requests.post(url, json={"text": digest}, timeout=10)
            print("(alert pushed to webhook)")
        except Exception as e:
            print(f"(webhook push failed: {e})")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")

    raw = load_json(BASE / "config.json", {})
    cfg = {**DEFAULTS, **(raw.get("monitor") or {})}
    start_eq = float(raw.get("starting_equity", 100000.0) or 100000.0)
    state = load_json(BASE / "state.json", {"positions": {}})
    hb = load_json(BASE / "heartbeat.json", {})
    now_et = datetime.now(ET)

    acct, positions, clock = fetch_live()
    hist = fetch_portfolio_history()
    equity = hist if len(hist) >= 2 else analytics.load_equity_curve(BASE / "equity_curve.csv")
    m = analytics.compute_all(BASE / "equity_curve.csv", BASE / "trades_closed.csv",
                              acct, positions, start_eq,
                              equity_override=equity)

    alerts = []
    alerts += check_heartbeat(hb, cfg, now_et)
    alerts += check_run_result(hb)
    alerts += check_drawdown(equity, cfg)
    alerts += check_divergence(positions, state)
    alerts.sort(key=lambda a: _RANK[a.severity], reverse=True)

    digest = build_digest(m, acct, positions, equity, alerts, now_et)
    notify(digest, alerts, now_et)

    print(digest)
    # Non-zero exit if anything CRITICAL, so external supervisors can react.
    return 2 if any(a.severity == CRITICAL for a in alerts) else 0


if __name__ == "__main__":
    sys.exit(main())
