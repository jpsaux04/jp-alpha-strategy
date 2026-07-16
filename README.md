# JP Alpha Strategy v3

A systematic, bidirectional **mean-reversion swing trading** agent that trades a
42-stock universe on Alpaca paper trading. Long oversold names, short overbought
names, with ATR-based position sizing and tiered profit-taking.

## Strategy

**Long signal** (oversold reversion):
- Wilder RSI < 45
- Price ≥ 2% below its 20-day MA
- Volume exhaustion (capitulation spike or 3-day dry-up)
- Bullish intraday close (upper half of the day's range)
- Regime filter: SPY not > 10% above its 50-day MA

**Short signal** (overbought reversion) — the exact mirror image.

**Exits** (both directions):
- T1 +4% → trim 25%
- T2 +8% → trim 25%
- T3 +12% → close remainder
- Stop loss: 8% adverse move
- Time stop: 21 days without hitting T1
- Post-T1 stop: 30 days after T1 without T2

**Sizing:** equal risk — 1% of portfolio per 1.5-ATR move.

## Backtest (2020–2025)
- CAGR 8.1% | Sharpe 0.66 | Max DD -10.9% | Profit Factor 1.53 | 249 trades
- Walk-forward validated: out-of-sample (PF 1.73) beat in-sample (PF 1.28)
- Monte Carlo: 99.4% of 500 simulations profitable

## Files
| File | Purpose |
|------|---------|
| `jp_agent.py` | The strategy engine — signals, risk, execution |
| `status.py` | Live P&L dashboard |
| `equity_curve.csv` | Daily portfolio value history (generated) |
| `trade_log.csv` | Blotter of every filled order (generated) |

## Setup
```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in your Alpaca keys
venv/bin/python jp_agent.py
```

## Schedule
Runs daily at 4:30 PM ET via cron:
```
TZ=America/New_York
30 16 * * 1-5 /path/to/venv/bin/python /path/to/jp_agent.py >> logs/cron.log 2>&1
```
