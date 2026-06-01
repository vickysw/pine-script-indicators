# History Data

Real market data used for backtesting all indicators.

## Sources

| Source | Timeframes | Market |
|--------|-----------|--------|
| OANDA | 5m, 15m, 30m, 60m, 240m | XAU/USD (Gold) |
| NSE Daily | 5m | NIFTY 50 |
| TradingView export | 5m, 15m, 1h | XAU/USD (24 months) |

## Files

### Root — Backtest trade logs
| File | Description |
|------|-------------|
| `s2_v2_trades.csv` | S2 v2 all trades (Normal mode) |
| `s2_v2_trades_5M.csv` | S2 v2 5M gate trades |
| `s2_v2_trades_15M.csv` | S2 v2 15M gate trades |
| `s2_v2_trades_30M.csv` | S2 v2 30M gate trades |
| `s2_v2_sim_results.csv` | S2 v2 full simulation results |
| `s2_v3_sim_results.csv` | S2 v3 distance filter results |
| `trades_24m_Normal.csv` | 24-month Normal mode trades |
| `trades_24m_5M.csv` | 24-month 5M mode trades |
| `trades_24m_MTF.csv` | 24-month MTF mode trades |
| `trades_BASELINE.csv` | Baseline comparison trades |
| `trades_OPTIMAL.csv` | Optimal parameter trades |
| `manku_signals.csv` | Raw signal detections |
| `manku_summary.csv` | Signal summary stats |
| `OANDA_XAUUSD, 5_30th.csv` | OANDA 5m data (30 days) |

### tvHistoryData/ — Raw OHLC price data
| File | Description |
|------|-------------|
| `gold_24m.csv` | XAU/USD 5m — 24 months |
| `gold_24m_15m.csv` | XAU/USD 15m — 24 months |
| `gold_24m_1h.csv` | XAU/USD 1h — 24 months |
| `gold_6m.csv` | XAU/USD 5m — 6 months |
| `OANDA_XAUUSD, 5.csv` | OANDA 5m data |
| `OANDA_XAUUSD, 15.csv` | OANDA 15m data |
| `OANDA_XAUUSD, 30.csv` | OANDA 30m data |
| `OANDA_XAUUSD, 60.csv` | OANDA 1h data |
| `OANDA_XAUUSD, 60-1y.csv` | OANDA 1h — 1 year |
| `OANDA_XAUUSD, 240_bulkData.csv` | OANDA 4h bulk |
| `NSE_DLY_NIFTY, 5.csv` | NIFTY 5m data |
| `fetch_gold.py` | Script to fetch OANDA data |
| `chart_gold.py` | Script to chart/visualize data |
