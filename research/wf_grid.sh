#!/bin/bash
# Phase 10 — generate the candidate grid ONCE over the full window.
# Each variant is a separate continuous simulation; folds are sliced afterwards
# so that no fold pays the 60-session indicator warmup.
cd /root/jp_strategy
run () { # name, env...
  n=$1; shift
  env "$@" ANCHOR_FILL=1 BT_PREFIX="wfv_$n" venv/bin/python backtest.py 2019-01-01 2026-08-29 >/dev/null 2>&1
  printf "  %-16s %s\n" "$n" "$(venv/bin/python -c "import json;r=json.load(open('wfv_$n'+'_results.json'));print(f\"ret {r['total_return_pct']:+8.2f}%  sharpe {r['sharpe']:5.2f}  mdd {r['max_drawdown_pct']:7.2f}%\")")"
}
echo "PHASE 10 — candidate grid (ANCHOR_FILL=1 throughout)"
run base_ls      LONG_ONLY=0
run ls_atr2      LONG_ONLY=0 STOP_ATR=2
run lo           LONG_ONLY=1
run lo_atr15     LONG_ONLY=1 STOP_ATR=1.5
run lo_atr2      LONG_ONLY=1 STOP_ATR=2
run lo_atr25     LONG_ONLY=1 STOP_ATR=2.5
run lo_atr3      LONG_ONLY=1 STOP_ATR=3
run lo_nsc       LONG_ONLY=1 NO_SCALE_OUT=1
run lo_trail3    LONG_ONLY=1 TRAIL_ATR=3
run lo_trail4    LONG_ONLY=1 TRAIL_ATR=4
run lo_t316      LONG_ONLY=1 T3_PCT_OVERRIDE=0.16
run lo_t320      LONG_ONLY=1 T3_PCT_OVERRIDE=0.20
run lo_atr2_nsc  LONG_ONLY=1 STOP_ATR=2 NO_SCALE_OUT=1
run lo_atr2_t316 LONG_ONLY=1 STOP_ATR=2 T3_PCT_OVERRIDE=0.16
