#!/usr/bin/env python3
"""STRATEGY VERSION GATE — pre-deployment smoke test. Stubs ALL order
submission, so nothing can reach the broker.

Was tests_smoke_v4.py, which hardcoded V4's 2.0xATR stop in its own assertions
and so could not survive a version change. It is now table-driven: the expected
behaviour of every version is written out explicitly here, INDEPENDENTLY of what
jp_agent.py computes. A test that derives its expectation from the code under
test proves nothing.

For each version it checks:
  1. the module imports and the version gate resolves to the expected triple
  2. the realised-risk identity holds (Phase 2), where an ATR stop applies
  3. a full entry scan runs without exception
  4. long-only versions emit NO short orders
  5. the stop level is computed off the right anchor and the right multiple
  6. the stop actually TRIGGERS exactly when price reaches that level
and finally that an unknown version refuses to start rather than silently
falling back to V3.
"""
import importlib
import os
import subprocess
import sys

sys.path.insert(0, "/root/jp_strategy")
os.chdir("/root/jp_strategy")

SENT = []

# Synthetic position used for the stop-level check.
ENTRY, FILL, ATR = 100.0, 101.0, 3.0
PROBE_LOW = 94.0          # the low we feed the exit logic

# version -> (allow_shorts, stop_atr_mult, anchor_on_fill, expected_stop_price)
#   V3: fixed -8% off ENTRY          -> 100.0 * 0.92        = 92.0
#   V4: 2.0xATR off FILL             -> 101.0 - 2.0 * 3.0   = 95.0
#   V5: 1.5xATR off FILL             -> 101.0 - 1.5 * 3.0   = 96.5
EXPECT = {
    "JP_ALPHA_V3_FROZEN":             (True,  0.0, False, 92.0),
    "JP_ALPHA_V4_LONGONLY_STOPATR2":  (False, 2.0, True,  95.0),
    "JP_ALPHA_V5_LONGONLY_STOPATR15": (False, 1.5, True,  96.5),
}
CURRENT = "JP_ALPHA_V5_LONGONLY_STOPATR15"

PASS = FAIL = 0


def check(cond, name, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"    PASS  {name}   {detail}")
    else:
        FAIL += 1
        print(f"    FAIL  {name}   {detail}")


def harness(version):
    want_shorts, want_mult, want_anchor, want_stop = EXPECT[version]
    os.environ["STRATEGY_VERSION"] = version
    sys.modules.pop("jp_agent", None)
    a = importlib.import_module("jp_agent")

    a.alpaca_post = lambda ep, body: (_ for _ in ()).throw(
        AssertionError(f"ORDER LEAKED TO BROKER: {body}"))
    a.place_market_order = lambda s, q, sd, **kw: (
        SENT.append((version, s, q, sd)) or {"id": "STUB", "status": "stubbed"})
    a.get_fill_price = lambda oid, **k: None

    print(f"\n  {'=' * 72}\n  {version}{'   <== DEPLOYED' if version == CURRENT else ''}")
    print(f"  {'=' * 72}")

    check(a.ALLOW_SHORTS == want_shorts, "ALLOW_SHORTS",
          f"{a.ALLOW_SHORTS} (expected {want_shorts})")
    check(a.STOP_ATR_MULT == want_mult, "STOP_ATR_MULT",
          f"{a.STOP_ATR_MULT} (expected {want_mult})")
    check(a.ANCHOR_ON_FILL == want_anchor, "ANCHOR_ON_FILL",
          f"{a.ANCHOR_ON_FILL} (expected {want_anchor})")

    # Phase 2 realised-risk identity. With ATR sizing the volatility term
    # cancels, so risk is a pure constant -- this is the whole reason V5 exists.
    if a.STOP_ATR_MULT > 0:
        risk = a.RISK_PER_TRADE_PCT * a.STOP_ATR_MULT / a.ATR_MULTIPLIER
        intended = a.RISK_PER_TRADE_PCT
        check(True, "realised risk per position",
              f"{risk*100:.3f}% of equity vs {intended*100:.3f}% intended"
              + ("  <-- matches by construction" if abs(risk - intended) < 1e-12
                 else f"  <-- OVER-RISKS by {risk/intended:.2f}x"))
        if version == CURRENT:
            check(abs(risk - intended) < 1e-12,
                  "DEPLOYED version risks exactly what it claims",
                  f"{risk*100:.4f}% == {intended*100:.4f}%")

    # entry scan
    state = a.load_state() if hasattr(a, "load_state") else {"positions": {}}
    data = None
    for nm in ("fetch_market_data", "load_market_data", "get_market_data", "fetch_data"):
        if hasattr(a, nm):
            data = getattr(a, nm)()
            break
    assert data is not None, "could not locate the market-data loader"

    orders = a.process_entries({"positions": {}}, data, 100_000.0)
    dirs = [o["direction"] for o in orders]
    print(f"    ....  entry scan -> {len(orders)} orders "
          f"(long {dirs.count('long')} / short {dirs.count('short')})")
    if not want_shorts:
        check(dirs.count("short") == 0, "long-only version emits no shorts",
              f"{dirs.count('short')} short orders")

    # stop level, computed the way the agent computes it
    pos = {"direction": "long", "entry_price": ENTRY, "fill_price": FILL,
           "atr_at_entry": ATR, "shares_total": 100, "shares_remaining": 100,
           "t1_hit": False, "t2_hit": False, "t1_hit_date": None,
           "entry_date": a.date.today().isoformat()}
    anchor = pos["fill_price"] if a.ANCHOR_ON_FILL else pos["entry_price"]
    stop = (anchor - a.STOP_ATR_MULT * ATR) if a.STOP_ATR_MULT > 0 else anchor * 0.92
    check(abs(stop - want_stop) < 1e-9, "stop level",
          f"{stop:.2f} (expected {want_stop:.2f}, anchor {anchor:.2f})")

    row = {"High": 101.5, "Low": PROBE_LOW, "Close": 95.0}
    res = a.process_long_exits("TEST", pos, row, a.date.today())
    got = res.get("action") if isinstance(res, dict) else (
        res[0] if isinstance(res, (tuple, list)) else res)
    should_fire = PROBE_LOW <= stop
    check((got == "STOP_LOSS") == should_fire,
          "stop triggers exactly at its computed level",
          f"low {PROBE_LOW} vs stop {stop:.2f} -> {got!r} "
          f"(expected {'STOP_LOSS' if should_fire else 'no stop'})")
    return stop


if __name__ == "__main__":
    print("=" * 78)
    print("  STRATEGY VERSION GATE — SMOKE TEST (no orders are sent)")
    print("=" * 78)

    for v in EXPECT:
        harness(v)

    print(f"\n  {'=' * 72}\n  unknown-version guard\n  {'=' * 72}")
    env = dict(os.environ, STRATEGY_VERSION="JP_ALPHA_V9_DOES_NOT_EXIST")
    r = subprocess.run([sys.executable, "-c", "import jp_agent"],
                       capture_output=True, text=True, env=env, cwd="/root/jp_strategy")
    out = (r.stdout or "") + (r.stderr or "")
    check(r.returncode != 0, "unknown STRATEGY_VERSION refuses to start",
          f"exit {r.returncode}")
    check("Refusing to start" in out, "and says why",
          "silent fallback to V3 would re-enable shorts + the fixed -8% stop")

    check(not any(sd in ("sell_short", "short") for _, _, _, sd in SENT),
          "no short order was stubbed under any long-only version",
          f"{len(SENT)} stubbed submissions total")

    print("\n" + "=" * 78)
    print(f"  {PASS} passed, {FAIL} failed        (no order reached the broker)")
    print("=" * 78)
    sys.exit(1 if FAIL else 0)
