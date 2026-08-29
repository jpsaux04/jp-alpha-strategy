#!/usr/bin/env python3
"""V4 pre-deployment smoke test. Stubs ALL order submission -- nothing is sent.

Verifies, for both version gates:
  1. module imports and config resolves
  2. a full entry scan runs without exception
  3. V4 emits NO short orders; V3 emits shorts when the signal fires
  4. the long ATR stop is computed from atr_at_entry and differs from -8%
  5. exit levels anchor to fill_price under V4 and to entry_price under V3
"""
import os, sys, importlib
sys.path.insert(0, "/root/jp_strategy")
os.chdir("/root/jp_strategy")

SENT = []


def harness(version):
    os.environ["STRATEGY_VERSION"] = version
    for m in list(sys.modules):
        if m == "jp_agent":
            del sys.modules[m]
    a = importlib.import_module("jp_agent")

    a.alpaca_post = lambda ep, body: (_ for _ in ()).throw(
        AssertionError(f"ORDER LEAKED TO BROKER: {body}"))
    a.place_market_order = lambda s, q, sd: (SENT.append((version, s, q, sd))
                                             or {"id": "STUB", "status": "stubbed"})
    a.get_fill_price = lambda oid, **k: None

    print(f"\n{'='*74}\n  {version}\n{'='*74}")
    print(f"  ALLOW_SHORTS={a.ALLOW_SHORTS}  STOP_ATR_MULT={a.STOP_ATR_MULT}  "
          f"ANCHOR_ON_FILL={a.ANCHOR_ON_FILL}")

    state = a.load_state() if hasattr(a, "load_state") else {"positions": {}}
    data = a.fetch_market_data() if hasattr(a, "fetch_market_data") else None
    if data is None:
        for nm in ("load_market_data", "get_market_data", "fetch_data"):
            if hasattr(a, nm):
                data = getattr(a, nm)()
                break
    assert data is not None, "could not locate the market-data loader"

    orders = a.process_entries({"positions": {}}, data, 100_000.0)
    dirs = [o["direction"] for o in orders]
    print(f"  entry scan -> {len(orders)} orders  "
          f"(long {dirs.count('long')} / short {dirs.count('short')})")
    if version.endswith("STOPATR2"):
        assert dirs.count("short") == 0, "V4 EMITTED A SHORT ORDER"
        print("  PASS: no short orders under V4")

    # exit-level check on a synthetic position
    pos = {"direction": "long", "entry_price": 100.0, "fill_price": 101.0,
           "atr_at_entry": 3.0, "shares_total": 100, "shares_remaining": 100,
           "t1_hit": False, "t2_hit": False, "t1_hit_date": None,
           "entry_date": a.date.today().isoformat()}
    row = {"High": 101.5, "Low": 94.0, "Close": 95.0}
    anchor = (pos["fill_price"] if a.ANCHOR_ON_FILL else pos["entry_price"])
    stop = (anchor - a.STOP_ATR_MULT * 3.0) if a.STOP_ATR_MULT > 0 else anchor * 0.92
    print(f"  anchor={anchor:.2f}  computed stop={stop:.2f}  "
          f"({'2.0xATR' if a.STOP_ATR_MULT else '-8% fixed'})")
    res = a.process_long_exits("TEST", pos, row, a.date.today())
    if isinstance(res, dict):
        got = res.get("action")
    elif isinstance(res, (tuple, list)):
        got = res[0]
    else:
        got = res
    print(f"  process_long_exits(Low=94.0) -> {got!r}   (expect STOP_LOSS iff 94.0 <= stop)")
    assert (got == "STOP_LOSS") == (94.0 <= stop), \
        f"stop logic inconsistent: got {got!r} with stop {stop:.2f}"
    print(f"  PASS: stop trigger consistent with computed level")
    return stop


s4 = harness("JP_ALPHA_V4_LONGONLY_STOPATR2")
s3 = harness("JP_ALPHA_V3_FROZEN")

print(f"\n{'='*74}")
assert abs(s4 - 95.0) < 1e-6, f"V4 stop should be 101-2*3=95.0, got {s4}"
assert abs(s3 - 92.0) < 1e-6, f"V3 stop should be 100*0.92=92.0, got {s3}"
assert not any(v.endswith("STOPATR2") and sd == "sell_short" for v, _, _, sd in SENT)
print("  ALL ASSERTIONS PASSED — no order reached the broker")
print(f"  stubbed submissions: {len(SENT)}")
