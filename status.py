#!/usr/bin/env python3
"""
JP Strategy v3 — Status Dashboard
Run anytime to see current positions, P&L, and account summary.
"""
import os, json, requests
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR   = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
CONFIG_FILE = BASE_DIR / "config.json"


def _start_equity():
    """Read starting_equity from config.json; fall back to 100000.0 (unchanged behavior)."""
    try:
        if CONFIG_FILE.exists():
            return float(json.loads(CONFIG_FILE.read_text()).get("starting_equity", 100000.0))
    except Exception:
        pass
    return 100000.0


load_dotenv(BASE_DIR / ".env")
APCA_KEY    = os.environ["APCA_API_KEY_ID"]
APCA_SECRET = os.environ["APCA_API_SECRET_KEY"]
APCA_URL    = os.environ.get("APCA_BASE_URL", "https://paper-api.alpaca.markets")
HEADERS     = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}

def get(endpoint):
    r = requests.get(f"{APCA_URL}{endpoint}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

account   = get("/v2/account")
positions = get("/v2/positions")
orders    = get("/v2/orders?status=open&limit=20")
state     = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"positions": {}}

pv   = float(account["portfolio_value"])
cash = float(account["cash"])
eq   = float(account["equity"])
init = _start_equity()  # starting portfolio (config.json, default 100000.0)

ET   = ZoneInfo("America/New_York")
now  = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")

print(f"""
╔══════════════════════════════════════════════════════════════╗
║       JP ALPHA STRATEGY v3  —  LIVE STATUS DASHBOARD        ║
╠══════════════════════════════════════════════════════════════╣
║  As of: {now:<52}║
╠══════════════════════════════════════════════════════════════╣
  Portfolio Value : ${pv:>12,.2f}
  Starting Capital: ${init:>12,.2f}
  Total P&L       : ${pv-init:>+12,.2f}  ({(pv/init-1)*100:+.2f}%)
  Cash (deployed) : ${cash:>12,.2f}  ({cash/pv*100:.1f}% cash)
  Invested        : ${pv-cash:>12,.2f}  ({(pv-cash)/pv*100:.1f}% deployed)
""")

print("  OPEN POSITIONS")
print("  " + "─" * 72)
if not positions:
    print("  No open positions.")
else:
    print(f"  {'Symbol':<8} {'Qty':>6} {'Entry':>9} {'Current':>9} {'P&L $':>10} {'P&L %':>8} {'T1':>4} {'Days':>6}")
    print("  " + "─" * 72)
    for p in positions:
        sym   = p["symbol"]
        qty   = int(float(p["qty"]))
        price = float(p["current_price"])
        cost  = float(p["avg_entry_price"])
        pl    = float(p["unrealized_pl"])
        plp   = float(p["unrealized_plpc"]) * 100

        # Get T1/T2 status from our state
        sp = state["positions"].get(sym, {})
        t1 = "✓" if sp.get("t1_hit") else "·"
        t2 = "✓" if sp.get("t2_hit") else "·"
        entry_date = sp.get("entry_date", "?")
        days = (date.today() - date.fromisoformat(entry_date)).days if entry_date != "?" else "?"

        print(f"  {sym:<8} {qty:>6} {cost:>9.2f} {price:>9.2f} {pl:>+10.2f} {plp:>+7.1f}%  T1={t1}T2={t2} {days:>4}d")

print()

if orders:
    print("  PENDING ORDERS")
    print("  " + "─" * 50)
    for o in orders:
        print(f"  {o['side'].upper():<4} {int(float(o['qty'])):>5} {o['symbol']:<6}  [{o['status']}]")
    print()

# Log last 5 entries from today's log
import glob
logs = sorted(glob.glob(str(BASE_DIR / "logs" / "agent_*.log")))
if logs:
    print("  LAST RUN LOG (tail)")
    print("  " + "─" * 72)
    with open(logs[-1]) as f:
        lines = f.readlines()
    for line in lines[-8:]:
        print("  " + line.rstrip())
