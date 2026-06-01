"""MTF mode 24-month backtest — Normal vs 5M vs MTF comparison."""
exec(open('s2_v2_backtest.py', encoding='utf-8').read())
import numpy as np, pandas as pd

print('Loading 24M data...')
df5  = load_csv(r'D:\Trade\tvHistoryData\gold_24m.csv')
df15 = load_csv(r'D:\Trade\tvHistoryData\gold_24m_15m.csv')
df1h = load_csv(r'D:\Trade\tvHistoryData\gold_24m_1h.csv')
start = max(df5.index[0], df15.index[0], df1h.index[0])
end   = min(df5.index[-1], df15.index[-1], df1h.index[-1])
df5c=df5[start:end]; df15c=df15[start:end]; df1hc=df1h[start:end]
months = round((end - start).days / 30.5, 1)
print(f'Period : {df5c.index[0].date()} to {df5c.index[-1].date()}  ({months} months)')
print(f'5M bars: {len(df5c):,}  |  15M bars: {len(df15c):,}  |  1H bars: {len(df1hc):,}')

# Enable all 3 modes for signal detection
CFG['SHOW_NORMAL'] = True
CFG['SHOW_5M']     = True
CFG['SHOW_MTF']    = True

print('\nDetecting signals (all 3 modes)...')
sig_df, df5p = detect_signals(df5c, df15c, df1hc)

n_norm = len(sig_df[sig_df['layer']=='Normal'])
n_5m   = len(sig_df[sig_df['layer']=='5M'])
n_mtf  = len(sig_df[sig_df['layer']=='MTF'])
print(f'Raw signals: Normal={n_norm}  5M={n_5m}  MTF={n_mtf}  Total={len(sig_df)}')

def full_stats(trades, label, months):
    if trades.empty:
        print(f'\n  {label}: no trades'); return None
    n    = len(trades)
    wins = int((trades['pnl_usd']>0).sum())
    loss = int((trades['pnl_usd']<0).sum())
    be   = int((trades['pnl_usd']==0).sum())
    net  = trades['pnl_usd'].sum()
    gw   = trades[trades['pnl_usd']>0]['pnl_usd'].sum()
    gl   = abs(trades[trades['pnl_usd']<0]['pnl_usd'].sum())
    pf   = round(gw/gl,2) if gl>0 else 999.0
    wr   = round(wins/n*100,1)
    nl   = round((wins+be)/n*100,1)
    eq   = np.concatenate([[5000], 5000+trades['pnl_usd'].cumsum().values])
    peak = np.maximum.accumulate(eq)
    mdd  = round(((peak-eq)/peak*100).max(),1)
    avg_r= round(net/(n*75),3)
    pm   = round(net/months,2)

    print(f'\n  {"="*60}')
    print(f'  MODE: {label}')
    print(f'  {"-"*60}')
    print(f'  Period        : {months} months')
    print(f'  Trades        : {n}  ({round(n/months,1)}/month)')
    print(f'  WIN/LOSS/BE   : {wins} / {loss} / {be}')
    print(f'  Win Rate      : {wr}%   |  Not-Loss: {nl}%')
    print(f'  Net P&L       : +${round(net,2)}  (+{round(net/5000*100,1)}% on $5k)')
    print(f'  Net/month     : +${pm}')
    print(f'  Profit Factor : {pf}')
    print(f'  Avg R/trade   : {avg_r}R')
    print(f'  Max DD        : {mdd}%')
    print(f'  Final Equity  : ${round(5000+net,2)}')
    print(f'  Outcome breakdown:')
    for outcome, lbl in [('T2','Full TP  (2R)'),('BE','Break-even (BE)'),('SL','Stop Loss  (SL)')]:
        sub = trades[trades['outcome']==outcome]
        if len(sub)>0:
            pct = round(len(sub)/n*100,1)
            avg = round(sub['pnl_usd'].mean(),2)
            print(f'    {lbl:<18}: {len(sub):>3}  ({pct}%)   avg ${avg:+.2f}')
    trades.to_csv(f'D:/Trade/trades_24m_{label[:6].strip()}.csv', index=False)
    return {'label':label,'n':n,'wins':wins,'loss':loss,'be':be,
            'wr':wr,'nl':nl,'net':round(net,2),'pf':pf,'mdd':mdd,
            'avg_r':avg_r,'pm':pm,'months':months}

# Run all 3 modes
results = {}
for mode in ['Normal','5M','MTF']:
    t = simulate(sig_df, df5p, mode)
    results[mode] = full_stats(t, mode, months)

# Summary comparison table
print(f'\n\n  {"="*72}')
print(f'  SUMMARY COMPARISON — 24 MONTHS  (May 2024 -> May 2026)')
print(f'  {"="*72}')
print(f'  {"Mode":<10} {"Trades":>7} {"WR%":>6} {"NL%":>6} {"Net$":>9} {"PF":>5} {"MaxDD":>7} {"$/mo":>8}')
print(f'  {"-"*65}')
for mode in ['Normal','5M','MTF']:
    r = results[mode]
    if r is None: continue
    best = ' <--' if mode == 'MTF' else ''
    print(f'  {mode:<10} {r["n"]:>7} {r["wr"]:>5}% {r["nl"]:>5}% '
          f'  ${r["net"]:>7.0f}  {r["pf"]:>4.2f}  {r["mdd"]:>5.1f}%  ${r["pm"]:>7.0f}{best}')
print(f'  {"="*72}')
print('\n  Trade CSVs: D:/Trade/trades_24m_Normal.csv / _5M.csv / _MTF.csv')
print('\n  Done.\n')
