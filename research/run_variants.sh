#!/usr/bin/env bash
# Exit-design ablation matrix. Each variant is a separate full backtest run.
cd /root/jp_strategy
S=2019-01-01; E=2026-08-29
run () {  # run <name> <env assignments...>
  local name="$1"; shift
  env "$@" BT_PREFIX="v_$name" ./venv/bin/python backtest.py $S $E >/dev/null 2>&1
  ./venv/bin/python3 -c "
import json
r=json.load(open('v_${name}_results.json'))
print(f\"{'$name':34s} {r['total_return_pct']:>9.1f}% {str(r['sharpe']):>7} {r['max_drawdown_pct']:>8.1f}% {str(r['profit_factor']):>6} {r['n_trades']:>6} {str(r['win_rate_pct']):>6}\"  )
"
}
printf "%-34s %10s %7s %9s %6s %6s %6s\n" VARIANT RETURN SHARPE MAXDD PF TRADES WIN%
printf '%.0s-' {1..82}; echo
run baseline                     DUMMY=1
run longonly                     LONG_ONLY=1
run longonly_noscale             LONG_ONLY=1 NO_SCALE_OUT=1
run longonly_trail3              LONG_ONLY=1 TRAIL_ATR=3
run longonly_noscale_trail3      LONG_ONLY=1 NO_SCALE_OUT=1 TRAIL_ATR=3
run longonly_stopatr2            LONG_ONLY=1 STOP_ATR=2
run longonly_noscale_trail3_satr LONG_ONLY=1 NO_SCALE_OUT=1 TRAIL_ATR=3 STOP_ATR=2
run longonly_t3_25pct            LONG_ONLY=1 T3_PCT_OVERRIDE=0.25
run longonly_noscale_t3_25       LONG_ONLY=1 NO_SCALE_OUT=1 T3_PCT_OVERRIDE=0.25
echo
echo "SPY buy-and-hold same window: +229% (Sharpe 0.93, MaxDD -33.7%)"
