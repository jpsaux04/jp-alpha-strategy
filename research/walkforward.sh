#!/usr/bin/env bash
# Walk-forward validation.
#   IN-SAMPLE  : 2019-01-01 -> 2023-12-31   (choose the variant here)
#   OUT-SAMPLE : 2024-01-01 -> 2026-08-29   (data starts 2023-10-01 so the
#                                            60-session warmup ends ~Jan 2024)
cd /root/jp_strategy

VARIANTS=(
  "baseline|DUMMY=1"
  "longonly|LONG_ONLY=1"
  "longonly_noscale|LONG_ONLY=1 NO_SCALE_OUT=1"
  "longonly_trail3|LONG_ONLY=1 TRAIL_ATR=3"
  "longonly_noscale_trail3|LONG_ONLY=1 NO_SCALE_OUT=1 TRAIL_ATR=3"
  "longonly_stopatr2|LONG_ONLY=1 STOP_ATR=2"
  "longonly_noscale_trail3_satr|LONG_ONLY=1 NO_SCALE_OUT=1 TRAIL_ATR=3 STOP_ATR=2"
)

phase () {  # phase <tag> <start> <end>
  local tag="$1" s="$2" e="$3"
  printf "\n===== %s : %s -> %s =====\n" "$tag" "$s" "$e"
  printf "%-32s %10s %7s %9s %6s %6s\n" VARIANT RETURN SHARPE MAXDD PF TRADES
  printf '%.0s-' {1..76}; echo
  for v in "${VARIANTS[@]}"; do
    local name="${v%%|*}" envs="${v#*|}"
    env $envs BT_PREFIX="wf_${tag}_${name}" ./venv/bin/python backtest.py "$s" "$e" >/dev/null 2>&1
    ./venv/bin/python3 -c "
import json
r=json.load(open('wf_${tag}_${name}_results.json'))
print(f\"{'$name':32s} {r['total_return_pct']:>9.1f}% {str(r['sharpe']):>7} {r['max_drawdown_pct']:>8.1f}% {str(r['profit_factor']):>6} {r['n_trades']:>6}\")
"
  done
}

phase IS  2019-01-01 2024-01-01
phase OOS 2023-10-01 2026-08-29

echo
echo "--- OOS equity curve actually starts on: ---"
head -2 wf_OOS_longonly_equity.csv | tail -1
