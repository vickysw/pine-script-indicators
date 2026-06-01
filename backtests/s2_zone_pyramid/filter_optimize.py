"""Run baseline vs optimal filter combination and compare."""
exec(open('s2_v2_backtest.py', encoding='utf-8').read())
import numpy as np, pandas as pd

print('Loading data...')
df5  = load_csv(r'D:\Trade\tvHistoryData\gold_24m.csv')
df15 = load_csv(r'D:\Trade\tvHistoryData\gold_24m_15m.csv')
df1h = load_csv(r'D:\Trade\tvHistoryData\OANDA_XAUUSD, 60-1y.csv')
start = max(df5.index[0], df15.index[0], df1h.index[0])
end   = min(df5.index[-1], df15.index[-1], df1h.index[-1])
df5c = df5[start:end]; df15c = df15[start:end]; df1hc = df1h[start:end]
print(f'Period: {df5c.index[0].date()} to {df5c.index[-1].date()} | {len(df5c):,} bars')

def run(label, overrides):
    orig = {k: CFG[k] for k in overrides}
    CFG.update(overrides)
    CFG['SHOW_5M'] = False; CFG['SHOW_MTF'] = False
    sig_df, df5p = detect_signals(df5c, df15c, df1hc)
    trades = simulate(sig_df, df5p, 'Normal')
    CFG.update(orig)
    if trades.empty:
        print(f'  {label}: no trades')
        return None

    n    = len(trades)
    wins = int((trades['pnl_usd'] > 0).sum())
    loss = int((trades['pnl_usd'] < 0).sum())
    be   = int((trades['pnl_usd'] == 0).sum())
    net  = trades['pnl_usd'].sum()
    gw   = trades[trades['pnl_usd'] > 0]['pnl_usd'].sum()
    gl   = abs(trades[trades['pnl_usd'] < 0]['pnl_usd'].sum())
    pf   = round(gw / gl, 2) if gl > 0 else 999.0
    wr   = round(wins / n * 100, 1)
    nl   = round((wins + be) / n * 100, 1)
    eq   = np.concatenate([[5000], 5000 + trades['pnl_usd'].cumsum().values])
    peak = np.maximum.accumulate(eq)
    mdd  = round(((peak - eq) / peak * 100).max(), 1)
    avg_r = round(net / (n * 75), 3)
    pm    = round(net / ((end - start).days / 30), 2)

    print(f'\n  {"="*57}')
    print(f'  {label}')
    print(f'  {"-"*57}')
    print(f'  Trades    : {n}  (WIN:{wins}  LOSS:{loss}  BE:{be})')
    print(f'  Per month : {round(n/((end-start).days/30),1)} trades/month')
    print(f'  Win Rate  : {wr}%   |  Not-Loss: {nl}%')
    print(f'  Net P&L   : +${round(net,2)}   |  PF: {pf}')
    print(f'  Avg R     : {avg_r}R/trade  |  Net/month: +${pm}')
    print(f'  Max DD    : {mdd}%   |  Final Eq: ${round(5000+net,2)}')
    print(f'  Outcome breakdown:')
    for outcome, lbl in [('T2','Full TP (2R)'), ('BE','Break-even'), ('SL','Stop Loss')]:
        sub = trades[trades['outcome'] == outcome]
        if len(sub) > 0:
            avg = round(sub['pnl_usd'].mean(), 2)
            pct = round(len(sub)/n*100, 1)
            print(f'    {lbl:<18}: {len(sub):>3} trades ({pct}%)  avg ${avg:+.2f}')

    trades.to_csv(f'D:/Trade/trades_{label[:8].strip()}.csv', index=False)
    return {'n':n,'wins':wins,'loss':loss,'be':be,'net':round(net,2),
            'wr':wr,'nl':nl,'pf':pf,'mdd':mdd,'avg_r':avg_r,'pm':pm}


# ── Run both ──────────────────────────────────────────────────────────
r_base = run('BASELINE  (all original filters)', {})

r_opt  = run('OPTIMAL   (fresh=F  open=F  sl_skip=5-8pt)', {
    'S2_FRESH_ONLY': False,
    'SKIP_OPEN_30M': False,
    'SL_SKIP_MIN':   5.0,
    'SL_SKIP_MAX':   8.0,
})

# ── Delta table ───────────────────────────────────────────────────────
if r_base and r_opt:
    print(f'\n  {"="*57}')
    print(f'  DELTA: OPTIMAL vs BASELINE')
    print(f'  {"-"*57}')
    metrics = [
        ('Trades',      'n',     '',    '{:+d}'),
        ('Win Rate',    'wr',    '%',   '{:+.1f}'),
        ('Not-Loss',    'nl',    '%',   '{:+.1f}'),
        ('Net P&L',     'net',   '$',   '{:+.2f}'),
        ('PF',          'pf',    '',    '{:+.2f}'),
        ('Max DD',      'mdd',   '%',   '{:+.1f}'),
        ('Avg R',       'avg_r', 'R',   '{:+.3f}'),
        ('Net/month',   'pm',    '$',   '{:+.2f}'),
    ]
    for lbl, key, unit, fmt in metrics:
        base_v = r_base[key]; opt_v = r_opt[key]
        delta  = opt_v - base_v
        better = ''
        if key in ('n','wr','nl','net','pf','avg_r','pm') and delta > 0:
            better = '  <-- BETTER'
        elif key in ('mdd',) and delta < 0:
            better = '  <-- BETTER'
        print(f'  {lbl:<12}: {str(base_v)+unit:<12} -> {str(opt_v)+unit:<12} '
              f'({fmt.format(delta)}{unit}){better}')

print('\n  Done.\n')
