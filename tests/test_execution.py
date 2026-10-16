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

# ── EXEC-8: orders that have not filled YET ─────────────────────────────────
#  This is what makes running after the close safe. A market/day order
#  submitted after 16:00 ET is queued by the broker and fills at the next open.
#  FakeBroker's "none" mode reproduces that: accepted, status "new", 0 filled.
print("\nEXEC-8  unsettled orders are carried, not discarded")

#  1. An unsettled ENTRY must not create a position, and must not be forgotten.
b = FakeBroker(); b.fill_mode = "none"; a = load_agent(b)
st = {"positions": {}}
res = a.execute_orders([entry_order("AAPL", 10)], st)
check("unsettled entry creates NO position yet", "AAPL" not in st["positions"])
check("unsettled entry is recorded as pending",
      len(st.get("pending_orders", {})) == 1,
      f"pending={list(st.get('pending_orders', {}))}")
_pk = list(st["pending_orders"])[0]
_pv = st["pending_orders"][_pk]
check("pending record knows it is an entry and which symbol",
      _pv["is_entry"] and _pv["symbol"] == "AAPL")
check("pending record pins the risk contract at submission",
      _pv["stop_atr_mult"] == a.STOP_ATR_MULT and _pv["atr"] == 3.0)
check("unsettled entry reported as PENDING, not NOFILL",
      res[0]["status"].startswith("PENDING"), res[0]["status"])

#  2. An unsettled EXIT must NOT be rolled back -- the broker will still fill
#     it. Rolling back here is what would make state claim a position the
#     broker is about to close.
b = FakeBroker(); b.fill_mode = "none"; a = load_agent(b)
_pos = {"direction": "long", "entry_price": 100.0, "fill_price": 100.0,
        "atr_at_entry": 3.0, "shares_total": 100, "shares_remaining": 100,
        "t1_hit": False, "t2_hit": False, "t1_hit_date": None,
        "entry_date": "2026-01-05", "sector": "Tech"}
st = {"positions": {}}                      # process_exits already closed it
_exit = {"symbol": "NVDA", "qty": 100, "side": "sell", "direction": "long",
         "action": "STOP_LOSS", "reason": "t", "entry_price": 100.0, "atr": 3.0,
         "_snapshot": copy.deepcopy(_pos), "_intended_qty": 100}
a.execute_orders([_exit], st)
check("unsettled EXIT is NOT rolled back", "NVDA" not in st["positions"],
      f"state={list(st['positions'])}")
check("unsettled EXIT keeps its rollback snapshot for later",
      st["pending_orders"][list(st["pending_orders"])[0]]["snapshot"]["shares_remaining"] == 100)

#  3. An order we cannot confirm AT ALL is live until proven dead. The broker
#     accepted it, so discarding it would leave an unmanaged position.
class _Unreadable(FakeBroker):
    def get(self, ep):
        if ep.startswith("/v2/orders/"):
            raise RuntimeError("503 broker unreachable")
        return FakeBroker.get(self, ep)

b = _Unreadable(); b.fill_mode = "none"; a = load_agent(b)
st = {"positions": {}}
res = a.execute_orders([entry_order("MU", 5)], st)
check("unconfirmable order is carried as pending, not dropped",
      len(st.get("pending_orders", {})) == 1 and "MU" not in st["positions"],
      f"status={res[0]['status']}")

#  4. A REJECTION is still a rejection -- the old rollback path must survive.
b = FakeBroker(); b.fill_mode = "reject"; a = load_agent(b)
st = {"positions": {}}
res = a.execute_orders([entry_order("INTC")], st)
check("rejected order is NOT carried as pending",
      not st.get("pending_orders") and res[0]["status"].startswith("NOFILL"),
      f"pending={st.get('pending_orders')}, status={res[0]['status']}")

# ── EXEC-8: resolving pending orders on the next run ────────────────────────
print("\nEXEC-8  resolution on the following run")


def _pending_state(**kw):
    p = {"order_id": "oidX", "symbol": "AAPL", "side": "buy", "direction": "long",
         "qty": 10, "is_entry": True, "submitted_at": "2026-03-02T16:10:00",
         "entry_price": 100.0, "atr": 3.0, "stop_atr_mult": 2.0,
         "sector": "Tech", "snapshot": None, "intended_qty": 10}
    p.update(kw)
    return {"positions": {}, "pending_orders": {"coidX": p}}


def _broker_order(**kw):
    o = {"id": "oidX", "status": "filled", "filled_qty": "10",
         "filled_avg_price": "98.25", "symbol": "AAPL",
         "filled_at": "2026-03-03T14:30:05Z"}       # 09:30:05 ET next session
    o.update(kw)
    return o


#  5. Entry filled overnight -> position created, dated when it FILLED.
b = FakeBroker(); a = load_agent(b)
b.orders["oidX"] = _broker_order()
st = _pending_state()
out = a.resolve_pending_orders(st)
check("overnight entry fill creates the position",
      "AAPL" in st["positions"], f"state={list(st['positions'])}")
_p = st["positions"].get("AAPL", {})
check("entry_date is the FILL date, not the submission date",
      _p.get("entry_date") == "2026-03-03", str(_p.get("entry_date")))
check("shares come from the actual fill", _p.get("shares_remaining") == 10)
check("stop multiple is the one pinned at submission",
      _p.get("stop_atr_mult") == 2.0, str(_p.get("stop_atr_mult")))
check("resolved pending order is removed from the queue",
      not st["pending_orders"])
check("resolution is reported for the blotter at the fill time",
      out and out[0]["date"] == "2026-03-03" and out[0]["time_et"].startswith("09:30"),
      str(out))

#  6. Entry that died overnight -> no position, no trace.
b = FakeBroker(); a = load_agent(b)
b.orders["oidX"] = _broker_order(status="canceled", filled_qty="0",
                                 filled_avg_price=None)
st = _pending_state()
a.resolve_pending_orders(st)
check("dead overnight entry creates NO position",
      "AAPL" not in st["positions"] and not st["pending_orders"])

#  7. Exit that died overnight -> the position must come BACK. This is the
#     rollback the submitting run deliberately deferred.
b = FakeBroker(); a = load_agent(b)
b.orders["oidX"] = _broker_order(status="canceled", filled_qty="0",
                                 filled_avg_price=None)
st = _pending_state(is_entry=False, side="sell", symbol="NVDA",
                    snapshot=copy.deepcopy(_pos), intended_qty=100, qty=100)
a.resolve_pending_orders(st)
check("dead overnight EXIT restores the position",
      st["positions"].get("NVDA", {}).get("shares_remaining") == 100,
      f"state={st['positions']}")

#  8. Exit partially filled overnight -> remainder stays open.
b = FakeBroker(); a = load_agent(b)
b.orders["oidX"] = _broker_order(status="filled", filled_qty="40")
st = _pending_state(is_entry=False, side="sell", symbol="NVDA",
                    snapshot=copy.deepcopy(_pos), intended_qty=100, qty=100)
a.resolve_pending_orders(st)
check("partial overnight exit leaves the remainder open",
      st["positions"].get("NVDA", {}).get("shares_remaining") == 60,
      f"state={st['positions']}")

#  9. Still open -> leave it alone. Booking a partial now and the rest next run
#     would double-count it.
b = FakeBroker(); a = load_agent(b)
b.orders["oidX"] = _broker_order(status="partially_filled", filled_qty="4")
st = _pending_state()
a.resolve_pending_orders(st)
check("a still-live order is left pending, not half-booked",
      "AAPL" not in st["positions"] and len(st["pending_orders"]) == 1)

# 10. The sweep must not cancel our own queued overnight order.
b = FakeBroker(); a = load_agent(b)
b.open_orders = [{"id": "oid9", "client_order_id": "JPV4-AAPL-L-ENTRY-0"},
                 {"id": "oid8", "client_order_id": "JPV4-MSFT-L-ENTRY-0"}]
a.cancel_all_pending_orders(keep_coids={"JPV4-AAPL-L-ENTRY-0"})
check("scoped cancel skips orders tracked as pending",
      b.deleted == ["/v2/orders/oid8"], str(b.deleted))

# ── EXEC-8: an unsettled entry is committed exposure ───────────────────────
#  Without this the next run sees no position in the symbol and sizes a SECOND
#  entry, doubling the intended risk the moment both fill.
print("\nEXEC-8  unsettled entries consume a slot and block their symbol")
import pandas as pd

b = FakeBroker(); a = load_agent(b)


def _df(n=80, px=100.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": px, "High": px * 1.01, "Low": px * 0.99,
                         "Close": px, "Volume": 1e6}, index=idx)


_data = {a.SPY: _df(), "AAPL": _df(), "JPM": _df()}
a.check_long_signal = lambda sym, df, spy: (True, {"price": 100.0, "atr": 3.0,
                                                   "rsi": 30.0, "disl_pct": 5.0})
a.check_short_signal = lambda sym, df, spy: (False, {})

st = {"positions": {}, "pending_orders": {"c1": {
    "symbol": "AAPL", "side": "buy", "direction": "long", "qty": 10,
    "is_entry": True, "sector": "Tech"}}}
_orders = a.process_entries(st, _data, 100000.0)
_syms = [o["symbol"] for o in _orders]
check("symbol with an unsettled entry is not entered again",
      "AAPL" not in _syms, f"orders={_syms}")
check("other symbols still trade normally", "JPM" in _syms, f"orders={_syms}")

# 11. ...and it must not be re-exited either.
b = FakeBroker(); a = load_agent(b)
st = {"positions": {"NVDA": copy.deepcopy(_pos)},
      "pending_orders": {"c1": {"symbol": "NVDA", "side": "sell",
                                "direction": "long", "qty": 100,
                                "is_entry": False}}}
check("symbol with an unsettled exit is not exited again",
      a.process_exits(st, {"NVDA": _df()}) == [])

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  ALL EXECUTION HARDENING TESTS PASSED")
