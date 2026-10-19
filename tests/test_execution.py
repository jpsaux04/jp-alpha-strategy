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

# ── EXEC-9: the consumers of the pending state ─────────────────────────────
#  EXEC-8 changed what "submitted but not filled" means. These three readers
#  were never told, and all three published false HIGH alerts on the first
#  real overnight order (NKE, 2026-10-16). Each fix suppresses something, so
#  each gets a paired test that the real condition still fires.
print()
print("== EXEC-9: heartbeat / monitor / dashboard understand pending ==")

import json as _j9
import importlib as _il9

_A9 = sys.modules.get("jp_agent") or _il9.import_module("jp_agent")


def _hb(results, resolved=(), state=None):
    """Call write_heartbeat and read back what it wrote."""
    import tempfile as _tf, pathlib as _pl
    _d = _pl.Path(_tf.mkdtemp())
    _old = _A9.HEARTBEAT_JSON
    _A9.HEARTBEAT_JSON = _d / "hb.json"
    try:
        _A9.write_heartbeat({"portfolio_value": 100.0}, results,
                            "RUN_OK", resolved=resolved, state=state)
        return _j9.loads((_d / "hb.json").read_text())
    finally:
        _A9.HEARTBEAT_JSON = _old


_PEND_R = {"symbol": "NKE", "side": "sell", "qty": 0, "requested_qty": 317,
           "status": "PENDING_accepted"}
_FILL_R = {"symbol": "JPM", "side": "buy", "qty": 10, "requested_qty": 10,
           "status": "filled"}
_DEAD_R = {"symbol": "BAC", "side": "buy", "qty": 0, "requested_qty": 5,
           "status": "NOFILL_rejected"}

h = _hb([_PEND_R])
check("queued order is not counted as rejected",
      h["n_orders_rejected"] == 0, f"rejected={h['n_orders_rejected']}")
check("queued order is not counted as an error",
      h["n_errors"] == 0, f"n_errors={h['n_errors']}")
check("queued order is counted as pending",
      h["n_orders_pending"] == 1, f"pending={h['n_orders_pending']}")

#  PAIRED NEGATIVE: a real rejection must still be an error even when a
#  pending order is in the same batch. This is the assertion that stops the
#  fix from simply switching the alert off.
h = _hb([_PEND_R, _DEAD_R, _FILL_R])
check("a REAL rejection still counts as rejected alongside a pending one",
      h["n_orders_rejected"] == 1, f"rejected={h['n_orders_rejected']}")
check("a REAL rejection still raises n_errors alongside a pending one",
      h["n_errors"] == 1, f"n_errors={h['n_errors']}")
check("mixed batch counts the fill",
      h["n_orders_filled"] == 1 and h["n_orders_submitted"] == 3,
      f"filled={h['n_orders_filled']} submitted={h['n_orders_submitted']}")
check("submitted == filled + pending + rejected",
      h["n_orders_submitted"] == (h["n_orders_filled"] + h["n_orders_pending"]
                                  + h["n_orders_rejected"]), str(h))

#  An order a PREVIOUS run queued and this run settled is a real fill.
h = _hb([], resolved=[{"symbol": "NKE", "qty": 317, "status": "filled"}],
        state={"pending_orders": {}})
check("overnight fill resolved from pending is reported",
      h["n_resolved_filled"] == 1, f"{h}")
check("outstanding pending count is reported",
      _hb([], state={"pending_orders": {"c1": {"symbol": "NKE"}}}
          )["n_pending_open"] == 1)

# ── monitor: in-flight symbol is not an orphan ─────────────────────────────
_mon9 = _il9.import_module("monitor")
_ST9 = {"positions": {"JPM": {}},
        "pending_orders": {"JPV4-NKE-L-T3HIT-0":
                           {"symbol": "NKE", "is_entry": False}}}
_BK9 = [{"symbol": "JPM"}, {"symbol": "NKE"}]

_al = _mon9.check_divergence(_BK9, _ST9)
_codes = [a.code for a in _al]
check("queued exit does not raise ORPHAN_POSITIONS",
      "ORPHAN_POSITIONS" not in _codes, f"codes={_codes}")
check("queued exit is still reported, as INFO",
      "SETTLING_POSITIONS" in _codes, f"codes={_codes}")
check("the settling alert is not HIGH",
      all(a.severity != "HIGH" for a in _al if a.code == "SETTLING_POSITIONS"))

#  PAIRED NEGATIVE: a genuine orphan next to a pending order must still fire.
_al = _mon9.check_divergence(_BK9 + [{"symbol": "TSLA"}], _ST9)
_codes = [a.code for a in _al]
check("a GENUINE orphan still alarms when a pending order exists",
      "ORPHAN_POSITIONS" in _codes, f"codes={_codes}")
_o = [a for a in _al if a.code == "ORPHAN_POSITIONS"][0]
check("the genuine orphan alert names TSLA and not NKE",
      "TSLA" in _o.message and "NKE" not in _o.message, _o.message)
check("ghost detection is untouched by the fix",
      "GHOST_POSITIONS" in [a.code for a in
                            _mon9.check_divergence([], {"positions": {"X": {}}})])

# ── dashboard: settling position is not an unmanaged orphan ────────────────
_bd9 = sys.modules.get("build_dashboard") or _il9.import_module("build_dashboard")
_SNAP = {"direction": "long", "entry_price": 33.78, "fill_price": 33.79,
         "atr_at_entry": 1.138, "stop_atr_mult": 1.5, "shares_total": 635,
         "shares_remaining": 317, "t1_hit": True, "t2_hit": True,
         "entry_date": "2026-10-02"}
_APOS = [{"symbol": "NKE", "qty": "317", "avg_entry_price": "33.79",
          "current_price": "37.80", "unrealized_pl": "1271.0",
          "unrealized_plpc": "0.118"}]
_S9 = {"positions": {},
       "pending_orders": {"c1": {"symbol": "NKE", "is_entry": False,
                                 "snapshot": _SNAP}}}
_r = _bd9.enrich_positions(_APOS, _S9)[0]
check("settling position is NOT flagged unmanaged",
      _r["unmanaged"] is False, f"unmanaged={_r['unmanaged']}")
check("settling position is labelled as a settling exit",
      _r["settling"] == "exit", f"settling={_r['settling']!r}")
check("settling position keeps the stop from its snapshot",
      _r["stop"] is not None
      and abs(_r["stop"] - (33.79 - 1.5 * 1.138)) < 0.01,
      f"stop={_r['stop']}")
check("settling position keeps its T1/T2 flags",
      _r["t1"] is True and _r["t2"] is True)

#  PAIRED NEGATIVE: a true broker-only position must still read unmanaged.
_r2 = _bd9.enrich_positions(
    _APOS + [{"symbol": "TSLA", "qty": "10", "avg_entry_price": "100",
              "current_price": "101", "unrealized_pl": "10",
              "unrealized_plpc": "0.01"}], _S9)
_t = [x for x in _r2 if x["symbol"] == "TSLA"][0]
check("a GENUINE orphan is still flagged unmanaged on the dashboard",
      _t["unmanaged"] is True and _t["stop"] is None,
      f"unmanaged={_t['unmanaged']} stop={_t['stop']}")
check("the orphan banner would name TSLA only",
      [x["symbol"] for x in _r2 if x["unmanaged"]] == ["TSLA"],
      f"{[x['symbol'] for x in _r2 if x['unmanaged']]}")

#  A queued ENTRY has no snapshot: still not an orphan, but no stop is known.
_r3 = _bd9.enrich_positions(
    _APOS, {"positions": {},
            "pending_orders": {"c1": {"symbol": "NKE", "is_entry": True}}})[0]
check("queued entry is not an orphan and is labelled as such",
      _r3["unmanaged"] is False and _r3["settling"] == "entry",
      f"{_r3['unmanaged']} {_r3['settling']!r}")

#  The orphan BANNER and the table BADGE are two renderings of one idea. They
#  read two different fields until EXEC-9, so fixing one left the other
#  wrong. Keep them on the same field.
import pathlib as _pl9
_src9 = _pl9.Path(_bd9.__file__).with_suffix(".py").read_text()
_blk9 = _src9[_src9.index("    # ── Positions table ──"):]
_blk9 = _blk9[:_blk9.index("_sv = f\"")]
check("positions badge is driven by `unmanaged`",
      'r["unmanaged"]' in _blk9, "badge no longer reads r['unmanaged']")
check("positions badge no longer branches on `in_state`",
      'r["in_state"]' not in _blk9,
      "badge is back on in_state — it will disagree with the orphan banner")
check("a settling exit gets its own, non-red badge",
      "EXIT QUEUED" in _blk9 and "var(--amb)" in _blk9)
check("the red UNMANAGED badge still exists for real orphans",
      "UNMANAGED</span>" in _blk9)

#  EXEC-10. The poller patches cells by symbol; a symbol can LEAVE the payload
#  when a queued exit fills at the open. Behaviour is asserted for real in
#  tests/test_live_poller.js, against a DOM stub; these are the cheap
#  structural guards that catch the hooks being removed from the renderer.
_js9 = _src9[_src9.index("/* Live poller"):]
_js9 = _js9[:_js9.index("</script>")]
check("the poller reconciles the row set, not only the cells",
      "tr[data-row-sym]" in _js9,
      "poller no longer enumerates rows — a closed position will render frozen")
check("a vanished row is marked, not silently left live",
      "row-closed" in _js9 and "CLOSED" in _js9)
check("the poller reads the broker's status, not status_at_submit",
      "d.pending" in _js9 and "'filled'" in _js9)
check("a fill is only claimed when the broker says filled",
      _js9.count("status==='filled'") >= 1
      and "status==='accepted'" not in _js9,
      "FILLED must be gated on the broker's own status string")

_rowblk9 = _src9[_src9.index("        prows += f"):_src9.index("    if not prows:")]
check("position rows carry data-row-sym for the poller to find",
      'data-row-sym=' in _rowblk9, "rows are anonymous again")
check("the badge is addressable so a stale one can be corrected",
      'data-f="tag"' in _rowblk9)
check("the risk cell is addressable so a closed row stops claiming risk",
      'data-f="risk"' in _rowblk9)

#  The /api/live contract the poller depends on.
_ls9 = _pl9.Path(_bd9.__file__).parent.joinpath("live_server.py").read_text()
check("/api/live publishes pending orders",
      '"pending": pending' in _ls9, "payload no longer carries pending[]")
check("/api/live asks the broker for each pending order's real status",
      "fetch_order_status" in _ls9)
check("a broker read failure never fabricates a fill",
      'o.get("status") or r.get("status_at_submit")' in _ls9,
      "status must fall back to the stored value, never to filled")

#  EXEC-11. The published snapshot strips every live-polling block, so the
#  poller's correction (EXEC-10) does not exist on the one copy that gets
#  shared. The banner therefore has to be TRUE AS RENDERED. These exercise the
#  builder directly: pure function, no network, no credentials.
_PB = {"k1": {"symbol": "NKE", "side": "sell", "qty": 317, "is_entry": False,
              "order_id": "oid1", "status_at_submit": "accepted"}}
_FILLED = {"oid1": {"status": "filled", "filled_qty": "317",
                    "filled_avg_price": "37.115678",
                    "filled_at": "2026-10-19T13:35:55.928953Z"}}

_b = _bd9.pending_banner(_PB, _FILLED)
check("a filled order is not announced as queued",
      "FILLED AT THE BROKER" in _b
      and "held by the broker until the next open" not in _b,
      "the banner still claims the order is waiting for the open")
check("the banner states when and at what price it filled",
      "09:35 ET" in _b and "37.12" in _b, _b[:200])
check("a filled order is not counted as unsettled",
      "UNSETTLED" not in _b)

#  NEGATIVE: the broker could not be read. Fall back to the stored status and
#  keep calling it queued -- a banner wrongly saying "queued" is stale; one
#  wrongly saying "filled" misstates where the money is.
for _lbl, _st in (("no status map", {}), ("empty status", {"oid1": {}}),
                  ("still accepted", {"oid1": {"status": "accepted"}})):
    _q = _bd9.pending_banner(_PB, _st)
    check(f"an unconfirmed order stays queued ({_lbl})",
          "QUEUED AT THE BROKER" in _q and "FILLED AT THE BROKER" not in _q,
          _q[:160])

#  A mixed batch must count only what is actually still open, and must not
#  bury the fill.
_MIX = dict(_PB)
_MIX["k2"] = {"symbol": "SBUX", "side": "buy", "qty": 100, "is_entry": True,
              "order_id": "oid2", "status_at_submit": "accepted"}
_m = _bd9.pending_banner(_MIX, _FILLED)
check("a mixed batch counts only the unsettled orders",
      "1 UNSETTLED ORDER(S)" in _m, _m[:160])
check("a mixed batch still reports the one that filled",
      "already FILLED" in _m and "filled 09:35 ET" in _m)

#  A partial fill is still open at the broker.
_p = _bd9.pending_banner(_PB, {"oid1": {"status": "partially_filled",
                                        "filled_qty": "100"}})
check("a partial fill counts as unsettled",
      "1 UNSETTLED ORDER(S)" in _p and "partially filled" in _p, _p[:200])

check("no pending orders renders no banner",
      _bd9.pending_banner({}, _FILLED) == "" and _bd9.pending_banner(None) == "")

check("the ET formatter refuses unparseable input instead of guessing",
      _bd9._et_hhmm(None) == "" and _bd9._et_hhmm("not-a-time") == ""
      and _bd9._et_hhmm("2026-10-19T13:35:55.928953Z") == "09:35 ET",
      repr(_bd9._et_hhmm("2026-10-19T13:35:55.928953Z")))

#  The wiring: render must be handed the broker's view, and main must fetch it.
check("render builds the banner from the resolved status",
      "pending_banner(pending, pending_status)" in _src9,
      "render no longer passes the broker status to the banner")
check("the build resolves pending status from the broker",
      "pending_status=fetch_order_status(" in _src9)
check("live_server shares the one ET formatter",
      "_et_hhmm" in _ls9 and "strftime(\"%H:%M ET\")" not in _ls9,
      "a second private copy of the ET formatting is back")

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAILED: {f}")
    sys.exit(1)
print("  ALL EXECUTION HARDENING TESTS PASSED")
