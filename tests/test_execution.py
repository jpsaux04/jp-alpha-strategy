#!/usr/bin/env python3
"""PHASE 1 — execution/state hardening tests (EXEC-1, 3, 4, 5, 6, 7).

Runs against a FAKE broker. No network, no orders. Each test reproduces the
specific failure the corresponding defect would have caused, and asserts the
new behaviour prevents it.

Run: python3 tests/test_execution.py
"""
import os, sys, json, importlib, copy

sys.path.insert(0, "/root/jp_strategy")
os.chdir("/root/jp_strategy")
os.environ.setdefault("STRATEGY_VERSION", "JP_ALPHA_V4_LONGONLY_STOPATR2")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


class FakeBroker:
    """Minimal Alpaca stand-in with programmable fill behaviour."""

    def __init__(self):
        self.orders = {}        # id -> order dict
        self.by_coid = {}
        self.open_orders = []
        self.deleted = []
        self.n_submits = 0
        self.fill_mode = "full"   # full | partial | reject | none
        self.seq = 0

    # -- endpoints --
    def get(self, ep):
        if ep.startswith("/v2/orders:by_client_order_id"):
            coid = ep.split("client_order_id=")[1]
            if coid in self.by_coid:
                return self.orders[self.by_coid[coid]]
            raise RuntimeError("404 no such order")
        if ep.startswith("/v2/orders?"):
            return list(self.open_orders)
        if ep.startswith("/v2/orders/"):
            return self.orders[ep.split("/v2/orders/")[1]]
        raise RuntimeError(f"unexpected GET {ep}")

    def post(self, ep, body):
        assert ep == "/v2/orders"
        self.n_submits += 1
        coid = body.get("client_order_id")
        if coid and coid in self.by_coid:
            import requests
            r = requests.Response()
            r.status_code = 422
            r._content = b'{"message":"client_order_id must be unique"}'
            raise requests.HTTPError(response=r)
        self.seq += 1
        oid = f"oid{self.seq}"
        req = int(body["qty"])
        if self.fill_mode == "full":
            fq, st = req, "filled"
        elif self.fill_mode == "partial":
            fq, st = max(1, req // 2), "partially_filled"
        elif self.fill_mode == "reject":
            fq, st = 0, "rejected"
        else:
            fq, st = 0, "new"
        o = {"id": oid, "client_order_id": coid, "status": st,
             "symbol": body["symbol"], "qty": str(req), "filled_qty": str(fq),
             "filled_avg_price": "101.50" if fq else None}
        self.orders[oid] = o
        if coid:
            self.by_coid[coid] = oid
        return o

    def delete(self, ep):
        self.deleted.append(ep)
        return 204


def load_agent(broker):
    for m in list(sys.modules):
        if m == "jp_agent":
            del sys.modules[m]
    a = importlib.import_module("jp_agent")
    a.alpaca_get = broker.get
    a.alpaca_post = broker.post
    a.alpaca_delete = broker.delete
    import time
    a_time = time.sleep
    time.sleep = lambda s: None        # no real delays in tests
    return a


def entry_order(sym="AAPL", qty=10):
    return {"symbol": sym, "qty": qty, "side": "buy", "direction": "long",
            "entry_price": 100.0, "atr": 3.0}


# ── EXEC-1: idempotency ─────────────────────────────────────────────────────
print("\nEXEC-1  deterministic client_order_id / idempotent submission")
b = FakeBroker(); a = load_agent(b)
coid1 = a.make_coid("AAPL", "long", "ENTRY", 0)
coid2 = a.make_coid("AAPL", "long", "ENTRY", 0)
check("coid is deterministic for identical intent", coid1 == coid2, coid1)
check("coid encodes symbol/direction/tag",
      "AAPL" in coid1 and "-L-" in coid1 and "ENTRY" in coid1)

st = {"positions": {}}
a.execute_orders([entry_order()], st)
first_submits = b.n_submits
st2 = {"positions": {}}
a.execute_orders([entry_order()], st2)      # same intent, same day = replay
check("replayed run does not double-submit a NEW order",
      b.n_submits == first_submits + 1 and len(b.orders) == 1,
      f"submit attempts={b.n_submits}, distinct broker orders={len(b.orders)}")
check("replay still resolves to the original order",
      st2["positions"]["AAPL"]["order_id"] == st["positions"]["AAPL"]["order_id"])

# ── EXEC-5: rejected entry must not create a position ───────────────────────
print("\nEXEC-5  order status is acted on, not merely logged")
b = FakeBroker(); b.fill_mode = "reject"; a = load_agent(b)
st = {"positions": {}}
res = a.execute_orders([entry_order("MSFT")], st)
check("rejected entry creates NO position", "MSFT" not in st["positions"])
check("rejected entry reported as no-fill", res[0]["status"].startswith("NOFILL"),
      res[0]["status"])

# ── EXEC-5: rejected EXIT must roll the position back ───────────────────────
b = FakeBroker(); b.fill_mode = "reject"; a = load_agent(b)
pos = {"direction": "long", "entry_price": 100.0, "fill_price": 100.0,
       "atr_at_entry": 3.0, "shares_total": 100, "shares_remaining": 100,
       "t1_hit": False, "t2_hit": False, "t1_hit_date": None,
       "entry_date": a.date.today().isoformat(), "sector": "Tech"}
st = {"positions": {}}                      # process_exits already "closed" it
exit_o = {"symbol": "NVDA", "qty": 100, "side": "sell", "direction": "long",
          "action": "STOP_LOSS", "reason": "test", "entry_price": 100.0,
          "atr": 3.0, "_snapshot": copy.deepcopy(pos), "_intended_qty": 100}
a.execute_orders([exit_o], st)
check("rejected EXIT restores the position (no silent orphan)",
      "NVDA" in st["positions"] and st["positions"]["NVDA"]["shares_remaining"] == 100,
      f"state now: {list(st['positions'])}")

# ── EXEC-7: partial fills ───────────────────────────────────────────────────
print("\nEXEC-7  partial fills")
b = FakeBroker(); b.fill_mode = "partial"; a = load_agent(b)
st = {"positions": {}}
res = a.execute_orders([entry_order("TSLA", 10)], st)
check("partial entry records FILLED qty, not requested",
      st["positions"]["TSLA"]["shares_total"] == 5,
      f"shares_total={st['positions']['TSLA']['shares_total']} of 10 requested")
check("partial entry reported with requested_qty", res[0]["requested_qty"] == 10)

b = FakeBroker(); b.fill_mode = "partial"; a = load_agent(b)
st = {"positions": {}}
exit_o = {"symbol": "AMD", "qty": 100, "side": "sell", "direction": "long",
          "action": "T3_HIT", "reason": "test", "entry_price": 100.0, "atr": 3.0,
          "_snapshot": copy.deepcopy(pos), "_intended_qty": 100}
a.execute_orders([exit_o], st)
check("partial EXIT leaves the unsold remainder open",
      st["positions"].get("AMD", {}).get("shares_remaining") == 50,
      f"remaining={st['positions'].get('AMD',{}).get('shares_remaining')}")

# ── EXEC-2: anchor uses the real fill ───────────────────────────────────────
print("\nEXEC-2  exit levels anchor to the actual fill")
b = FakeBroker(); a = load_agent(b)
st = {"positions": {}}
a.execute_orders([entry_order("GOOG")], st)
check("fill_price recorded from broker, not reference price",
      st["positions"]["GOOG"]["fill_price"] == 101.50,
      f"ref={st['positions']['GOOG']['entry_price']} fill={st['positions']['GOOG']['fill_price']}")

# ── EXEC-3: scoped cancellation ─────────────────────────────────────────────
print("\nEXEC-3  cancellation is scoped to orders we own")
b = FakeBroker(); a = load_agent(b)
b.open_orders = [
    {"id": "ours1", "client_order_id": "JPV4-AAPL-L-ENTRY-20260828-0"},
    {"id": "ours2", "client_order_id": "JPV3-MSFT-S-ENTRY-20260828-1"},
    {"id": "human", "client_order_id": "manual-trade-by-JP"},
    {"id": "other", "client_order_id": ""},
]
a.cancel_all_pending_orders()
cancelled = {e.split("/")[-1] for e in b.deleted}
check("our orders cancelled", {"ours1", "ours2"} <= cancelled)
check("foreign orders untouched", not ({"human", "other"} & cancelled),
      f"cancelled={sorted(cancelled)}")
check("no blanket DELETE /v2/orders issued", "/v2/orders" not in b.deleted)

b = FakeBroker(); a = load_agent(b)
os.environ["EMERGENCY_CANCEL_ALL"] = "1"
a.cancel_all_pending_orders()
check("blanket cancel available behind explicit flag", "/v2/orders" in b.deleted)
del os.environ["EMERGENCY_CANCEL_ALL"]

# ── EXEC-4 / EXEC-6: orphans and the halt gate ──────────────────────────────
print("\nEXEC-4/6  orphan detection, exposure accounting, halt gate")
b = FakeBroker(); a = load_agent(b)
st = {"positions": {"AAPL": {"direction": "long", "shares_remaining": 10,
                             "shares_total": 10, "entry_price": 100.0}}}
broker_pos = [{"symbol": "AAPL", "qty": "10"}, {"symbol": "NFLX", "qty": "-25"}]
st, rep = a.reconcile_state(st, broker_pos)
check("orphan detected", [o["symbol"] for o in rep["orphans"]] == ["NFLX"],
      str(rep["orphans"]))
check("orphan direction inferred from sign",
      rep["orphans"][0]["direction"] == "short")
check("reconcile reported as NOT clean", not a.reconcile_is_clean(rep))
check("orphans persisted to state for downstream limits",
      st.get("orphans") and st["orphans"][0]["symbol"] == "NFLX")

st2 = {"positions": {"AAPL": {"direction": "long", "shares_remaining": 10,
                              "shares_total": 10, "entry_price": 100.0}}}
st2, rep2 = a.reconcile_state(st2, [{"symbol": "AAPL", "qty": "10"}])
check("clean book reports clean", a.reconcile_is_clean(rep2))

st3 = {"positions": {"AAPL": {"direction": "long", "shares_remaining": 10,
                              "shares_total": 10, "entry_price": 100.0}}}
st3, rep3 = a.reconcile_state(st3, [{"symbol": "AAPL", "qty": "7"}])
check("partial-fill share drift is repaired",
      st3["positions"]["AAPL"]["shares_remaining"] == 7)
check("share drift alone does NOT halt entries", a.reconcile_is_clean(rep3),
      "drift is benign; orphans are not")

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  ALL EXECUTION HARDENING TESTS PASSED")
