#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  build_dashboard.py — Institutional Command Center (STATIC HTML · READ-ONLY)
═══════════════════════════════════════════════════════════════════════════════

  Renders a single self-contained dashboard.html from:
    • equity_curve.csv, trades_closed.csv, positions_history.csv
    • heartbeat.json, config.json
    • live Alpaca account/positions/clock  (read-only GET)
    • analytics.py  (metrics engine)

  GUARANTEES
    • Imports only analytics.py (read-only) + stdlib + requests.
    • Places / modifies / cancels ZERO orders. No trading-state writes.
    • Output is one HTML file. Nothing else is touched.

  Information hierarchy (top → bottom, per the approved wireframe):
    1. Status banner (heartbeat liveness + next scheduled run)
    2. Four hero tiles: Equity · Day P&L · Open Risk (CaR) · Drawdown
    3. Equity curve + underwater (drawdown) chart
    4. Open positions — sorted by open risk, with distance-to-stop / -target
    5. Exposure (long/short/cash) + concentration
    6. Performance stats (win% · PF · expectancy · Sharpe* w/ sample guard)
    7. Recent closed trades
    8. Ops footer (last run result, counts)
"""

import os
import json
import html
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import analytics

BASE = Path(__file__).parent
ET = ZoneInfo("America/New_York")

STOP_PCT = 0.08
T1, T2, T3 = 0.04, 0.08, 0.12


# ─────────────────────────────────────────────────────────────────────────────
#  DATA GATHERING (all read-only)
# ─────────────────────────────────────────────────────────────────────────────

def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def fetch_live():
    """Read-only Alpaca snapshot. Returns (account, positions, clock) or (None,…)."""
    try:
        H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
             "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
        U = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
        acct = requests.get(f"{U}/v2/account", headers=H, timeout=15).json()
        pos = requests.get(f"{U}/v2/positions", headers=H, timeout=15).json()
        clock = requests.get(f"{U}/v2/clock", headers=H, timeout=15).json()
        return acct, pos, clock
    except Exception as e:
        print(f"(live data unavailable: {e})")
        return None, [], {}


def fetch_portfolio_history(period="3M", timeframe="1D"):
    """Read-only Alpaca portfolio equity history → [{'date','pv'}, ...] sorted.

    Filters out leading zero-equity (pre-funding / no-data) days so the curve
    starts where the account actually held value. Returns [] on any failure —
    the caller falls back to the local equity_curve.csv.
    """
    try:
        H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
             "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
        U = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
        r = requests.get(f"{U}/v2/account/portfolio/history",
                         params={"period": period, "timeframe": timeframe},
                         headers=H, timeout=15).json()
        ts = r.get("timestamp", []) or []
        eq = r.get("equity", []) or []
        out = []
        for t, e in zip(ts, eq):
            try:
                pv = float(e)
            except (TypeError, ValueError):
                continue
            if pv <= 0:                      # skip pre-funding / empty days
                continue
            d = datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat()
            out.append({"date": d, "pv": pv})
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as ex:
        print(f"(portfolio history unavailable: {ex})")
        return []


def fetch_spy_series(start_date, feed="iex"):
    """Read-only SPY daily closes from Alpaca market data → {date: close}.

    Used to draw a buy-and-hold benchmark over the equity curve. Paginates via
    next_page_token. Returns {} on any failure — the overlay is simply omitted.
    """
    try:
        H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
             "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
        out, page_token = {}, None
        for _ in range(20):                       # hard page cap (safety)
            params = {"timeframe": "1Day", "start": start_date,
                      "limit": 10000, "feed": feed, "adjustment": "all"}
            if page_token:
                params["page_token"] = page_token
            r = requests.get("https://data.alpaca.markets/v2/stocks/SPY/bars",
                             params=params, headers=H, timeout=15).json()
            for b in r.get("bars", []) or []:
                try:
                    d = str(b["t"])[:10]          # 'YYYY-MM-DD'
                    out[d] = float(b["c"])
                except (KeyError, TypeError, ValueError):
                    continue
            page_token = r.get("next_page_token")
            if not page_token:
                break
        return out
    except Exception as ex:
        print(f"(SPY benchmark unavailable: {ex})")
        return {}


def spy_benchmark(equity, spy_map):
    """Normalize SPY to the equity curve's starting value → list aligned to
    equity dates (buy-and-hold: '$ if you'd held SPY instead'). Forward-fills
    missing days; returns None if no usable overlap."""
    if not equity or not spy_map:
        return None
    start_pv = equity[0]["pv"]
    aligned, last = [], None
    for e in equity:
        px = spy_map.get(e["date"], last)
        last = px if px is not None else last
        aligned.append(last)
    base = next((x for x in aligned if x), None)
    if not base:
        return None
    return [round(start_pv * (x / base), 2) if x else None for x in aligned]


def enrich_positions(positions, state):
    """Attach stop, next target, distances, days, T1/T2 flags to each Alpaca position."""
    sp = state.get("positions", {})
    rows = []
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
        meta = sp.get(p.get("symbol"), {})
        t1_hit = bool(meta.get("t1_hit", False))
        t2_hit = bool(meta.get("t2_hit", False))

        if is_long:
            stop = entry * (1 - STOP_PCT)
            nxt = entry * (1 + (T3 if t2_hit else T2 if t1_hit else T1))
            dist_stop = (cur / stop - 1) * 100 if stop else 0      # cushion above stop
            dist_tgt = (nxt / cur - 1) * 100 if cur else 0         # room to target
        else:
            stop = entry * (1 + STOP_PCT)
            nxt = entry * (1 - (T3 if t2_hit else T2 if t1_hit else T1))
            dist_stop = (stop / cur - 1) * 100 if cur else 0
            dist_tgt = (cur / nxt - 1) * 100 if nxt else 0

        per_share = (cur - stop) if is_long else (stop - cur)
        risk = abs(qty) * max(0.0, per_share)
        try:
            plpc = float(p.get("unrealized_plpc", 0)) * 100
            upl = float(p.get("unrealized_pl", 0))
        except (TypeError, ValueError):
            plpc = upl = 0.0
        ed = meta.get("entry_date", "")
        days = ""
        if ed:
            try:
                days = (datetime.now(ET).date() - datetime.fromisoformat(ed).date()).days
            except Exception:
                days = ""
        rows.append({
            "symbol": p.get("symbol"), "dir": "L" if is_long else "S",
            "qty": abs(int(qty)), "entry": entry, "cur": cur,
            "upl": upl, "plpc": plpc, "stop": stop, "target": nxt,
            "dist_stop": dist_stop, "dist_tgt": dist_tgt, "risk": risk,
            "days": days, "t1": t1_hit, "t2": t2_hit,
            "in_state": p.get("symbol") in sp,
        })
    rows.sort(key=lambda r: r["risk"], reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
#  HTML RENDERING
# ─────────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--fg:#e6edf3;--mut:#8b949e;
--grn:#2ea043;--red:#f85149;--amb:#d29922;--blu:#388bfd}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:16px 20px 40px}
h1{font-size:16px;font-weight:600;letter-spacing:.02em}
.mut{color:var(--mut)} .grn{color:var(--grn)} .red{color:var(--red)} .amb{color:var(--amb)}
.mono{font-family:"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.banner{display:flex;align-items:center;gap:14px;padding:10px 14px;border-radius:8px;margin-bottom:16px;font-weight:600}
.banner .dot{width:10px;height:10px;border-radius:50%}
.live{background:rgba(46,160,67,.12);border:1px solid rgba(46,160,67,.4)}
.stale{background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.4)}
.dot-live{background:var(--grn);box-shadow:0 0 8px var(--grn)} .dot-stale{background:var(--red)}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.tile .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:6px}
.tile .val{font-size:24px;font-weight:600}
.tile .sub{font-size:12px;color:var(--mut);margin-top:3px}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-bottom:16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:16px}
.panel h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:12px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:right;color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:6px 8px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
td{text-align:right;padding:7px 8px;border-bottom:1px solid rgba(48,54,61,.5)}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600}
.tag-L{background:rgba(56,139,253,.15);color:#79c0ff} .tag-S{background:rgba(210,153,34,.15);color:#e3b341}
.kv{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(48,54,61,.5)}
.kv:last-child{border:none}
.footer{color:var(--mut);font-size:12px;margin-top:8px}
.warn{color:var(--amb);font-size:12px}
"""


def color_num(v, fmt="{:+,.2f}", zero_neutral=True):
    if v is None:
        return '<span class="mut">N/A</span>'
    cls = "grn" if v > 0 else "red" if v < 0 else "mut"
    return f'<span class="{cls} mono">{fmt.format(v)}</span>'


def ratio_str(d):
    if not isinstance(d, dict):
        return "N/A"
    if d.get("value") is None:
        if d.get("insufficient_sample"):
            return f'<span class="mut">N/A · n={d.get("n",0)}/{d.get("needed","?")}</span>'
        return f'<span class="mut">N/A</span>'
    return f'<span class="mono">{d["value"]:.2f}</span>'


def render(m, acct, positions, clock, hb, equity, spy=None):
    now_et = datetime.now(ET)
    pv = m["portfolio_value"]

    # ── Banner: heartbeat liveness ──
    stale = True
    hb_txt = "NO HEARTBEAT YET"
    if hb.get("last_run_ts"):
        try:
            last = datetime.fromisoformat(hb["last_run_ts"])
            age_h = (now_et - last.astimezone(ET)).total_seconds() / 3600
            stale = age_h > 26
            hb_txt = f'last run {last.astimezone(ET):%Y-%m-%d %H:%M ET} ({age_h:.0f}h ago) · {hb.get("result","?")}'
        except Exception:
            pass
    next_open = clock.get("next_open", "")[:16].replace("T", " ") if clock else "?"
    bcls = "stale" if stale else "live"
    dcls = "dot-stale" if stale else "dot-live"
    btext = "STALE — agent may be down" if stale else "LIVE"
    banner = (f'<div class="banner {bcls}"><span class="dot {dcls}"></span>'
              f'<span>{btext}</span><span class="mut" style="font-weight:400">{html.escape(hb_txt)}'
              f' · next open {html.escape(next_open)}</span></div>')

    # ── Hero tiles ──
    try:
        last_eq = float(acct.get("last_equity", pv)) if acct else pv
    except (TypeError, ValueError):
        last_eq = pv
    day_pl = pv - last_eq
    day_pl_pct = (day_pl / last_eq * 100) if last_eq else 0
    dd = m["drawdown"]
    car = m["open_risk"] or {}
    tot = m["total_return_pct"]

    tiles = f"""
    <div class="tiles">
      <div class="tile"><div class="lbl">Equity</div>
        <div class="val mono">${pv:,.0f}</div>
        <div class="sub">Total {color_num(tot, "{:+.2f}%")} vs ${m['starting_equity']:,.0f}</div></div>
      <div class="tile"><div class="lbl">Day P&amp;L</div>
        <div class="val">{color_num(day_pl, "${:+,.0f}")}</div>
        <div class="sub">{color_num(day_pl_pct, "{:+.2f}%")} since prior close</div></div>
      <div class="tile"><div class="lbl">Open Risk (CaR)</div>
        <div class="val mono">${car.get('capital_at_risk',0):,.0f}</div>
        <div class="sub">{car.get('pct_of_equity','—')}% of equity if all stops hit</div></div>
      <div class="tile"><div class="lbl">Drawdown</div>
        <div class="val">{color_num(dd['current_dd']*100, "{:+.2f}%")}</div>
        <div class="sub">max {dd['max_dd']*100:+.2f}% · {dd['days_in_drawdown']}d in DD</div></div>
    </div>"""

    # ── Charts data ──
    labels = [e["date"] for e in equity]
    pvs = [e["pv"] for e in equity]
    # underwater
    peak = float("-inf"); uw = []
    for v in pvs:
        peak = max(peak, v)
        uw.append(round((v / peak - 1) * 100, 2) if peak > 0 else 0)

    # ── SPY buy-and-hold benchmark (optional overlay) ──
    spy_data = spy if (spy and len(spy) == len(pvs)) else None
    bench_caption = ""
    if spy_data and pvs[0] and spy_data[0] and spy_data[-1]:
        strat_ret = (pvs[-1] / pvs[0] - 1) * 100
        spy_ret = (spy_data[-1] / spy_data[0] - 1) * 100
        alpha = strat_ret - spy_ret
        acls = "grn" if alpha >= 0 else "red"
        bench_caption = (
            f'<div class="footer">Since {labels[0]}: '
            f'strategy <span class="{"grn" if strat_ret>=0 else "red"} mono">{strat_ret:+.2f}%</span> · '
            f'SPY buy &amp; hold <span class="{"grn" if spy_ret>=0 else "red"} mono">{spy_ret:+.2f}%</span> · '
            f'excess <span class="{acls} mono">{alpha:+.2f}%</span></div>')
    chart = f"""
    <div class="panel"><h2>Equity Curve &amp; Drawdown{' vs SPY' if spy_data else ''}</h2>
      {"<div class='warn'>Only "+str(len(pvs))+" data point(s) — the curve fills in as the daily cron accrues history.</div>" if len(pvs)<2 else ""}
      <canvas id="eq" height="90"></canvas>
      <canvas id="uw" height="45" style="margin-top:8px"></canvas>{bench_caption}</div>"""

    # ── Positions table ──
    prows = ""
    for r in positions:
        star = "" if r["in_state"] else ' <span class="amb" title="in broker but not in state.json">*</span>'
        t1 = "✓" if r["t1"] else "·"; t2 = "✓" if r["t2"] else "·"
        prows += f"""<tr>
          <td>{html.escape(str(r['symbol']))}{star} <span class="tag tag-{r['dir']}">{r['dir']}</span></td>
          <td class="mono">{r['qty']}</td>
          <td class="mono">{r['entry']:.2f}</td>
          <td class="mono">{r['cur']:.2f}</td>
          <td>{color_num(r['plpc'],'{:+.1f}%')}</td>
          <td class="mono">{r['stop']:.2f}</td>
          <td class="mono grn">{r['dist_stop']:+.1f}%</td>
          <td class="mono">{r['target']:.2f}</td>
          <td class="mono blu">{r['dist_tgt']:+.1f}%</td>
          <td class="mono amb">${r['risk']:,.0f}</td>
          <td class="mono">{r['days']}</td>
          <td class="mono">{t1}/{t2}</td></tr>"""
    if not prows:
        prows = '<tr><td colspan="12" class="mut">No open positions.</td></tr>'
    postable = f"""
    <div class="panel"><h2>Open Positions — sorted by open risk</h2>
      <table><thead><tr>
        <th>Symbol</th><th>Qty</th><th>Entry</th><th>Now</th><th>uP&amp;L</th>
        <th>Stop</th><th>→Stop</th><th>Target</th><th>→Tgt</th><th>Risk $</th><th>Days</th><th>T1/T2</th>
      </tr></thead><tbody>{prows}</tbody></table>
      <div class="footer">* = held at broker but not tracked in state.json (divergence)</div></div>"""

    # ── Exposure + Stats side by side ──
    e = m["exposure"] or {}
    expo = f"""
    <div class="panel"><h2>Exposure &amp; Concentration</h2>
      <div class="kv"><span class="mut">Gross</span><span class="mono">${e.get('gross',0):,.0f} ({e.get('gross_pct','—')}%)</span></div>
      <div class="kv"><span class="mut">Net</span><span class="mono">${e.get('net',0):,.0f} ({e.get('net_pct','—')}%)</span></div>
      <div class="kv"><span class="mut">Long / Short</span><span class="mono">${e.get('long_mv',0):,.0f} / ${e.get('short_mv',0):,.0f}</span></div>
      <div class="kv"><span class="mut">Cash</span><span class="mono">${e.get('cash',0):,.0f} ({e.get('cash_pct','—')}%)</span></div>
      <div class="kv"><span class="mut">Largest position</span><span class="mono">{e.get('largest_position_pct','—')}% of equity</span></div>
      <div class="kv"><span class="mut">Concentration (HHI)</span><span class="mono">{e.get('concentration_hhi','—')}</span></div></div>"""

    t = m["trades"]
    warn = ' <span class="warn">(account-level, incl. legacy trades)</span>' if t["n_trades"] else ""
    guard = ' <span class="warn">· sample too small</span>' if t.get("insufficient_sample") else ""
    stats = f"""
    <div class="panel"><h2>Performance{warn}</h2>
      <div class="kv"><span class="mut">Closed trades</span><span class="mono">{t['n_trades']}{guard}</span></div>
      <div class="kv"><span class="mut">Win rate</span><span class="mono">{(str(round(t['win_rate']*100,1))+'%  ('+str(t.get('n_wins',0))+'W/'+str(t.get('n_losses',0))+'L)') if t['win_rate'] is not None else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Profit factor</span><span class="mono">{('%.2f'%t['profit_factor']) if t['profit_factor'] else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Expectancy / trade</span><span>{color_num(t['expectancy'],'${:+,.2f}') if t['expectancy'] is not None else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Avg hold (days)</span><span class="mono">{('%.1f'%t['avg_hold_days']) if t.get('avg_hold_days') else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Sharpe</span><span>{ratio_str(m['sharpe'])}</span></div>
      <div class="kv"><span class="mut">Sortino</span><span>{ratio_str(m['sortino'])}</span></div>
      <div class="kv"><span class="mut">Calmar</span><span>{ratio_str(m['calmar'])}</span></div>
      <div class="kv"><span class="mut">Recovery factor</span><span class="mono">{('%.2f'%m['recovery_factor']) if m['recovery_factor'] else 'N/A'}</span></div></div>"""

    # ── Recent closed trades ──
    trades = analytics.load_closed_trades(BASE / "trades_closed.csv")
    trades.sort(key=lambda x: x.get("close_date", ""), reverse=True)
    trows = ""
    for tr in trades[:12]:
        trows += f"""<tr>
          <td>{html.escape(tr.get('close_date',''))}</td>
          <td>{html.escape(tr.get('symbol',''))} <span class="tag tag-{'L' if tr.get('direction')=='long' else 'S'}">{'L' if tr.get('direction')=='long' else 'S'}</span></td>
          <td class="mono">{tr.get('qty','')}</td>
          <td class="mono">{tr.get('entry_px','')}</td>
          <td class="mono">{tr.get('exit_px','')}</td>
          <td>{color_num(tr.get('gross_pnl'),'${:+,.2f}')}</td>
          <td>{color_num(tr.get('return_pct'),'{:+.2f}%')}</td>
          <td class="mono">{tr.get('hold_days','') if tr.get('hold_days') is not None else ''}</td></tr>"""
    if not trows:
        trows = '<tr><td colspan="8" class="mut">No closed trades yet.</td></tr>'
    ttable = f"""
    <div class="panel"><h2>Recent Closed Trades</h2>
      <table><thead><tr><th>Closed</th><th>Symbol</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&amp;L $</th><th>Return</th><th>Days</th></tr></thead>
      <tbody>{trows}</tbody></table></div>"""

    footer = (f'<div class="footer">Generated {now_et:%Y-%m-%d %H:%M:%S ET} · '
              f'orders last run: {hb.get("n_orders_submitted","?")} submitted / '
              f'{hb.get("n_orders_rejected","?")} rejected · '
              f'JP Alpha Strategy v3 (frozen) · monitoring layer read-only</div>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JP Alpha v3 — Command Center</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>{CSS}</style></head><body>
<h1>JP ALPHA STRATEGY v3 · COMMAND CENTER</h1>
{banner}{tiles}{chart}{postable}
<div class="grid2">{expo}{stats}</div>
{ttable}{footer}
<script>
const L={json.dumps(labels)},PV={json.dumps(pvs)},UW={json.dumps(uw)},SPY={json.dumps(spy_data)};
const g=(id)=>document.getElementById(id).getContext('2d');
const base={{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#8b949e',maxTicksLimit:8}},grid:{{color:'#21262d'}}}},y:{{ticks:{{color:'#8b949e'}},grid:{{color:'#21262d'}}}}}}}};
const eqDatasets=[{{label:'Strategy',data:PV,borderColor:'#388bfd',backgroundColor:'rgba(56,139,253,.08)',fill:true,tension:.2,pointRadius:L.length<40?2:0,borderWidth:2}}];
if(SPY){{eqDatasets.push({{label:'SPY buy & hold',data:SPY,borderColor:'#8b949e',borderDash:[5,4],fill:false,tension:.2,pointRadius:0,borderWidth:1.5}});}}
const eqOpts={{...base,plugins:{{legend:{{display:!!SPY,labels:{{color:'#8b949e',boxWidth:12,font:{{size:11}}}}}}}}}};
new Chart(g('eq'),{{type:'line',data:{{labels:L,datasets:eqDatasets}},options:eqOpts}});
new Chart(g('uw'),{{type:'line',data:{{labels:L,datasets:[{{data:UW,borderColor:'#f85149',backgroundColor:'rgba(248,81,73,.12)',fill:true,tension:.2,pointRadius:0,borderWidth:1}}]}},options:{{...base,scales:{{...base.scales,y:{{...base.scales.y,max:0}}}}}}}});
</script></body></html>"""


def main():
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")

    cfg = load_json(BASE / "config.json", {"starting_equity": 100000.0})
    start_eq = float(cfg.get("starting_equity", 100000.0))
    state = load_json(BASE / "state.json", {"positions": {}})
    hb = load_json(BASE / "heartbeat.json", {})

    acct, positions, clock = fetch_live()

    # Equity curve source: prefer Alpaca's full portfolio history (read-only) so
    # the chart AND drawdown/Sharpe reflect real multi-week history rather than
    # the thin locally-accrued CSV. Fall back to the CSV if the API is down or
    # returns fewer points than we already have locally.
    csv_equity = analytics.load_equity_curve(BASE / "equity_curve.csv")
    hist_equity = fetch_portfolio_history()
    equity = hist_equity if len(hist_equity) >= max(2, len(csv_equity)) else csv_equity

    m = analytics.compute_all(BASE / "equity_curve.csv", BASE / "trades_closed.csv",
                              acct, positions, start_eq, STOP_PCT,
                              equity_override=equity)
    enriched = enrich_positions(positions, state)

    # SPY buy-and-hold benchmark aligned to the equity curve (read-only overlay).
    spy = spy_benchmark(equity, fetch_spy_series(equity[0]["date"])) if equity else None

    html_out = render(m, acct, enriched, clock, hb, equity, spy)

    out = BASE / "dashboard.html"
    out.write_text(html_out)
    print(f"Dashboard written: {out} ({len(html_out):,} bytes)")


if __name__ == "__main__":
    main()
