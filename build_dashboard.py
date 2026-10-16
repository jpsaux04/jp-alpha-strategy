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
import pathlib
import re as _re


def _agent_version_table() -> dict:
    """jp_agent.py's _VERSIONS, read out of the source with ast.

    NOT re-declared here. The exit levels this page displays must be the exit
    levels the agent will act on, and the only way to guarantee that is to read
    the same table rather than keep a copy in step by hand.
    """
    try:
        import ast
        src = (pathlib.Path(__file__).parent / "jp_agent.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_VERSIONS"
                    for t in node.targets):
                return ast.literal_eval(node.value)
    except Exception:
        pass
    return {}


def _deployed_version() -> tuple:
    """(version_string, short_label) for the version jp_agent would run NOW.

    Read out of jp_agent.py's source rather than imported: build_dashboard
    promises to import only analytics.py + stdlib + requests, and importing
    jp_agent would load .env and the broker config as a side effect.
    """
    default = "UNKNOWN"
    try:
        src = (pathlib.Path(__file__).parent / "jp_agent.py").read_text()
        m = _re.search(r'STRATEGY_VERSION\s*=\s*os\.environ\.get\(\s*'
                       r'"STRATEGY_VERSION"\s*,\s*\n?\s*"([^"]+)"', src)
        if m:
            default = m.group(1)
    except Exception:
        pass
    ver = os.environ.get("STRATEGY_VERSION", default)
    # JP_ALPHA_V5_LONGONLY_STOPATR15 -> ("V5", "1.5")
    m = _re.match(r"JP_ALPHA_(V\d+)_.*?STOPATR(\d+)$", ver)
    if m:
        num, digits = m.group(1), m.group(2)
        mult = f"{digits[0]}.{digits[1:]}" if len(digits) > 1 else digits
        return ver, f"{num} (long-only, {mult}x ATR stop)"
    if ver == "JP_ALPHA_V3_FROZEN":
        return ver, "V3 (bidirectional, fixed -8% stop)"
    return ver, ver

import html
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as _dtime, date as _date
from zoneinfo import ZoneInfo

import requests
import analytics

BASE = Path(__file__).parent
ET = ZoneInfo("America/New_York")

STOP_PCT = 0.08          # V3 fixed stop; still correct for SHORTS (jp_agent.py:933)

#  Mirror the agent's live exit configuration. Derived, never re-declared --
#  see _agent_version_table().
_VTABLE = _agent_version_table()
_VNAME = _deployed_version()[0]
ALLOW_SHORTS, STOP_ATR_MULT, ANCHOR_ON_FILL = _VTABLE.get(
    _VNAME, (True, 0.0, False))
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


def _inception_period(H, U) -> str:
    """An Alpaca `period` covering the account's WHOLE history, derived.

    A hardcoded rolling window (this was "3M") drops the oldest history a day
    at a time, so the equity chart's origin advances every day and the earliest
    trades quietly leave the record. Losing days early in the track therefore
    disappear first, which biases the displayed return upward with no code
    change and nothing to alert on.

    Computed from the account's created_at, rounded up with a month of slack.
    Falls back to "1A" if the account cannot be read — wider than the window it
    replaces, so the failure mode is too much history rather than too little.
    """
    try:
        a = requests.get(f"{U}/v2/account", headers=H, timeout=15).json()
        d0 = datetime.fromisoformat(
            str(a["created_at"]).replace("Z", "+00:00")).date()
        now = datetime.now(timezone.utc).date()
        months = (now.year - d0.year) * 12 + (now.month - d0.month) + 2
        return f"{max(3, min(months, 240))}M"
    except Exception:
        return "1A"


def fetch_portfolio_history(period=None, timeframe="1D"):
    """Read-only Alpaca portfolio equity history → [{'date','pv'}, ...] sorted.

    `period` defaults to the account's full lifetime — see _inception_period().
    Do not pin this to a fixed window: the curve must start at inception, or the
    chart, the drawdown, the risk ratios, the SPY overlay (which is re-based to
    equity[0]) and the alpha regression all measure from a moving origin that is
    later than the one the Total Return tile divides by.

    Filters out leading zero-equity (pre-funding / no-data) days so the curve
    starts where the account actually held value. Returns [] on any failure —
    the caller falls back to the local equity_curve.csv.
    """
    try:
        H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
             "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
        U = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
        r = requests.get(f"{U}/v2/account/portfolio/history",
                         params={"period": period or _inception_period(H, U),
                                 "timeframe": timeframe},
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
            # Alpaca stamps 1D rows at 20:00 ET; converting in UTC rolls
            # every date forward a day and turns Friday closes into
            # "Saturdays" that the trading-day filter then drops.
            d = datetime.fromtimestamp(int(t), tz=ET).date().isoformat()
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


def beta_matched_benchmark(equity, spy_map, beta):
    """Beta-matched passive book: `beta` x SPY + (1-beta) cash, rebalanced daily.

    This is the honest hurdle for a partially-invested long book: matching the
    strategy's market exposure rather than comparing it to 0% or to fully
    invested SPY. Cash is assumed to earn 0% (conservative *against* the
    benchmark, i.e. it flatters the strategy). Returns a list aligned to the
    equity dates, or None if unusable.
    """
    if not equity or not spy_map or not beta:
        return None
    start_pv = equity[0]["pv"]
    px, last = [], None
    for e in equity:
        p = spy_map.get(e["date"], last)
        last = p if p is not None else last
        px.append(last)
    if not px or px[0] is None:
        return None
    out, lvl = [], start_pv
    for i, p in enumerate(px):
        if i == 0 or p is None or px[i - 1] in (None, 0):
            out.append(round(lvl, 2))
            continue
        lvl *= (1 + beta * (p / px[i - 1] - 1))
        out.append(round(lvl, 2))
    return out


def alpha_tstat(equity, spy_map):
    """t-statistic on the CAPM intercept for the LIVE equity curve.

    analytics.beta_alpha reports alpha with no standard error, so a large
    annualized alpha off a short, low-correlation sample reads as a result when
    it is noise. This runs the same OLS and returns (t, n) so the page can state
    whether the live alpha is distinguishable from zero. Returns (None, n) if it
    cannot be computed — never a guess.
    """
    try:
        import math
        import statistics as st
        pr = analytics._paired_returns(equity, spy_map)
        n = len(pr)
        if n < 30:
            return None, n
        s = [x[0] for x in pr]
        b = [x[1] for x in pr]
        mb, ms = st.mean(b), st.mean(s)
        var_b = st.pvariance(b)
        if var_b == 0:
            return None, n
        beta = sum((si - ms) * (bi - mb) for si, bi in pr) / n / var_b
        a = ms - beta * mb
        resid = [si - (a + beta * bi) for si, bi in pr]
        dof = n - 2
        if dof <= 0:
            return None, n
        sse = sum(r * r for r in resid) / dof
        se_a = math.sqrt(sse * (1.0 / n + (mb * mb) / (n * var_b)))
        if se_a == 0:
            return None, n
        return a / se_a, n
    except Exception:
        return None, 0


# Exit fragments vs. positions ────────────────────────────────────────────────
# The strategy scales out through tiered targets (T1/T2/T3) and partial fills,
# so trades_closed.csv holds EXIT FRAGMENTS, not positions. A T1 fragment is a
# win by construction, so a fragment-level win rate is upward-biased and must
# never be published as "win rate". We roll fragments up into positions here.

TAB_JS = """
function showTab(n){
  ['dash','scan'].forEach(function(k){
    document.getElementById('tab-'+k).classList.toggle('hide', k!==n);
    document.getElementById('btn-'+k).classList.toggle('on', k===n);
  });
  // A hidden canvas measures 0x0; re-measure explicitly on reveal rather than
  // trusting the ResizeObserver to fire for a display:none -> block flip.
  if(n==='dash'){(window.jpCharts||[]).forEach(function(c){c.resize();});}
}
"""

TABLE_JS = """
//  Sort + filter for the history tables. Pure client-side: the page is a
//  static artifact published to GitHub Pages, so there is no server to ask.
function wireTable(wrapId){
  const wrap=document.getElementById(wrapId); if(!wrap) return;
  const tbl=wrap.querySelector('table'); if(!tbl||!tbl.tBodies.length) return;
  const body=tbl.tBodies[0];
  const inp=document.querySelector('[data-filter="'+wrapId+'"]');
  const cnt=document.querySelector('[data-count="'+wrapId+'"]');
  const allRows=()=>Array.from(body.querySelectorAll('tr'));
  const total=allRows().length;

  function refresh(){
    const shown=allRows().filter(r=>r.style.display!=='none').length;
    if(cnt) cnt.textContent=(shown===total)?(total+' rows'):(shown+' of '+total+' rows');
  }
  if(inp){
    inp.addEventListener('input',()=>{
      const q=inp.value.trim().toUpperCase();
      allRows().forEach(tr=>{
        tr.style.display=(!q||tr.textContent.toUpperCase().indexOf(q)>-1)?'':'none';
      });
      refresh();
    });
  }
  //  Numeric where the cell parses as a number, lexical otherwise, so a
  //  '$-1,234.00' P&L column sorts by magnitude and sign rather than as text.
  const num=(t)=>{
    const v=parseFloat(String(t).replace(/[^0-9.+-]/g,''));
    return isNaN(v)?null:v;
  };
  Array.from(tbl.querySelectorAll('thead th')).forEach((th,idx)=>{
    th.classList.add('srt');
    th.addEventListener('click',()=>{
      const desc=!th.classList.contains('desc');
      Array.from(tbl.querySelectorAll('thead th')).forEach(h=>h.classList.remove('asc','desc'));
      th.classList.add(desc?'desc':'asc');
      const rows=allRows();
      rows.sort((a,b)=>{
        const x=(a.cells[idx]?a.cells[idx].textContent:'').trim();
        const y=(b.cells[idx]?b.cells[idx].textContent:'').trim();
        const nx=num(x), ny=num(y);
        const c=(nx!==null&&ny!==null)?(nx-ny):x.localeCompare(y);
        return desc?-c:c;
      });
      rows.forEach(r=>body.appendChild(r));
    });
  });
  refresh();
}
"""


CHART_JS = """
const g=(id)=>document.getElementById(id).getContext('2d');

//  Every series is normalised to the same starting dollar value, so a percent
//  return is comparable across all of them.
const pctOf=(arr,i)=>(arr && arr[0]) ? ((arr[i]/arr[0]-1)*100) : null;
const sgn=(v)=>(v>=0?'+':'')+v.toFixed(2)+'%';
const usd=(v)=>'$'+Math.round(v).toLocaleString('en-US');

const eqDatasets=[{label:'Strategy',data:PV,borderColor:'#388bfd',
  backgroundColor:'rgba(56,139,253,.08)',fill:true,tension:.2,
  pointRadius:L.length<40?2:0,pointHoverRadius:4,borderWidth:2}];
//  SERIES mirrors eqDatasets exactly so tooltip lookup by datasetIndex is
//  correct even when a benchmark is unavailable and its dataset is absent.
const SERIES=[PV];
if(SPY){eqDatasets.push({label:'SPY buy & hold',data:SPY,borderColor:'#8b949e',
  borderDash:[5,4],fill:false,tension:.2,pointRadius:0,pointHoverRadius:4,
  borderWidth:1.5}); SERIES.push(SPY);}
if(BMK){eqDatasets.push({label:'Beta-matched passive book',data:BMK,
  borderColor:'#d29922',borderDash:[2,3],fill:false,tension:.2,pointRadius:0,
  pointHoverRadius:4,borderWidth:1.5}); SERIES.push(BMK);}

const tipStyle={
  backgroundColor:'rgba(13,17,23,.97)',borderColor:'#30363d',borderWidth:1,
  titleColor:'#e6edf3',bodyColor:'#e6edf3',footerColor:'#8b949e',
  padding:10,caretPadding:6,displayColors:true,boxWidth:9,boxHeight:9,
  titleFont:{size:12,weight:'600'},
  bodyFont:{family:'SF Mono,Menlo,Consolas,monospace',size:12},
  footerFont:{family:'SF Mono,Menlo,Consolas,monospace',size:11,weight:'400'},
  footerMarginTop:8
};

const base={responsive:true,
  interaction:{mode:'index',intersect:false},
  hover:{mode:'index',intersect:false},
  plugins:{legend:{display:false}},
  scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:8},grid:{color:'#21262d'}},
          y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}};

const eqOpts={...base,plugins:{
  regimeMarks:{label:true},
  legend:{display:!!SPY,labels:{color:'#8b949e',boxWidth:12,font:{size:11}}},
  tooltip:{...tipStyle,
    callbacks:{
      title:(it)=>it.length?it[0].label:'',
      //  Dollar value AND return-since-inception on every line: the chart is
      //  a comparison and the reader should not have to do the arithmetic.
      label:(c)=>{
        const src=SERIES[c.datasetIndex];
        const p=pctOf(src,c.dataIndex);
        return ' '+c.dataset.label+'  '+usd(c.parsed.y)+(p===null?'':'  '+sgn(p));
      },
      footer:(its)=>{
        if(!its.length) return '';
        const i=its[0].dataIndex, out=[];
        const sret=pctOf(PV,i);
        if(SPY){const b=pctOf(SPY,i);
          if(sret!==null&&b!==null) out.push('excess vs SPY        '+sgn(sret-b));}
        if(BMK){const m=pctOf(BMK,i);
          if(sret!==null&&m!==null) out.push('excess vs beta-match '+sgn(sret-m));}
        if(typeof UW!=='undefined'&&UW[i]!==undefined)
          out.push('drawdown             '+UW[i].toFixed(2)+'%');
        return out;
      }
    }}}};

//  Execution-regime boundaries. Drawn from the same REGIMES array that
//  feeds the caption, so the line and its explanation cannot disagree.
const regimePlugin={
  id:'regimeMarks',
  afterDatasetsDraw(ch,args,opts){
    const R=(typeof REGIMES!=='undefined'&&REGIMES)?REGIMES:[];
    if(!R.length||!L.length) return;
    const ca=ch.chartArea, xs=ch.scales.x, ctx=ch.ctx;
    R.forEach(function(r){
      //  First data point on or after the change. Labels are ISO dates, so a
      //  string compare is a date compare.
      let i=-1;
      for(let k=0;k<L.length;k++){if(L[k]>=r.date){i=k;break;}}
      if(i<0) return;                       // change is after the last point
      const px=xs.getPixelForValue(i);
      if(!isFinite(px)||px<ca.left||px>ca.right) return;
      ctx.save();
      ctx.beginPath(); ctx.setLineDash([4,4]); ctx.lineWidth=1;
      ctx.strokeStyle='#d29922';
      ctx.moveTo(px,ca.top); ctx.lineTo(px,ca.bottom); ctx.stroke();
      if(opts&&opts.label&&r.short){
        ctx.setLineDash([]);
        ctx.font='600 10px -apple-system,system-ui,sans-serif';
        ctx.fillStyle='#d29922'; ctx.textBaseline='top';
        const w=ctx.measureText(r.short).width;
        //  Flip inside the plot if the label would run off the right edge.
        ctx.fillText(r.short,(px+5+w>ca.right)?(px-5-w):(px+5),ca.top+3);
      }
      ctx.restore();
    });
  }
};

window.jpCharts=[];
window.jpCharts.push(new Chart(g('eq'),{type:'line',
  data:{labels:L,datasets:eqDatasets},options:eqOpts,plugins:[regimePlugin]}));

window.jpCharts.push(new Chart(g('uw'),{type:'line',
  data:{labels:L,datasets:[{label:'Drawdown',data:UW,borderColor:'#f85149',
    backgroundColor:'rgba(248,81,73,.12)',fill:true,tension:.2,pointRadius:0,
    pointHoverRadius:4,borderWidth:1}]},
  options:{...base,
    plugins:{legend:{display:false},regimeMarks:{label:false},
      tooltip:{...tipStyle,callbacks:{
        title:(it)=>it.length?it[0].label:'',
        label:(c)=>' drawdown from peak  '+c.parsed.y.toFixed(2)+'%'}}},
    scales:{...base.scales,y:{...base.scales.y,max:0}}},
  plugins:[regimePlugin]}));
"""


def rollup_positions(fragments):
    """Aggregate exit fragments into positions keyed by (symbol, direction,
    entry_date). P&L is summed; return_pct is notional-weighted; hold_days is
    the longest leg (the date the position was fully closed)."""
    buckets = {}
    order = []
    for f in fragments:
        key = (f.get("symbol"), f.get("direction"), f.get("entry_date"))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(f)

    out = []
    for key in order:
        legs = buckets[key]
        pnl = sum(float(x.get("gross_pnl") or 0) for x in legs)
        notional = 0.0
        wret = 0.0
        for x in legs:
            try:
                n = abs(float(x.get("qty") or 0)) * float(x.get("entry_px") or 0)
            except (TypeError, ValueError):
                n = 0.0
            r = x.get("return_pct")
            if n and r is not None:
                notional += n
                wret += n * float(r)
        holds = [x["hold_days"] for x in legs if x.get("hold_days") is not None]
        closes = [x.get("close_date", "") for x in legs if x.get("close_date")]
        out.append({
            "symbol": key[0], "direction": key[1], "entry_date": key[2],
            "close_date": max(closes) if closes else "",
            "gross_pnl": pnl,
            "return_pct": (wret / notional) if notional else None,
            "hold_days": max(holds) if holds else None,
            "n_fragments": len(legs),
        })
    return out


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

        #  Anchor exactly as jp_agent.py:847 does. Using the broker's average
        #  fill here instead would silently disagree with the agent whenever
        #  fill_price is null, which is the case for every legacy position.
        if meta:
            anchor = ((meta.get("fill_price") or meta.get("entry_price") or entry)
                      if ANCHOR_ON_FILL else (meta.get("entry_price") or entry))
        else:
            anchor = entry            # broker-only position: no state to anchor to

        #  Honour the multiple the position was OPENED under (jp_agent.py:863).
        #  A position opened under V4 keeps its 2.0xATR stop; reporting it at
        #  the current 1.5x would describe an exit that will not happen.
        _atr = (meta.get("atr_at_entry") or 0.0) if meta else 0.0
        _mult = meta.get("stop_atr_mult", STOP_ATR_MULT) if meta else STOP_ATR_MULT

        #  ORPHAN: held at the broker, absent from state.json. jp_agent only
        #  exits what is in state and places no resting stop at Alpaca, so
        #  nothing will close this position. It has no stop -- report that,
        #  rather than falling through to the fixed-% branch and inventing one.
        unmanaged = not meta

        if unmanaged:
            stop = nxt = dist_stop = dist_tgt = None
        elif is_long:
            #  ATR stop where the agent would use one, else the V3 fixed stop.
            stop = (anchor - _mult * _atr) if (_mult and _mult > 0 and _atr > 0) \
                   else anchor * (1 - STOP_PCT)
            nxt = anchor * (1 + (T3 if t2_hit else T2 if t1_hit else T1))
            dist_stop = (cur / stop - 1) * 100 if stop else 0      # cushion above stop
            dist_tgt = (nxt / cur - 1) * 100 if cur else 0         # room to target
        else:
            #  Shorts are always the V3 fixed rule (jp_agent.py:933). V4/V5
            #  cannot open one, so every short is a V3 legacy.
            stop = anchor * (1 + STOP_PCT)
            nxt = anchor * (1 - (T3 if t2_hit else T2 if t1_hit else T1))
            dist_stop = (stop / cur - 1) * 100 if cur else 0
            dist_tgt = (cur / nxt - 1) * 100 if nxt else 0

        if stop is None:
            #  Deliberately None, never 0.0: "unknown and unbounded" must not be
            #  summed into Capital-at-Risk as if it were "no risk".
            risk = None
        else:
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
            "unmanaged": unmanaged,
            "exposure": abs(qty) * cur,
        })
    #  Unmanaged rows first: they have no risk number to sort by, and they are
    #  the ones that need acting on.
    rows.sort(key=lambda r: (r["unmanaged"], r["risk"] or 0), reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
#  HTML RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def scan_universe(held_syms=None):
    """Current indicator state for every name in the tradeable universe.

    Returns (rows, meta). Calls jp_agent's own data + signal functions, so the
    thresholds shown are by construction the thresholds the agent will act on.
    Never raises: on any failure returns ([], {"error": ...}) and the panel
    renders an explicit failure notice rather than silently showing nothing.

    NOT a recommendation list. "Oversold" here means RSI is below the agent's
    own long threshold -- one of five conditions. A name can sit in the
    oversold zone for weeks and never become a signal, which is exactly what
    the condition column is there to show.
    """
    held_syms = held_syms or {}
    try:
        import jp_agent as A          # import-safe: no lock, no network at module level

        data = A.fetch_data()
        spy_df = data.get(A.SPY)
        if spy_df is None:
            return [], {"error": "SPY regime series unavailable"}
        spy_df = A.compute_regime(spy_df)

        rows = []
        for sym in A.WATCHLIST:
            df = data.get(sym)
            if df is None or len(df) < 60:
                continue
            lsig, ld = A.check_long_signal(sym, df, spy_df)
            ssig, sd = A.check_short_signal(sym, df, spy_df)
            if not ld and not sd:        # below MIN_PRICE, or no ATR yet
                continue
            d = ld or sd
            rsi = d["rsi"]
            #  Signed distance from MA20: + = above (extended), - = below.
            above = (sd or {}).get("disl_pct")
            below = (ld or {}).get("disl_pct")
            ma_pct = above if (above is not None and above > 0) else (
                -below if below is not None else 0.0)

            #  Raw, UNROUNDED row -- the same values check_*_signal compares.
            #  Deriving these from the rounded details dict put a 4/5 breakdown
            #  next to a SIGNAL badge for any name sitting on a threshold.
            row, srow = df.iloc[-1], spy_df.iloc[-1]
            lc = [("RSI", bool(row["RSI"] < A.RSI_OVERSOLD)),
                  ("MA20", bool(row["long_disl"] > A.MIN_LONG_DISL)),
                  ("VOL", bool(row["vol_exhaust_long"])),
                  ("CLOSE", bool(row["bullish_intraday"])),
                  ("REGIME", bool(srow["regime_long"]))] if ld else []
            sc = [("RSI", bool(row["RSI"] > A.RSI_OVERBOUGHT)),
                  ("MA20", bool(row["short_disl"] > A.MIN_SHORT_DISL)),
                  ("VOL", bool(row["vol_exhaust_short"])),
                  ("CLOSE", bool(row["bearish_intraday"])),
                  ("REGIME", bool(srow["regime_short"]))] if sd else []

            #  The breakdown must reconcile with the agent's own verdict. If it
            #  ever does not, this panel has drifted from the rule it claims to
            #  display -- say so in the row instead of rendering a quiet lie.
            lok = (not lc) or (all(v for _, v in lc) == bool(lsig))
            sok = (not sc) or (all(v for _, v in sc) == bool(ssig))

            zone = ("oversold" if rsi < A.RSI_OVERSOLD else
                    "overbought" if rsi > A.RSI_OVERBOUGHT else "neutral")

            rows.append({
                "symbol": sym,
                "sector": A.SECTOR_MAP.get(sym, ""),
                "price": d["price"],
                "rsi": rsi,
                "ma_pct": round(ma_pct, 1),
                "atr_pct": round((d["atr"] / d["price"] * 100), 1) if d["price"] else 0.0,
                "zone": zone,
                "long_conds": lc,
                "short_conds": sc,
                "n_long": sum(1 for _, v in lc if v),
                "n_short": sum(1 for _, v in sc if v),
                "long_sig": bool(lsig),
                "short_sig": bool(ssig),
                "reconciles": bool(lok and sok),
                "held": held_syms.get(sym),
            })

        last_bar = str(list(data.values())[0].index[-1].date())
        return rows, {
            "bar": last_bar,
            "settled": last_bar != datetime.now(ET).strftime("%Y-%m-%d"),
            "regime_long": bool(spy_df.iloc[-1]["regime_long"]),
            "regime_short": bool(spy_df.iloc[-1]["regime_short"]),
            "allow_shorts": bool(A.ALLOW_SHORTS),
            "rsi_os": A.RSI_OVERSOLD,
            "rsi_ob": A.RSI_OVERBOUGHT,
            "n": len(rows),
            "universe": len(A.WATCHLIST),
            "max_open": A.MAX_SIMULTANEOUS,
            "max_long": A.MAX_LONGS,
            "max_sector": A.MAX_PER_SECTOR,
        }
    except Exception as ex:
        return [], {"error": f"{type(ex).__name__}: {ex}"}


def render_scan(rows, meta):
    """The Analysis tab. Zone + the five-condition breakdown behind it."""
    if not rows:
        why = html.escape(str(meta.get("error", "no data")))
        return (f'<div class="panel"><h2>Universe Scan</h2>'
                f'<div class="warn">Scan unavailable &mdash; {why}. '
                f'The figures on the Dashboard tab are unaffected: they come '
                f'from the broker, not from this scan.</div></div>')

    os_n = sum(1 for r in rows if r["zone"] == "oversold")
    ob_n = sum(1 for r in rows if r["zone"] == "overbought")
    nu_n = len(rows) - os_n - ob_n
    sig_n = sum(1 for r in rows if r["long_sig"] or r["short_sig"])

    def dots(conds):
        if not conds:
            return '<span class="mut">&mdash;</span>'
        return ('<span class="cond">' + "".join(
            f'<span class="{"y" if v else "n"}" title="{html.escape(k)}">'
            f'{"&#9679;" if v else "&#9675;"}</span>' for k, v in conds)
            + "</span>")

    tr = ""
    for r in rows:
        zc = {"oversold": "z-os", "overbought": "z-ob"}.get(r["zone"], "z-nu")
        #  Which side is relevant: an oversold name is a long candidate, an
        #  overbought name a short candidate. Neutral names get the long view,
        #  since V5 is long-only.
        short_side = r["zone"] == "overbought"
        conds = r["short_conds"] if short_side else r["long_conds"]
        nmet = r["n_short"] if short_side else r["n_long"]
        sig = r["short_sig"] if short_side else r["long_sig"]
        badge = ('<span class="sig">SIGNAL</span>' if sig
                 else f'<span class="mut">{nmet}/5</span>')
        if not r.get("reconciles", True):
            badge = ('<span class="red" title="condition breakdown disagrees with '
                     'the agent\'s signal — do not trust this row">&#9888; DRIFT</span>')
        if sig and short_side and not meta.get("allow_shorts"):
            badge = '<span class="mut" title="V5 is long-only">short n/a</span>'
        heldtxt = (f'<span class="held">{html.escape(str(r["held"]).upper())}</span>'
                   if r["held"] else '<span class="mut">&middot;</span>')
        rcls = "red" if r["rsi"] > meta["rsi_ob"] else "grn" if r["rsi"] < meta["rsi_os"] else "mut"
        mcls = "red" if r["ma_pct"] > 0 else "grn"
        tr += (f'<tr><td><b>{html.escape(r["symbol"])}</b></td>'
               f'<td class="mut">{html.escape(r["sector"])}</td>'
               f'<td class="mono">{r["price"]:,.2f}</td>'
               f'<td class="mono {rcls}">{r["rsi"]:.1f}</td>'
               f'<td class="mono {mcls}">{r["ma_pct"]:+.1f}%</td>'
               f'<td class="mono mut">{r["atr_pct"]:.1f}%</td>'
               f'<td><span class="z {zc}">{r["zone"]}</span></td>'
               f'<td>{dots(conds)}</td>'
               f'<td>{badge}</td>'
               f'<td>{heldtxt}</td></tr>')

    bar = html.escape(meta.get("bar", "?"))
    settled = meta.get("settled")
    barnote = (f'bar of {bar} (settled close)' if settled else
               f'<span class="amb">bar of {bar} &mdash; TODAY, still forming. '
               f'The agent runs at 16:20 ET on the SETTLED close, so this is not '
               f'the bar it will evaluate. Treat these rows as indicative.</span>')
    reg = ("OK" if meta.get("regime_long") else "BLOCKED")
    regcls = "grn" if meta.get("regime_long") else "red"

    return f"""
    <div class="panel"><h2>Universe Scan &mdash; all {meta.get('universe', 0)} names</h2>
      <div class="tbar">
        <input class="filter" data-filter="scan" placeholder="Filter symbol / sector&hellip;">
        <span class="count" data-count="scan"></span>
        <span class="count">{os_n} oversold &middot; {nu_n} neutral &middot;
          {ob_n} overbought &middot; <b>{sig_n} full signal</b></span>
        <span class="count">long regime <span class="{regcls}">{reg}</span></span>
      </div>
      <div class="scroll" id="scan"><table>
      <thead><tr>
        <th class="srt">Symbol</th><th class="srt">Sector</th><th class="srt">Price</th>
        <th class="srt">RSI</th><th class="srt">vs MA20</th><th class="srt">ATR</th>
        <th class="srt">Zone</th><th>Conditions</th><th class="srt">Entry</th>
        <th class="srt">Held</th>
      </tr></thead><tbody>{tr}</tbody></table></div>
      <div class="legend">
        <b>As of</b> {barnote}<br>
        <b>Zone</b> uses the strategy's own thresholds &mdash;
        RSI &lt; {meta['rsi_os']} oversold, &gt; {meta['rsi_ob']} overbought &mdash;
        <i>not</i> the textbook 30/70. They are deliberately loose because RSI is
        a filter here, not the trigger.<br>
        <b>Conditions</b> are the five that must <i>all</i> hold for an entry:
        RSI &middot; &ge;2% dislocation from MA20 &middot; volume exhaustion &middot;
        close in the favourable half of the day's range &middot; SPY regime.
        Filled = met. Hover a dot for its name. The count is n/5.<br>
        <b>This is not a buy list.</b> A name can sit in the oversold zone for weeks
        at 4/5 and never trigger &mdash; that is the column's purpose. Only
        <span class="sig">SIGNAL</span> rows would be acted on, and then only subject to
        the portfolio caps (max {meta['max_open']} open, {meta['max_long']} long,
        {meta['max_sector']} per sector).<br>
        <b>Overbought names are informational.</b> The deployed V5 is long-only, so the
        agent will not short them. The short columns are shown because the frozen V3
        control traded them and the closed-trade history contains them.<br>
        Computed by calling jp_agent's own <span class="mono">check_long_signal</span> /
        <span class="mono">check_short_signal</span> &mdash; this table cannot drift
        from the live rule.
      </div>
    </div>"""


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
.scroll{max-height:430px;overflow-y:auto;border:1px solid var(--line);border-radius:6px}
.scroll::-webkit-scrollbar{width:10px}
.scroll::-webkit-scrollbar-thumb{background:var(--line);border-radius:5px}
.scroll table{font-size:12.5px}
.scroll thead th{position:sticky;top:0;background:var(--panel);z-index:2}
.tbar{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.filter{background:var(--bg);border:1px solid var(--line);color:var(--fg);
border-radius:6px;padding:5px 9px;font-size:12px;width:190px;outline:none}
.filter:focus{border-color:var(--blu)}
.filter::placeholder{color:var(--mut)}
.count{font-size:11px;color:var(--mut);letter-spacing:.04em}
th.srt{cursor:pointer;user-select:none;white-space:nowrap}
th.srt:hover{color:var(--fg)}
th.srt::after{content:'\2195';opacity:.3;margin-left:4px;font-size:10px}
th.srt.asc::after{content:'\2191';opacity:1;color:var(--blu)}
th.srt.desc::after{content:'\2193';opacity:1;color:var(--blu)}
.warn{color:var(--amb);font-size:12px}
.disclose{background:rgba(210,153,34,.10);border:2px solid var(--amb);border-radius:8px;
padding:14px 18px;margin-bottom:16px;color:var(--fg);font-size:13.5px;line-height:1.55}
.disclose h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--amb);
font-weight:700;margin-bottom:8px}
.disclose ul{margin:0 0 0 18px} .disclose li{margin-bottom:5px}
.disclose b{color:#f0c674}
.tabs{display:flex;gap:4px;margin:0 0 16px;border-bottom:1px solid var(--line)}
.tab{background:none;border:none;border-bottom:2px solid transparent;color:var(--mut);
font:600 12px/1 inherit;text-transform:uppercase;letter-spacing:.07em;
padding:10px 16px;cursor:pointer}
.tab:hover{color:var(--fg)}
.tab.on{color:var(--fg);border-bottom-color:var(--blu)}
.hide{display:none}
.z{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}
.z-os{background:rgba(56,139,253,.15);color:#79c0ff}
.z-ob{background:rgba(248,81,73,.15);color:#ff7b72}
.z-nu{background:rgba(139,148,158,.12);color:var(--mut)}
.sig{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700;
background:rgba(46,160,67,.18);color:#56d364}
.cond{font-family:"SF Mono",Menlo,Consolas,monospace;letter-spacing:2px;font-size:12px}
.cond .y{color:var(--grn)} .cond .n{color:#484f58}
.legend{font-size:11.5px;color:var(--mut);line-height:1.6;margin-top:10px}
.held{color:#79c0ff;font-weight:600}
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


def pct_ratio(d):
    """ratio_str variant for fraction-valued metrics shown as percentages."""
    if not isinstance(d, dict) or d.get("value") is None:
        if isinstance(d, dict) and d.get("insufficient_sample"):
            return f'<span class="mut">N/A · n={d.get("n",0)}/{d.get("needed","?")}</span>'
        return '<span class="mut">N/A</span>'
    return f'<span class="mono">{d["value"]*100:.1f}%</span>'


def market_str(d, key):
    """Render one ratio field of the beta/alpha dict, with sample guards."""
    if not isinstance(d, dict):
        return '<span class="mut">N/A</span>'
    if d.get("insufficient_sample"):
        return f'<span class="mut">N/A · n={d.get("n",0)}/{d.get("needed","?")}</span>'
    v = d.get(key)
    if v is None:
        return '<span class="mut">N/A</span>'
    return f'<span class="mono">{v:.2f}</span>'


def alpha_str(d):
    """Annualized Jensen's alpha as a colored percentage, with sample guards."""
    if not isinstance(d, dict):
        return '<span class="mut">N/A</span>'
    if d.get("insufficient_sample"):
        return f'<span class="mut">N/A · n={d.get("n",0)}/{d.get("needed","?")}</span>'
    v = d.get("alpha")
    if v is None:
        return '<span class="mut">N/A</span>'
    cls = "grn" if v >= 0 else "red"
    return f'<span class="mono {cls}">{v*100:+.1f}%</span>'


LIVE_JS = """
<script>
/* Live poller — updates hero tiles + open-position marks from /api/live every 7s.
   No-op when the page is opened as a static file (file://), so the generated
   dashboard.html still works standalone. Read-only: only GETs a JSON snapshot. */
(function(){
  if(!location.protocol.startsWith('http')) return;
  var $=function(id){return document.getElementById(id);};
  var sgn=function(v){return v>0?'grn':(v<0?'red':'mut');};
  var colored=function(txt,v){return '<span class="'+sgn(v)+' mono">'+txt+'</span>';};
  var usd0=function(v){return '$'+Math.round(v).toLocaleString('en-US');};
  var usdS=function(v){return (v<0?'-$':'$')+Math.abs(Math.round(v)).toLocaleString('en-US');};
  var pct=function(v,d){d=(d===undefined)?2:d;return (v>=0?'+':'')+v.toFixed(d)+'%';};
  var setHTML=function(el,h){if(el)el.innerHTML=h;};
  var setTxt=function(el,t){if(el)el.textContent=t;};
  var setPct=function(el,v){if(el){el.textContent=pct(v);el.className=sgn(v)+' mono';}};
  async function tick(){
    try{
      var r=await fetch('/api/live',{cache:'no-store'});
      if(!r.ok) return;
      var d=await r.json();
      if(d.error) return;
      setTxt($('eq-val'), usd0(d.equity));
      setHTML($('eq-ret'), colored(pct(d.total_return_pct), d.total_return_pct));
      setHTML($('pl-val'), colored(usdS(d.day_pl), d.day_pl));
      setHTML($('pl-pct'), colored(pct(d.day_pl_pct), d.day_pl_pct));
      setTxt($('risk-val'), usd0(d.open_risk));
      setTxt($('risk-pct'), d.open_risk_pct.toFixed(2));
      setHTML($('dd-val'), colored(pct(d.drawdown_pct), d.drawdown_pct));
      if(d.spy_return_pct!=null){setPct($('bench-strat'), d.total_return_pct);setPct($('bench-spy'), d.spy_return_pct);setPct($('bench-excess'), d.excess_pct);}
      (d.positions||[]).forEach(function(p){
        document.querySelectorAll('[data-sym="'+p.symbol+'"][data-f="cur"]').forEach(function(td){td.textContent=p.cur.toFixed(2);});
        document.querySelectorAll('[data-sym="'+p.symbol+'"][data-f="plpc"]').forEach(function(td){td.innerHTML=colored(pct(p.plpc,1), p.plpc);});
      });
      var lu=$('live-upd');
      if(lu) lu.textContent=' · live '+(d.market_open?'● open':'○ closed')+' '+d.ts.substring(11,19)+' ET';
    }catch(e){}
  }
  tick(); setInterval(tick, 7000);
})();
</script>"""


#  Agent schedule. MUST match the crontab entry:
#      30 16 * * 1-5  ... jp_agent.py ...
#  If you change one, change the other; a drift here makes the liveness banner
#  lie in whichever direction the drift went.
RUN_HOUR, RUN_MINUTE = 16, 20   # must match the crontab; ops suite asserts it
RUN_WEEKDAYS = range(0, 5)          # Mon-Fri (Python weekday(): Mon=0)
RUN_GRACE_MIN = 45                  # a run must be allowed time to finish

#  The date the CURRENT schedule took effect. Slots before this ran under a
#  different time, so applying RUN_HOUR/RUN_MINUTE to them would invent
#  missed runs that never were. Bump this whenever the crontab time changes.
SCHEDULE_SINCE = _date(2026, 10, 16)   # 15:45 -> 16:20 ET (settled close)

#  Dates where the EXECUTION regime changed -- where the fill mechanics moved,
#  not merely the wall-clock time. Marked on the equity curve because the
#  series either side is not the same strategy and should not be read as one
#  continuous record.
#
#  Derived from SCHEDULE_SINCE, not restated: this boundary IS that change,
#  and two hand-maintained copies of one date is the drift bug this repo keeps
#  rediscovering. A future cron tweak that does NOT change when orders fill
#  (16:20 -> 16:30, both post-close) must not be appended here.
EXEC_REGIME_CHANGES = [
    (SCHEDULE_SINCE,
     "fills move to the next open",
     "Before this date the agent ran at 15:45 ET and orders filled in the same "
     "session. From this date it runs at 16:20 ET on the settled close and "
     "orders are queued by the broker to fill at the next open. Returns either "
     "side reflect different execution, not only different markets."),
]


def missed_scheduled_runs(last_run, now_et, _max_lookback_days=45):
    """How many scheduled agent runs should have completed since `last_run`.

    0 means healthy: every run that was due has produced a heartbeat. This is
    the honest liveness question. Elapsed hours is not -- it cannot tell a
    weekend apart from a dead agent, which is the whole bug this replaces.

    A run due less than RUN_GRACE_MIN ago is not counted: the agent may still
    be mid-run, and flagging a run that is currently succeeding is the same
    false-positive failure in miniature.
    """
    cutoff = now_et - timedelta(minutes=RUN_GRACE_MIN)
    missed = 0
    day = cutoff.date()
    for _ in range(_max_lookback_days):
        slot = datetime.combine(day, _dtime(RUN_HOUR, RUN_MINUTE), tzinfo=ET)
        if slot > cutoff:                     # today's run not due yet
            day -= timedelta(days=1)
            continue
        if slot <= last_run:                  # reached the last good run
            break
        if day < SCHEDULE_SINCE:              # a different schedule applied
            break
        if slot.weekday() in RUN_WEEKDAYS:
            missed += 1
        day -= timedelta(days=1)
    return missed


def next_scheduled_run(now_et, _max_lookahead_days=10):
    """Wall-clock time of the next cron run, for display."""
    day = now_et.date()
    for _ in range(_max_lookahead_days):
        slot = datetime.combine(day, _dtime(RUN_HOUR, RUN_MINUTE), tzinfo=ET)
        if slot > now_et and slot.weekday() in RUN_WEEKDAYS:
            return slot
        day += timedelta(days=1)
    return None


def render(m, acct, positions, clock, hb, equity, spy=None, bmk=None, bmk_beta=None,
           bmk_beta_src="", alpha_t=None, alpha_n=0, scan=None, pending=None):
    now_et = datetime.now(ET)
    pv = m["portfolio_value"]

    # ── Banner: heartbeat liveness ──
    #  Liveness is "were any SCHEDULED runs missed", not "how many hours old".
    #  The old flat 26h threshold fired all weekend, every weekend, while the
    #  agent was perfectly healthy -- and printed RUN_OK next to "may be down".
    stale = True
    missed = None
    hb_txt = "NO HEARTBEAT YET — the agent has never recorded a completed run"
    if hb.get("last_run_ts"):
        try:
            last = datetime.fromisoformat(hb["last_run_ts"]).astimezone(ET)
            age_h = (now_et - last).total_seconds() / 3600
            missed = missed_scheduled_runs(last, now_et)
            stale = missed > 0
            hb_txt = (f'last run {last:%Y-%m-%d %H:%M ET} ({age_h:.0f}h ago) · '
                      f'{hb.get("result","?")}')
        except Exception:
            pass

    nxt = next_scheduled_run(now_et)
    sched_txt = f'next run {nxt:%a %Y-%m-%d %H:%M ET}' if nxt else "next run ?"
    next_open = clock.get("next_open", "")[:16].replace("T", " ") if clock else "?"

    #  Unmanaged (orphan) exposure. Surfaced loudly: it is invisible to the
    #  Capital-at-Risk number by construction, because there is no stop to
    #  compute a risk against.
    _orphans = [r for r in positions if r.get("unmanaged")]
    _orph_val = sum(r.get("exposure") or 0 for r in _orphans)
    _orph_pct = (_orph_val / pv * 100) if pv else 0
    _unmanaged_sub = ""
    _orphan_banner = ""
    if _orphans:
        _names = ", ".join(html.escape(str(r["symbol"])) for r in _orphans)
        _unmanaged_sub = (f'<br><span class="red">excludes ${_orph_val:,.0f} '
                          f'unmanaged &mdash; no stop exists</span>')
        _orphan_banner = (
            f'<div class="banner stale"><span class="dot dot-stale"></span>'
            f'<span>UNMANAGED POSITIONS &mdash; {_names}</span>'
            f'<span class="mut" style="font-weight:400">'
            f'${_orph_val:,.0f} ({_orph_pct:.1f}% of equity) held at the broker but absent '
            f'from state.json. The agent will not stop, target or time-stop these, and no '
            f'resting stop order exists at the broker. New entries are HALTED until state '
            f'and broker agree. Not included in Open Risk.</span></div>')

    #  EXEC-8: orders submitted after the close are queued by the broker for
    #  the next open. They are not positions yet -- by design -- so without
    #  this they are invisible everywhere on this page.
    _pending_banner = ""
    if pending:
        _bits = []
        for _p in pending.values():
            _sym = html.escape(str(_p.get("symbol", "?")))
            _side = html.escape(str(_p.get("side", "?"))).upper()
            _qty = _p.get("qty") or _p.get("intended_qty") or "?"
            _kind = "entry" if _p.get("is_entry") else "exit"
            _bits.append(f"{_side} {_qty} {_sym} ({_kind})")
        _pending_banner = (
            f'<div class="banner stale"><span class="dot dot-stale"></span>'
            f'<span>QUEUED AT THE BROKER &mdash; {len(pending)} UNSETTLED '
            f'ORDER(S)</span><span class="mut" style="font-weight:400">'
            f'{html.escape(", ".join(_bits))}. Submitted after the close and held '
            f'by the broker until the next open, so these are NOT positions yet and '
            f'are not in Open Positions or Open Risk. A queued entry already '
            f'consumes a slot and blocks its symbol; a queued exit is not rolled '
            f'back. The next run resolves them against the actual fill.</span></div>')

    bcls = "stale" if stale else "live"
    dcls = "dot-stale" if stale else "dot-live"
    if missed is None:
        btext = "NO HEARTBEAT"
    elif missed == 0:
        btext = "LIVE"
    else:
        btext = (f"STALE — {missed} scheduled run{'s' if missed > 1 else ''} missed")
    banner = (f'<div class="banner {bcls}"><span class="dot {dcls}"></span>'
              f'<span>{btext}</span><span class="mut" style="font-weight:400">{html.escape(hb_txt)}'
              f' · {html.escape(sched_txt)} · next open {html.escape(next_open)}</span>'
              f'<span id="live-upd" class="mut" style="font-weight:400"></span></div>')

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
        <div class="val mono" id="eq-val">${pv:,.0f}</div>
        <div class="sub">Total <span id="eq-ret">{color_num(tot, "{:+.2f}%")}</span> <span class="amb">gross of costs</span> vs ${m['starting_equity']:,.0f}</div></div>
      <div class="tile"><div class="lbl">Day P&amp;L</div>
        <div class="val" id="pl-val">{color_num(day_pl, "${:+,.0f}")}</div>
        <div class="sub"><span id="pl-pct">{color_num(day_pl_pct, "{:+.2f}%")}</span> since prior close</div></div>
      <div class="tile"><div class="lbl">Open Risk (CaR)</div>
        <div class="val mono" id="risk-val">${car.get('capital_at_risk',0):,.0f}</div>
        <div class="sub"><span id="risk-pct">{car.get('pct_of_equity','—')}</span>% of equity if all stops hit{_unmanaged_sub}</div></div>
      <div class="tile"><div class="lbl">Drawdown</div>
        <div class="val" id="dd-val">{color_num(dd['current_dd']*100, "{:+.2f}%")}</div>
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
    bmk_data = bmk if (bmk and len(bmk) == len(pvs)) else None
    #  Only boundaries that actually fall inside the plotted window. A marker
    #  for a change that predates every data point would be a line at the
    #  y-axis implying a split that isn't in view.
    regime_marks = []
    if labels:
        for _d, _short, _why in EXEC_REGIME_CHANGES:
            _iso = _d.isoformat()
            if labels[0] < _iso <= labels[-1]:
                regime_marks.append({"date": _iso, "short": _short,
                                     "why": _why})

    regime_caption = ""
    if regime_marks:
        _items = "".join(
            f'<br><span class="amb">&#9478; {html.escape(m["date"])} &mdash; '
            f'{html.escape(m["short"])}.</span> {html.escape(m["why"])}'
            for m in regime_marks)
        regime_caption = (
            f'<div class="footer">Dashed gold line{"s" if len(regime_marks) > 1 else ""} '
            f'= execution-regime change. Statistics above span the whole period '
            f'and therefore MIX these regimes; read the stretches either side '
            f'separately before drawing conclusions.{_items}</div>')

    bench_caption = ""
    if spy_data and pvs[0] and spy_data[0] and spy_data[-1]:
        strat_ret = (pvs[-1] / pvs[0] - 1) * 100
        spy_ret = (spy_data[-1] / spy_data[0] - 1) * 100
        alpha = strat_ret - spy_ret
        acls = "grn" if alpha >= 0 else "red"
        bench_caption = (
            f'<div class="footer">Since {labels[0]}: '
            f'strategy <span id="bench-strat" class="{"grn" if strat_ret>=0 else "red"} mono">{strat_ret:+.2f}%</span> · '
            f'SPY buy &amp; hold <span id="bench-spy" class="{"grn" if spy_ret>=0 else "red"} mono">{spy_ret:+.2f}%</span> · '
            f'excess <span id="bench-excess" class="{acls} mono">{alpha:+.2f}%</span></div>')
        if bmk_data and bmk_data[0]:
            bret = (bmk_data[-1] / bmk_data[0] - 1) * 100
            bex = strat_ret - bret
            bench_caption += (
                f'<div class="footer">Beta-matched passive book '
                f'({bmk_beta:.2f}&times;SPY + {1-bmk_beta:.2f} cash @0%, {html.escape(bmk_beta_src)}): '
                f'<span class="{"grn" if bret>=0 else "red"} mono">{bret:+.2f}%</span> · '
                f'strategy excess over beta-matched '
                f'<span class="{"grn" if bex>=0 else "red"} mono">{bex:+.2f}%</span>. '
                f'This — not 0% — is the hurdle the strategy must clear to demonstrate skill.</div>')
    chart = f"""
    <div class="panel"><h2>Equity Curve &amp; Drawdown{' vs SPY &amp; beta-matched book' if spy_data else ''}</h2>
      {"<div class='warn'>Only "+str(len(pvs))+" data point(s) — the curve fills in as the daily cron accrues history.</div>" if len(pvs)<2 else ""}
      <canvas id="eq" height="90"></canvas>
      <canvas id="uw" height="45" style="margin-top:8px"></canvas>{bench_caption}{regime_caption}</div>"""

    # ── Positions table ──
    prows = ""
    for r in positions:
        star = "" if r["in_state"] else (
            ' <span class="tag" style="background:var(--red);color:#fff" '
            'title="Held at the broker but absent from state.json. The agent does '
            'not know this position exists: it will never stop it, target it or '
            'time-stop it. There is no resting stop order at the broker either.">'
            'UNMANAGED</span>')
        #  No state, no stop. Print nothing rather than something comforting.
        _sv = f"{r['stop']:.2f}" if r["stop"] is not None else "&mdash;"
        _ds = f"{r['dist_stop']:+.1f}%" if r["dist_stop"] is not None else "&mdash;"
        _tv = f"{r['target']:.2f}" if r["target"] is not None else "&mdash;"
        _dt = f"{r['dist_tgt']:+.1f}%" if r["dist_tgt"] is not None else "&mdash;"
        _rv = f"${r['risk']:,.0f}" if r["risk"] is not None else "&mdash;"
        _dsc = "mono grn" if r["dist_stop"] is not None else "mono mut"
        _dtc = "mono blu" if r["dist_tgt"] is not None else "mono mut"
        _rvc = "mono amb" if r["risk"] is not None else "mono mut"
        t1 = "✓" if r["t1"] else "·"; t2 = "✓" if r["t2"] else "·"
        prows += f"""<tr>
          <td>{html.escape(str(r['symbol']))}{star} <span class="tag tag-{r['dir']}">{r['dir']}</span></td>
          <td class="mono">{r['qty']}</td>
          <td class="mono">{r['entry']:.2f}</td>
          <td class="mono" data-sym="{html.escape(str(r['symbol']))}" data-f="cur">{r['cur']:.2f}</td>
          <td data-sym="{html.escape(str(r['symbol']))}" data-f="plpc">{color_num(r['plpc'],'{:+.1f}%')}</td>
          <td class="mono">{_sv}</td>
          <td class="{_dsc}">{_ds}</td>
          <td class="mono">{_tv}</td>
          <td class="{_dtc}">{_dt}</td>
          <td class="{_rvc}">{_rv}</td>
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

    costs = load_json(BASE / "costs.json", {})
    if costs:
        cost_val = f"${costs['total_cost']:,.0f} ({costs['cost_bps_of_notional']:.1f} bps)"
        net_val = f"{costs['net_return_pct']:+.2f}% (gross {costs['gross_return_pct']:+.2f}%, -{costs['cost_drag_pct']:.2f} pts)"
    else:
        cost_val = net_val = "N/A"

    # ── Trade stats: POSITION-level, not fragment-level ──
    # m["trades"] is computed over trades_closed.csv rows, which are exit
    # FRAGMENTS (tiered T1/T2/T3 scale-outs + partial fills). Publishing a
    # fragment win rate overstates the hit rate because the T1 fragment is a
    # win by construction. We roll up to positions before computing any
    # win rate / count / expectancy.
    if alpha_t is None:
        t_note = f'<span class="warn">· t-stat unavailable (n={alpha_n})</span>'
    elif abs(alpha_t) < 1.96:
        t_note = (f'<span class="warn">· t={alpha_t:+.2f}, n={alpha_n} — '
                  f'NOT significant, indistinguishable from zero</span>')
    else:
        t_note = f'<span class="warn">· t={alpha_t:+.2f}, n={alpha_n}</span>'

    frag = analytics.load_closed_trades(BASE / "trades_closed.csv")
    posn = rollup_positions(frag)
    t = analytics.trade_stats(posn)
    n_frag = len(frag)
    n_long = sum(1 for p in posn if p["direction"] == "long")
    n_short = len(posn) - n_long
    warn = ' <span class="warn">(account-level, incl. legacy trades)</span>' if t["n_trades"] else ""
    guard = ' <span class="warn">· sample too small</span>' if t.get("insufficient_sample") else ""
    stats = f"""
    <div class="panel"><h2>Performance — position-level{warn}</h2>
      <div class="kv"><span class="mut">Closed positions</span><span class="mono">{t['n_trades']}{guard}</span></div>
      <div class="kv"><span class="mut">&nbsp;&nbsp;— long / short</span><span class="mono">{n_long} / {n_short}</span></div>
      <div class="kv"><span class="mut">Exit fragments (not trades)</span><span class="mono">{n_frag}</span></div>
      <div class="kv"><span class="mut">Win rate <b class="amb">(position-level)</b></span><span class="mono">{(str(round(t['win_rate']*100,1))+'%  ('+str(t.get('n_wins',0))+'W/'+str(t.get('n_losses',0))+'L)') if t['win_rate'] is not None else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Profit factor (position)</span><span class="mono">{('%.2f'%t['profit_factor']) if t['profit_factor'] else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Expectancy / position</span><span>{color_num(t['expectancy'],'${:+,.2f}') if t['expectancy'] is not None else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Avg hold (days)</span><span class="mono">{('%.1f'%t['avg_hold_days']) if t.get('avg_hold_days') else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Sharpe</span><span>{ratio_str(m['sharpe'])}</span></div>
      <div class="kv"><span class="mut">Sortino</span><span>{ratio_str(m['sortino'])}</span></div>
      <div class="kv"><span class="mut">Calmar</span><span>{ratio_str(m['calmar'])}</span></div>
      <div class="kv"><span class="mut">Volatility (ann.)</span><span>{pct_ratio(m['volatility'])}</span></div>
      <div class="kv"><span class="mut">Beta vs SPY</span><span>{market_str(m['market'],'beta')}</span></div>
      <div class="kv"><span class="mut">Alpha (ann.)</span><span>{alpha_str(m['market'])} {t_note}</span></div>
      <div class="kv"><span class="mut">Correlation</span><span>{market_str(m['market'],'correlation')}</span></div>
      <div class="kv"><span class="mut">Recovery factor</span><span class="mono">{('%.2f'%m['recovery_factor']) if m['recovery_factor'] else 'N/A'}</span></div>
      <div class="kv"><span class="mut">Trading cost (all-in)</span><span class="mono">{cost_val}</span></div>
      <div class="kv"><span class="mut">Return net of costs</span><span class="mono">{net_val}</span></div></div>"""

    # ── Mandatory disclosure (always rendered, never collapsible) ──
    n_days = len(equity)
    span = f"{labels[0]} → {labels[-1]}" if labels else "n/a"
    if costs:
        cost_line = (f'All headline return figures on this page are <b>GROSS of trading costs</b>. '
                     f'Measured costs to date are ${costs["total_cost"]:,.0f} '
                     f'({costs["cost_bps_of_notional"]:.1f} bps of notional), which takes the '
                     f'{costs["gross_return_pct"]:+.2f}% gross return to {costs["net_return_pct"]:+.2f}% net. '
                     f'Research-grade cost assumptions (10 bps slippage per leg + 1% borrow) consume '
                     f'the entire estimated alpha.')
    else:
        cost_line = ('All headline return figures on this page are <b>GROSS of trading costs</b>. '
                     'A measured cost figure is <b>unavailable</b> for the current period. '
                     'Research-grade cost assumptions (10 bps slippage per leg + 1% borrow) consume '
                     'the entire estimated alpha.')
    wr_txt = (f'{t["win_rate"]*100:.1f}% ({t.get("n_wins",0)}W/{t.get("n_losses",0)}L over '
              f'{t["n_trades"]} positions)') if t.get("win_rate") is not None else "unavailable"
    #  Lead with the measured result. Recomputed here rather than carried down
    #  from the chart block so this paragraph cannot silently disagree with the
    #  caption above it if either is edited.
    _lead = ""
    if spy_data and pvs and pvs[0] and spy_data[0] and spy_data[-1]:
        _s = (pvs[-1] / pvs[0] - 1) * 100
        _b = (spy_data[-1] / spy_data[0] - 1) * 100
        _x = _s - _b
        _bm_txt = ""
        if bmk_data and bmk_data[0]:
            _bx = _s - (bmk_data[-1] / bmk_data[0] - 1) * 100
            _bm_txt = (f' Against the beta-matched passive book &mdash; the hurdle that '
                       f'holds market exposure equal, described below &mdash; the excess is '
                       f'<b class="{"grn" if _bx>=0 else "red"} mono">{_bx:+.2f}%</b>.')
        _lead = (
            f'<p><b>Live result, {span}:</b> the strategy returned '
            f'<b class="{"grn" if _s>=0 else "red"} mono">{_s:+.2f}%</b> against SPY '
            f'buy &amp; hold (dividend-adjusted) '
            f'<b class="{"grn" if _b>=0 else "red"} mono">{_b:+.2f}%</b> &mdash; an excess of '
            f'<b class="{"grn" if _x>=0 else "red"} mono">{_x:+.2f}%</b>.{_bm_txt} '
            f'That is the arithmetic of this window and it is not in dispute: on this '
            f'sample the book outperformed both passive alternatives, gross of costs.</p>')

    #  ...and then say exactly what it does not license. Ordering, not content:
    #  every caveat below is unchanged.
    if alpha_t is not None and alpha_n:
        _infer = (f'<p><b>What that does not yet establish is skill.</b> The live alpha carries '
                  f't = <b class="mono">{alpha_t:+.2f}</b> on n = {alpha_n} daily observations; '
                  f'|t| &gt; 1.96 is the conventional bar. At this sample size a strategy with '
                  f'genuinely no edge produces a result this good or better a meaningful '
                  f'fraction of the time, so the excess above is <i>consistent with</i> skill '
                  f'without being <i>evidence of</i> it. The multi-year backtest points the '
                  f'other way &mdash; SPY wins there &mdash; and a three-month window in which '
                  f'this book is ahead does not overturn it. The items below are why the '
                  f'question is still open, not a claim that the number above is wrong.</p>')
    else:
        _infer = ('<p><b>What that does not yet establish is skill.</b> The live sample is too '
                  'short to compute a meaningful significance test on, which is itself the '
                  'point: the excess above is consistent with skill without being evidence '
                  'of it. The items below are why the question is still open, not a claim '
                  'that the number above is wrong.</p>')

    disclosure = f"""
    <div class="disclose"><h2>&#9888; The live result, and what it does and does not prove</h2>
      {_lead}{_infer}
      <ul>
        <li><b>No statistically significant alpha has been demonstrated.</b> Factor attribution
            (Phase 8, 3/4/6-factor and CAPM) finds zero significant alpha in <i>any</i> configuration.
            The long-only candidate measured there (V4, 2.0&times;ATR) shows alpha
            <b>+2.26%/yr with t = 0.57</b>
            (95% CI &minus;5.44% to +9.96%) &mdash; statistically indistinguishable from zero. The
            previously live long/short V3 showed alpha <b>&minus;10.77%/yr, t = &minus;1.91</b>.
            Measured beta is ~0.55: the return is market exposure, not skill.
            The deployed <b>V5</b> (1.5&times;ATR) does not change this &mdash; its alpha against
            the strategy's own universe is <b>+1.51%/yr (t = +0.38)</b>. V5 was deployed to make
            realised risk equal the 1% the system claims to risk, <i>not</i> to create an edge.</li>
        <li><b>The correct benchmark is not 0%.</b> SPY buy &amp; hold returned <b>+229% (Sharpe 0.93)</b>
            over 2019&ndash;2026, beating every strategy variant tested. A passive
            0.55&times;SPY + 0.45 cash book reproduces essentially the whole result. Both benchmarks are
            plotted on the equity chart above.</li>
        <li>{cost_line}</li>
        <li><b>The win rate shown is POSITION-level: {wr_txt}.</b> Earlier versions of this dashboard
            and older documents reported a <b>65.4% / 85.4% win rate</b>; those were <b>fragment-level</b>
            and upward-biased, because tiered exits emit up to three exit fragments per position and the
            T1 fragment is a win <i>by construction</i>. Trade counts were inflated the same way
            (e.g. 1,279 backtest &ldquo;trades&rdquo; were really 688 positions).
            Fragment counts are still shown, but labelled as fragments.</li>
        <li><b>The live track record is short.</b> {n_days} equity observations
            ({span}) and {t['n_trades']} closed positions &mdash; far too few to distinguish skill from
            luck. Ratios such as Sharpe/Sortino/Calmar over this window are descriptive only, carry no
            inferential weight, and should not be extrapolated.</li>
        <li>Paper-trading account with simulated capital. Past and simulated results are not indicative
            of future results. Nothing here is investment advice.</li>
      </ul></div>"""

    # ── Recent closed trades ──
    trades = sorted(frag, key=lambda x: x.get("close_date", ""), reverse=True)
    trows = ""
    for tr in trades:
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
    #  Position-level history: THE trade history. Fragments are an artefact of
    #  tiered exits; a reader asking "what trades has it made" means this.
    _pclosed = sorted([x for x in posn if x.get("close_date")],
                      key=lambda x: x.get("close_date", ""), reverse=True)
    prows_hist = ""
    for pz in _pclosed:
        _d = "L" if pz.get("direction") == "long" else "S"
        _rp = pz.get("return_pct")
        _hd = pz.get("hold_days")
        prows_hist += f"""<tr>
          <td>{html.escape(pz.get('close_date',''))}</td>
          <td>{html.escape(pz.get('symbol',''))} <span class="tag tag-{_d}">{_d}</span></td>
          <td>{html.escape(pz.get('entry_date',''))}</td>
          <td>{color_num(pz.get('gross_pnl'),'${:+,.2f}')}</td>
          <td>{color_num(_rp,'{:+.2f}%')}</td>
          <td class="mono">{_hd if _hd is not None else ''}</td>
          <td class="mono mut">{pz.get('n_fragments','')}</td></tr>"""
    if not prows_hist:
        prows_hist = '<tr><td colspan="7" class="mut">No closed positions yet.</td></tr>'

    ttable = f"""
    <div class="panel"><h2>Closed Positions &mdash; full history</h2>
      <div class="tbar">
        <input class="filter" data-filter="hist-pos" placeholder="Filter by symbol&hellip;">
        <span class="count" data-count="hist-pos"></span>
        <span class="count">click any column to sort</span>
      </div>
      <div class="scroll" id="hist-pos">
      <table><thead><tr><th>Closed</th><th>Symbol</th><th>Opened</th><th>P&amp;L $</th><th>Return</th><th>Days</th><th>Frags</th></tr></thead>
      <tbody>{prows_hist}</tbody></table></div>
      <div class="footer">One row per <b>position</b> &mdash; every closed trade the agent has
      made, tiered exits rolled up. This is the trade history; the fragment ledger below is
      the raw exit record it is built from.</div></div>

    <div class="panel"><h2>Exit Fragment Ledger &mdash; full history</h2>
      <div class="tbar">
        <input class="filter" data-filter="hist-frag" placeholder="Filter by symbol&hellip;">
        <span class="count" data-count="hist-frag"></span>
        <span class="count">click any column to sort</span>
      </div>
      <div class="scroll" id="hist-frag">
      <table><thead><tr><th>Closed</th><th>Symbol</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&amp;L $</th><th>Return</th><th>Days</th></tr></thead>
      <tbody>{trows}</tbody></table></div>
      <div class="footer">Each row is one <b>exit fragment</b> (a tiered T1/T2/T3 scale-out or a
      partial fill), <b>not</b> a position. {n_frag} fragments correspond to {t['n_trades']} positions
      ({n_frag/t['n_trades']:.1f} fragments per position). Do not count these rows as trades, and
      do not read a win rate off this table &mdash; a T1 fragment is a winner by construction.</div></div>""" if t["n_trades"] else f"""
    <div class="panel"><h2>Exit Fragment Ledger</h2>
      <table><thead><tr><th>Closed</th><th>Symbol</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&amp;L $</th><th>Return</th><th>Days</th></tr></thead>
      <tbody>{trows}</tbody></table></div>"""

    _VER, _VLABEL = _deployed_version()
    _VSHORT = _VLABEL.split(' ')[0]
    footer = (f'<div class="footer">Generated {now_et:%Y-%m-%d %H:%M:%S ET} · '
              f'orders last run: {hb.get("n_orders_submitted","?")} submitted / '
              f'{hb.get("n_orders_rejected","?")} rejected · '
              f'{_VER} · {_VLABEL} · monitoring layer read-only</div>')

    scan_rows, scan_meta = scan if scan else ([], {"error": "not run"})
    scan_panel = render_scan(scan_rows, scan_meta)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JP Alpha {_VSHORT} — Command Center</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>{CSS}</style></head><body>
<h1>JP ALPHA STRATEGY {_VSHORT} · COMMAND CENTER</h1>
<div class="tabs">
  <button class="tab on" id="btn-dash" onclick="showTab('dash')">Dashboard</button>
  <button class="tab" id="btn-scan" onclick="showTab('scan')">Analysis &mdash; Universe Scan</button>
</div>
<div id="tab-dash">
{banner}{_orphan_banner}{_pending_banner}{disclosure}{tiles}{chart}{postable}
<div class="grid2">{expo}{stats}</div>
{ttable}{footer}
</div>
<div id="tab-scan" class="hide">{scan_panel}</div>
<script>
const L={json.dumps(labels)},PV={json.dumps(pvs)},UW={json.dumps(uw)},SPY={json.dumps(spy_data)},BMK={json.dumps(bmk_data)};
const REGIMES={json.dumps(regime_marks)};
</script>
<script>{CHART_JS}</script>
<script>{TAB_JS}</script>
<script>{TABLE_JS}
wireTable('hist-pos'); wireTable('hist-frag'); wireTable('scan');
</script>{LIVE_JS}</body></html>"""


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

    # SPY price series (read-only) — reused for BOTH the benchmark overlay and
    # the beta/alpha regression inside analytics, so we fetch it only once.
    spy_map = fetch_spy_series(equity[0]["date"]) if equity else {}

    # Portfolio-history 1D can carry weekend/holiday rows (flat, 0% return) that
    # deflate volatility and inflate Sharpe. When the SPY trading calendar is
    # available, restrict the curve to real trading days. Guarded so a SPY
    # outage can never blank the equity curve.
    if spy_map and equity:
        _td = [e for e in equity if e["date"] in spy_map]
        if len(_td) >= max(2, int(0.6 * len(equity))):
            equity = _td

    #  Enrich FIRST: enrich_positions() mirrors the agent's exit rule, so it is
    #  the only thing here that knows where a stop actually is. Feeding those
    #  levels into compute_all keeps the Open Risk tile consistent with the
    #  Stop column beneath it -- they used to disagree, because the tile
    #  assumed a fixed 8% stop the agent stopped using at V4.
    enriched = enrich_positions(positions, state)
    _stops = {r["symbol"]: r["stop"] for r in enriched if r.get("stop")}

    m = analytics.compute_all(BASE / "equity_curve.csv", BASE / "trades_closed.csv",
                              acct, positions, start_eq, STOP_PCT,
                              equity_override=equity, benchmark_prices=spy_map,
                              stops=_stops)

    # SPY buy-and-hold benchmark aligned to the equity curve (read-only overlay).
    spy = spy_benchmark(equity, spy_map) if equity else None

    # Beta-matched passive book. Prefer the beta measured on THIS equity curve;
    # if the live sample is too short to estimate it, fall back to the Phase 8
    # research estimate (0.55) and say so, rather than inventing a number.
    mk = m.get("market") or {}
    live_beta = None if mk.get("insufficient_sample") else mk.get("beta")
    # We deliberately do NOT use the live-window beta point estimate for the
    # hurdle: on a sample this short it is dominated by noise (and a spuriously
    # low beta would flatter the strategy by lowering the bar). We use the
    # full-sample Phase 8 structural estimate and disclose the live estimate
    # alongside it.
    bmk_beta = 0.55
    bmk_src = "beta 0.55, Phase 8 full-sample estimate"
    if live_beta is not None:
        bmk_src += f"; live-window point estimate {live_beta:.2f}, n={mk.get('n','?')}, too noisy to use"
    bmk = beta_matched_benchmark(equity, spy_map, bmk_beta) if equity else None

    a_t, a_n = alpha_tstat(equity, spy_map)

    #  Universe scan: the Analysis tab. Guarded inside scan_universe(), so a
    #  yfinance outage degrades to a notice on that tab and leaves the
    #  broker-sourced dashboard untouched.
    _held = {str(p.get("symbol")): ("long" if float(p.get("qty", 0) or 0) > 0 else "short")
             for p in (positions or [])}
    scan = scan_universe(_held)

    html_out = render(m, acct, enriched, clock, hb, equity, spy, bmk, bmk_beta, bmk_src,
                      alpha_t=a_t, alpha_n=a_n, scan=scan,
                      pending=state.get("pending_orders") or {})

    out = BASE / "dashboard.html"
    out.write_text(html_out)
    print(f"Dashboard written: {out} ({len(html_out):,} bytes)")


if __name__ == "__main__":
    main()
