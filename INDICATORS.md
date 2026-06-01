# Indicator Specifications

Detailed specs for each indicator. No source code — available on request.

---

## 1. S2 Zone Pyramid v3 — XAU/USD

**Platform:** TradingView (Pine Script v6)  
**Market:** XAU/USD (Gold), any HTF-compatible pair  
**Timeframe:** 5m chart, 60m HTF reference

### Logic Overview
1. Detects key supply/demand zones using swing high/low structure
2. Applies HTF (60M) EMA50 dead zone filter — zones within 10 pts of EMA50 are skipped
3. Pyramid entry: multiple entries within zone at fixed step intervals
4. Auto SL: placed above/below zone boundary
5. Auto targets: T1 at 1.5R (50% close, SL → entry), T2 at 2.0R (close remaining)

### Key Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `htf_dist_min` | 10 pts | Min distance from 60M EMA50 |
| `key_step` | 5 pts | Zone pyramid step size |
| `sl_skip` | 2 pts | SL buffer beyond zone |
| `risk_pct` | 1% | Risk per trade |
| `point_val` | $1/pt | XAU/USD point value |

### HTF Slope Visual
- EMA line **BRIGHT** = price above EMA (bullish bias)
- EMA line **DIM** = price below EMA (bearish bias)
- Used for visual confluence — not a hard filter by default

### Alert Format
```
S2PASS | XAUUSD | SHORT | Entry:4450.26 SL:4451.46 Lots:0.50 [Normal]
```

---

## 2. S2 Zone Pyramid v3 — NIFTY Edition

**Platform:** TradingView (Pine Script v6)  
**Market:** NSE NIFTY 50  
**Timeframe:** 5m chart

### Calibration vs XAU version
| Parameter | XAU/USD | NIFTY |
|-----------|---------|-------|
| `htf_dist_min` | 10 pts | 35 pts |
| `key_step` | 5 pts | 100 pts |
| `key_range` | 10 pts | 50 pts |
| `sl_skip` | 2 pts | 15–25 pts |
| `point_val` | $1/pt | Rs.50/lot |
| Session filter | 24h | 09:15–15:30 IST |

---

## 3. ICT ChoCH Signal

**Platform:** TradingView (Pine Script v6)  
**Market:** Any (XAU/USD primary)  
**Timeframe:** 5m

### Logic Overview
1. Tracks internal swing structure (HH, HL, LH, LL)
2. Detects Break of Structure (BOS) → confirms trend
3. ChoCH fires when structure flips against established trend
4. Entry at ChoCH candle close
5. SL above/below the swing that caused ChoCH
6. Auto lot sizing based on SL distance

### Alert Format
```
CHOCHPASS | XAUUSD | SHORT | Entry:2345.67 SL:2351.20 Lots:0.021
```

---

## 4. Manku Top5 Indicator

**Platform:** TradingView (Pine Script v6)  
**Market:** XAU/USD primary  

ICT-based composite indicator combining 5 confluence factors:
1. HTF trend alignment
2. Supply/Demand zone proximity
3. EMA structure
4. Session timing
5. Volume/momentum confirmation

> Source code locked — not shared publicly.

---

## Backtest Python Scripts

All indicators were backtested using custom Python scripts against real OANDA/TV export data.

### S2 Zone Pyramid backtests (`backtests/s2_zone_pyramid/`)
| File | Purpose |
|------|---------|
| `s2_v2_backtest.py` | Full v2 backtest — all 3 modes |
| `s2_fullbt.py` | v3 full backtest with distance filter |
| `s2_backtest_sim.py` | Simulation with parameter sweep |
| `s2_exit_sim.py` | Exit strategy comparison (T1/T2 vs trail) |
| `mtf_backtest.py` | MTF gate analysis |
| `mtf_bug_analysis.py` | MTF logic bug investigation |
| `filter_optimize.py` | HTF distance filter optimization |
| `manku_backtest.py` | Combined Manku signal backtest |

### ICT ChoCH backtests (`backtests/ict_choch/`)
| File | Purpose |
|------|---------|
| `ict_v2_backtest.py` | ChoCH v2 baseline backtest |
| `ict_v3_mss_backtest.py` | v3 with MSS (Market Structure Shift) filter |
| `ict_choch_optimized.py` | Optimized parameter version |
| `ict_choch_confluence.py` | Multi-factor confluence backtest |
| `ict_choch_tier3.py` | Tier 3 (high-conviction only) filter |
| `ict_choch_pivot_opt.py` | Pivot-based entry optimization |
| `ict_choch_sideways.py` | Sideways market filter analysis |
| `ict_choch_isolate.py` | Signal isolation and classification |
