#!/usr/bin/env bash
# PHASE 12 — parameter robustness sweep, one-at-a-time around the frozen values.
#
# Run on the DEPLOYED configuration (V4: long-only, 2.0xATR stop, anchor fixed)
# so the question asked is "is the thing we actually deployed sitting on a
# plateau or on a spike?".
#
# Every parameter defaults to its frozen value, so the row where the swept
# value equals the frozen value must reproduce the V4 baseline exactly. That
# is used as an internal consistency check by param_robustness.py.
set -u
cd /root/jp_strategy
PY=venv/bin/python
S=2019-01-01
E=2026-08-29

export LONG_ONLY=1 ANCHOR_FILL=1 STOP_ATR=2.0

run () {   # run <tag> <ENVVAR> <value>
  local tag=$1 var=$2 val=$3
  local pfx="p12_${tag}"
  if [ -f "${pfx}_results.json" ]; then echo "  skip ${pfx}"; return; fi
  env "$var=$val" BT_PREFIX="$pfx" $PY backtest.py "$S" "$E" >/dev/null 2>&1 \
    && echo "  ok   ${pfx}  ($var=$val)" || echo "  FAIL ${pfx}"
}

echo "== signal: RSI oversold threshold (frozen 45) =="
for v in 35 40 42 45 48 50 55; do run "rsios_$v" P_RSI_OS "$v"; done

echo "== signal: minimum long dislocation vs MA20 (frozen 0.02) =="
for v in 0.005 0.01 0.015 0.02 0.025 0.03 0.04; do run "disl_$v" P_LONG_DISL "$v"; done

echo "== signal: volume capitulation multiple (frozen 1.3) =="
for v in 1.0 1.15 1.3 1.5 2.0; do run "volcap_$v" P_VOL_CAP "$v"; done

echo "== regime: SPY-vs-MA50 long gate (frozen 0.10) =="
for v in 0.03 0.05 0.10 0.15 0.25 99; do run "reglong_$v" P_REG_LONG "$v"; done

echo "== exit: T1 (frozen 0.04) =="
for v in 0.02 0.03 0.04 0.05 0.06; do run "t1_$v" P_T1 "$v"; done

echo "== exit: T2 (frozen 0.08) =="
for v in 0.06 0.07 0.08 0.10 0.12; do run "t2_$v" P_T2 "$v"; done

echo "== exit: T3 (frozen 0.12) =="
for v in 0.08 0.10 0.12 0.14 0.16 0.20; do run "t3_$v" P_T3 "$v"; done

echo "== exit: time stop days (frozen 21) =="
for v in 10 15 21 30 40 60; do run "tstop_$v" P_TIME_STOP "$v"; done

echo "== risk: ATR stop multiple (deployed 2.0) =="
for v in 1.0 1.5 2.0 2.5 3.0 4.0; do run "stopatr_$v" STOP_ATR "$v"; done

echo "== risk: sizing ATR multiple (frozen 1.5) =="
for v in 1.0 1.25 1.5 2.0 2.5; do run "sizeatr_$v" P_ATR_MULT "$v"; done

echo "DONE"
