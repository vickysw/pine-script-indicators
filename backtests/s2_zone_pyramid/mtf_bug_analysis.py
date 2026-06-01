"""
MTF Bug Fix Analysis — Manku S2 Zone-Pyramid v2
Compares current (buggy) vs fixed 15M gate logic
Shows: gate fire counts, additional signals, new signals post May-27
"""

import pandas as pd
import numpy as np
from datetime import timezone

# ── Load data ────────────────────────────────────────────────────────────────
df5  = pd.read_csv(r"D:\Trade\OANDA_XAUUSD, 5_30th.csv")
df15 = pd.read_csv(r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 15.csv")
df60 = pd.read_csv(r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 60.csv")

# Convert timestamps
for df in [df5, df15, df60]:
    df['dt'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('dt', inplace=True)
    df.sort_index(inplace=True)

print(f"5M  : {df5.index[0]} to {df5.index[-1]}  ({len(df5)} bars)")
print(f"15M : {df15.index[0]} to {df15.index[-1]}  ({len(df15)} bars)")
print(f"60M : {df60.index[0]} to {df60.index[-1]}  ({len(df60)} bars)")

# ── EMA helper ───────────────────────────────────────────────────────────────
def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

# ── 15M Computations ─────────────────────────────────────────────────────────
d = df15.copy()
d['ema9']  = ema(d['close'], 9)
d['ema15'] = ema(d['close'], 15)
d['hlc3']  = (d['high'] + d['low'] + d['close']) / 3

# Simple daily VWAP (resets at UTC midnight)
d['date']  = d.index.date
d['tp_vol'] = d['hlc3']   # OANDA data has no volume; use hlc3 as proxy (flat vol)
d['cum_tp'] = d.groupby('date')['hlc3'].cumsum()
d['cum_n']  = d.groupby('date').cumcount() + 1
d['vwap']   = d['cum_tp'] / d['cum_n']   # price-only VWAP (no volume available)

# Body / range for each bar
d['body'] = (d['close'] - d['open']).abs()
d['rng']  = (d['high'] - d['low']).clip(lower=0.01)
d['bp']   = d['body'] / d['rng']

# ── Gate logic per 15M bar i (looking at bar-1 = prev, bar-2 = prev-prev) ──
results = []

for i in range(3, len(d)):
    row   = d.iloc[i]       # current bar (reference point, like 5M bar seeing 15M data)
    b1    = d.iloc[i-1]     # bar[1] — last completed 15M
    b2    = d.iloc[i-2]     # bar[2]
    b3    = d.iloc[i-3]     # bar[3] — needed for fixed be2

    # Confluence (sell): EMA9 > close OR VWAP > close (on 15M)
    conf_sell = (row['ema9'] > row['close']) or (row['vwap'] > row['close'])
    conf_buy  = (row['ema9'] < row['close']) or (row['vwap'] < row['close'])

    # ── SELL patterns ────────────────────────────────────────────────────────
    # bar-1
    sr1    = (b1['close'] < b1['open']) and (b1['bp'] > 0.55)

    # bar-1 engulf — BUGGY: b1.close < b1.low  (impossible, always False)
    be1_bug = (b1['close'] < b1['open']) and (b1['close'] < b1['low'])        # always False
    # bar-1 engulf — FIXED: b1.close < b2.low  (close[1] < low[2])
    be1_fix = (b1['close'] < b1['open']) and (b1['close'] < b2['low'])

    # bar-2 body% — BUGGY: uses bar-1 range
    bp2_bug = d.iloc[i-2]['body'] / b1['rng']
    # bar-2 body% — FIXED: uses bar-2 range
    bp2_fix = b2['bp']

    sr2_bug = (b2['close'] < b2['open']) and (bp2_bug > 0.55)
    sr2_fix = (b2['close'] < b2['open']) and (bp2_fix > 0.55)

    # bar-2 engulf — BUGGY: b2.close < b1.low  (checks newer bar's low — wrong direction)
    be2_bug = (b2['close'] < b2['open']) and (b2['close'] < b1['low'])
    # bar-2 engulf — FIXED: b2.close < b3.low  (close[2] < low[3])
    be2_fix = (b2['close'] < b2['open']) and (b2['close'] < b3['low'])

    gate_bug = be1_bug or sr1 or be2_bug or sr2_bug
    gate_fix = be1_fix or sr1 or be2_fix or sr2_fix

    sell_fired_bug = conf_sell and gate_bug
    sell_fired_fix = conf_sell and gate_fix

    # Which component fired
    if sell_fired_fix and not sell_fired_bug:
        new_comp = ('be1_fix' if be1_fix else '') + ('|be2_fix' if be2_fix else '') + ('|sr2_fix' if (sr2_fix and not sr2_bug) else '')
    else:
        new_comp = ''

    # ── BUY patterns ─────────────────────────────────────────────────────────
    # bar-1 engulf buy — BUGGY: b1.close > b1.high (impossible, always False)
    buy_be1_bug = (b1['close'] > b1['open']) and (b1['close'] > b1['high'])   # always False
    # FIXED: b1.close > b2.high
    buy_be1_fix = (b1['close'] > b1['open']) and (b1['close'] > b2['high'])

    buy_sr1     = (b1['close'] > b1['open']) and (b1['bp'] > 0.55)

    buy_gate_bug = buy_be1_bug or buy_sr1
    buy_gate_fix = buy_be1_fix or buy_sr1

    buy_fired_bug = conf_buy and buy_gate_bug
    buy_fired_fix = conf_buy and buy_gate_fix

    results.append({
        'dt': row.name,
        # SELL
        'sr1': sr1, 'be1_bug': be1_bug, 'be1_fix': be1_fix,
        'sr2_bug': sr2_bug, 'sr2_fix': sr2_fix,
        'be2_bug': be2_bug, 'be2_fix': be2_fix,
        'sell_gate_bug': gate_bug, 'sell_gate_fix': gate_fix,
        'conf_sell': conf_sell,
        'sell_fired_bug': sell_fired_bug, 'sell_fired_fix': sell_fired_fix,
        'new_sell_component': new_comp,
        # BUY
        'buy_be1_bug': buy_be1_bug, 'buy_be1_fix': buy_be1_fix,
        'buy_sr1': buy_sr1,
        'buy_gate_bug': buy_gate_bug, 'buy_gate_fix': buy_gate_fix,
        'conf_buy': conf_buy,
        'buy_fired_bug': buy_fired_bug, 'buy_fired_fix': buy_fired_fix,
    })

res = pd.DataFrame(results).set_index('dt')

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("15M GATE FIRE ANALYSIS")
print("="*60)

# Sell gate
sb  = res['sell_fired_bug'].sum()
sf  = res['sell_fired_fix'].sum()
new_sell = res[res['sell_fired_fix'] & ~res['sell_fired_bug']]

print(f"\nSELL gate fires:")
print(f"  Current (buggy) : {sb}")
print(f"  Fixed           : {sf}  (+{sf-sb} additional)")
print(f"  Improvement     : +{(sf-sb)/sb*100:.1f}%" if sb > 0 else "  Improvement: N/A")

# Buy gate
bb  = res['buy_fired_bug'].sum()
bf  = res['buy_fired_fix'].sum()
new_buy = res[res['buy_fired_fix'] & ~res['buy_fired_bug']]

print(f"\nBUY gate fires:")
print(f"  Current (buggy) : {bb}")
print(f"  Fixed           : {bf}  (+{bf-bb} additional)")
print(f"  Improvement     : +{(bf-bb)/bb*100:.1f}%" if bb > 0 else "  Improvement: N/A")

# What components drive the new sells
print(f"\nNew SELL gates broken down by component:")
print(f"  be1_fix only (bar-1 engulf)  : {(new_sell['be1_fix'] & ~new_sell['be2_fix']).sum()}")
print(f"  be2_fix only (bar-2 engulf)  : {(new_sell['be2_fix'] & ~new_sell['be1_fix']).sum()}")
print(f"  sr2_fix only (bar-2 SR fixed): {(new_sell['sr2_fix'] & ~new_sell['be1_fix'] & ~new_sell['be2_fix']).sum()}")
print(f"  combination                  : {(new_sell['be1_fix'] & new_sell['be2_fix']).sum()}")

# New BUY gate driver
print(f"\nNew BUY gates — all from buy_be1_fix (close[1] > high[2]): {new_buy['buy_be1_fix'].sum()}")

# Monthly breakdown
print("\nMonthly additional SELL gate fires (fix vs buggy):")
monthly = res.resample('ME').agg(
    sell_bug=('sell_fired_bug','sum'),
    sell_fix=('sell_fired_fix','sum'),
    buy_bug=('buy_fired_bug','sum'),
    buy_fix=('buy_fired_fix','sum')
)
monthly['sell_extra'] = monthly['sell_fix'] - monthly['sell_bug']
monthly['buy_extra']  = monthly['buy_fix']  - monthly['buy_bug']
print(monthly[['sell_bug','sell_fix','sell_extra','buy_bug','buy_fix','buy_extra']].to_string())

# Overlap: new gate fires vs existing Normal 5M signals
print("\n" + "="*60)
print("NEW 5M DATA SIGNALS CHECK (post May-27)")
print("="*60)

# Load existing backtest CSV
bt = pd.read_csv(r"D:\Trade\s2_v2_trades_5M.csv")
bt['entry_time'] = pd.to_datetime(bt['entry_time'], utc=True)
last_bt = bt['entry_time'].max()
print(f"Last backtest signal: {last_bt}")

# Check if any 5M bars after last_bt had potential signal conditions
# (simplified: show if 5M close is near recent zones + HTF is trending)
df5_new = df5[df5.index > last_bt].copy()
print(f"New 5M bars after last signal: {len(df5_new)}")

if len(df5_new) > 0:
    # HTF bias from HTF MA column (htf_ma) vs 60M close
    # Use 60M close merged into 5M
    df60_close = df60['close'].resample('5min').ffill()
    htf_ma_5m  = df5['HTF MA'].copy()
    # Merge 60M close into 5M
    df5['htf_close_60'] = df60_close.reindex(df5.index, method='ffill')
    df5['htf_bear'] = df5['htf_close_60'] < df5['HTF MA']
    df5['htf_bull'] = df5['htf_close_60'] > df5['HTF MA']

    df5_new = df5[df5.index > last_bt].copy()
    print(f"\nHTF bias in new data:")
    print(f"  BEAR bars: {df5_new['htf_bear'].sum()}")
    print(f"  BULL bars: {df5_new['htf_bull'].sum()}")
    print(f"  NEUTRAL  : {(~df5_new['htf_bear'] & ~df5_new['htf_bull']).sum()}")
    print(f"\nNew data date range: {df5_new.index[0]} to {df5_new.index[-1]}")
    print("(Full zone simulation needed for exact signal detection)")
else:
    print("No new bars after last backtest signal date.")

# Key new gate fire examples
print("\n" + "="*60)
print("SAMPLE NEW SELL GATE FIRES (fix adds these)")
print("="*60)
print(new_sell[['sr1','be1_fix','be2_fix','sr2_fix','sr2_bug','conf_sell']].head(15).to_string())
