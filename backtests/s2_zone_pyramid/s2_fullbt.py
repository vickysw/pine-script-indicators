"""
S2 Zone-Pyramid — V2 vs V3 full 2-year backtest
Data: gold_24m.csv (5M, May 2024-May 2026) + gold_24m_1h.csv (1H HTF)
"""
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

HTF_LEN          = 50
ZONE_TOUCHES_MIN = 2
ZONE_INVALID_ATR = 1.5
S2_MIN_IMPULSE   = 1.5
S2_MAX_AGE       = 288
S2_KEY_STEP      = 50.0
S2_KEY_RANGE     = 15.0
SL_SKIP_MIN      = 5.0
SL_SKIP_MAX      = 8.0
SL_BUF           = 0.05
ACCOUNT_SIZE     = 5000
RISK_PCT         = 1.5
POINT_VAL        = 100.0
HTF_DIST_MIN_V3  = 10.0
NY_START_UTC     = 13
NY_END_UTC       = 18
MAX_BARS         = 200

PNL_SL       = -75.0
PNL_SL_T1    = -18.75
PNL_BE       =  56.25
PNL_T2       = 131.25

# ── Load data ─────────────────────────────────────────────────────────────────
df = pd.read_csv(r'tvHistoryData\gold_24m.csv')
df['dt'] = pd.to_datetime(df['Datetime'])
df = df.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close'})
df.set_index('dt', inplace=True)
df.sort_index(inplace=True)
print(f"5M  : {df.index[0]} to {df.index[-1]}  ({len(df)} bars)")

h1 = pd.read_csv(r'tvHistoryData\gold_24m_1h.csv')
h1['dt'] = pd.to_datetime(h1['Datetime'])
h1 = h1.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close'})
h1.set_index('dt', inplace=True)
h1.sort_index(inplace=True)
print(f"1H  : {h1.index[0]} to {h1.index[-1]}  ({len(h1)} bars)")

# ── ATR ───────────────────────────────────────────────────────────────────────
df['prev_c'] = df['close'].shift(1)
df['tr'] = np.maximum(df['high']-df['low'],
           np.maximum((df['high']-df['prev_c']).abs(),
                      (df['low'] -df['prev_c']).abs()))

def rma(s, n):
    r = np.full(len(s), np.nan)
    for i in range(len(s)):
        v = s.iloc[i]
        if np.isnan(v): continue
        r[i] = v if np.isnan(r[i-1]) else r[i-1]*(1-1/n)+v*(1/n)
    return pd.Series(r, index=s.index)

df['atr14']  = rma(df['tr'], 14)
df['atr14e'] = df['atr14'].ewm(span=14, adjust=False).mean()

# ── HTF bias from 1H ─────────────────────────────────────────────────────────
htf_close = h1['close'].reindex(df.index, method='ffill')
df['htf_close'] = htf_close
df['htf_ma']    = df['htf_close'].ewm(span=HTF_LEN, adjust=False).mean()
df['htf_bear']  = df['htf_close'] < df['htf_ma']
df['htf_bull']  = df['htf_close'] > df['htf_ma']
df['htf_ds']    = df['htf_ma'] - df['htf_close']
df['htf_db']    = df['htf_close'] - df['htf_ma']

# ── 5M indicators ─────────────────────────────────────────────────────────────
df['ema9']  = df['close'].ewm(span=9,  adjust=False).mean()
df['ema15'] = df['close'].ewm(span=15, adjust=False).mean()
df['hlc3']  = (df['high']+df['low']+df['close'])/3
df['date']  = df.index.date
df['vwap']  = df.groupby('date')['hlc3'].transform(lambda x: x.expanding().mean())

df['sr'] = (df['close']<df['open']) & ((df['open']-df['close'])>df['atr14']*0.5)
df['sg'] = (df['close']>df['open']) & ((df['close']-df['open'])>df['atr14']*0.5)
df['be'] = (df['close']<df['open']) & (df['close']<df['low'].shift(1))  & (df['open']>=df['close'].shift(1))
df['bu'] = (df['close']>df['open']) & (df['close']>df['high'].shift(1)) & (df['open']<=df['close'].shift(1))
df['cs'] = ((df['ema9']>df['close'])&(df['ema15']>df['close']))|(df['vwap']>df['close'])
df['cb'] = ((df['ema9']<df['close'])&(df['ema15']<df['close']))|(df['vwap']<df['close'])
df['ny'] = (df.index.hour>=NY_START_UTC)&(df.index.hour<NY_END_UTC)

# ── Exit simulator ────────────────────────────────────────────────────────────
def exit_trade(bi, direction, entry, sl, t1, t2):
    t1_hit = False
    for j in range(bi+1, min(bi+MAX_BARS+1, len(df))):
        hi = df['high'].iloc[j]
        lo = df['low'].iloc[j]
        if direction == 'SHORT':
            if lo <= t1 and hi >= sl and not t1_hit:
                return 'SL', True, PNL_SL_T1
            if not t1_hit:
                if hi >= sl: return 'SL', False, PNL_SL
                if lo <= t1: t1_hit = True
            else:
                if hi >= entry: return 'BE', True, PNL_BE
                if lo <= t2:    return 'T2', True, PNL_T2
        else:
            if hi >= t1 and lo <= sl and not t1_hit:
                return 'SL', True, PNL_SL_T1
            if not t1_hit:
                if lo <= sl: return 'SL', False, PNL_SL
                if hi >= t1: t1_hit = True
            else:
                if lo <= entry: return 'BE', True, PNL_BE
                if hi >= t2:    return 'T2', True, PNL_T2
    return ('BE', True, PNL_BE) if t1_hit else ('SL', False, PNL_SL)

# ── Main loop ─────────────────────────────────────────────────────────────────
def run(v3=False):
    trades = []
    bzt,bzb,bzc,bze = [],[],[],[]
    uzt,uzb,uzc,uze = [],[],[],[]
    bt=ut=0; br=ur=None
    pis=pib=False

    for bi in range(5, len(df)):
        r   = df.iloc[bi]
        c,h,l = r['close'],r['high'],r['low']
        atr=r['atr14']; atre=r['atr14e']
        hc=r['htf_close']; hm=r['htf_ma']
        hbr=bool(r['htf_bear']); hbu=bool(r['htf_bull'])
        if np.isnan(atr) or np.isnan(hm): continue
        inv=atr*ZONE_INVALID_ATR

        c0=df['close'].iloc[bi]; c1=df['close'].iloc[bi-1]
        c2=df['close'].iloc[bi-2]; c3=df['close'].iloc[bi-3]
        c4=df['close'].iloc[bi-4]
        h4=df['high'].iloc[bi-4]; l4=df['low'].iloc[bi-4]

        def key(p): return S2_KEY_STEP<=0 or abs(p-round(p/S2_KEY_STEP)*S2_KEY_STEP)<=S2_KEY_RANGE

        # Zone creation
        if c0<c1<c2<c3<c4 and hbr and (c4-c0)>=atre*S2_MIN_IMPULSE and key(h4):
            if len(bzt)>=3: bzt.pop(0);bzb.pop(0);bzc.pop(0);bze.pop(0)
            bzt.append(h4);bzb.append(l4);bzc.append(bi);bze.append(0.0)
        if c0>c1>c2>c3>c4 and hbu and (c0-c4)>=atre*S2_MIN_IMPULSE and key(l4):
            if len(uzt)>=3: uzt.pop(0);uzb.pop(0);uzc.pop(0);uze.pop(0)
            uzt.append(h4);uzb.append(l4);uzc.append(bi);uze.append(0.0)

        # Invalidation
        for zt,zb,zc,ze,ib in[(bzt,bzb,bzc,bze,True),(uzt,uzb,uzc,uze,False)]:
            i2=len(zt)-1
            while i2>=0:
                if (bi-zc[i2])>S2_MAX_AGE or (c>zt[i2]+inv if ib else c<zb[i2]-inv):
                    zt.pop(i2);zb.pop(i2);zc.pop(i2);ze.pop(i2)
                i2-=1

        # In-zone
        ins=False; st=sb=None; si=-1
        for i2 in range(len(bzt)):
            if h>=bzb[i2] and l<=bzt[i2]: ins=True;st=bzt[i2];sb=bzb[i2];si=i2;break
        inb=False; bt2=bb=None; bi2=-1
        for i2 in range(len(uzt)):
            if h>=uzb[i2] and l<=uzt[i2]: inb=True;bt2=uzt[i2];bb=uzb[i2];bi2=i2;break

        sf=si>=0 and bze[si]==0.0
        bf=bi2>=0 and uze[bi2]==0.0

        # Touch counter
        if ins:
            if br is None or st!=br: bt=1;br=st
            elif not pis: bt+=1
        else:
            if br is not None and br not in bzt: br=None;bt=0
        if inb:
            if ur is None or bb!=ur: ut=1;ur=bb
            elif not pib: ut+=1
        else:
            if ur is not None and ur not in uzb: ur=None;ut=0

        # SL filter
        ssd=(st+SL_BUF-c) if st else 0.0
        bsd=(c-(bb-SL_BUF)) if bb else 0.0
        sos=SL_SKIP_MAX<=0 or not(SL_SKIP_MIN<=ssd<=SL_SKIP_MAX)
        sob=SL_SKIP_MAX<=0 or not(SL_SKIP_MIN<=bsd<=SL_SKIP_MAX)

        dos=not v3 or (hm-hc)>=HTF_DIST_MIN_V3
        dob=not v3 or (hc-hm)>=HTF_DIST_MIN_V3

        ny_=bool(r['ny'])
        sigs=ins and hbr and dos and bt>=ZONE_TOUCHES_MIN and (bool(r['be']) or bool(r['sr'])) and bool(r['cs']) and ny_ and sos
        sigb=inb and hbu and dob and ut>=ZONE_TOUCHES_MIN and (bool(r['bu']) or bool(r['sg'])) and bool(r['cb']) and ny_ and sob

        if sigs and st:
            sp=st+SL_BUF; d=sp-c
            if d>0:
                oc,t1h,pnl=exit_trade(bi,'SHORT',c,sp,c-d*1.5,c-d*2.0)
                trades.append({'entry_time':r.name,'direction':'SHORT','entry':round(c,2),
                    'sl':round(sp,2),'dist':round(d,2),'touches':bt,
                    'htf_dist':round(r['htf_ds'],2),'fresh':sf,
                    'outcome':oc,'t1_hit':t1h,'pnl_usd':pnl})
                bze[si]=1.0

        if sigb and bb:
            sp=bb-SL_BUF; d=c-sp
            if d>0:
                oc,t1h,pnl=exit_trade(bi,'LONG',c,sp,c+d*1.5,c+d*2.0)
                trades.append({'entry_time':r.name,'direction':'LONG','entry':round(c,2),
                    'sl':round(sp,2),'dist':round(d,2),'touches':ut,
                    'htf_dist':round(r['htf_db'],2),'fresh':bf,
                    'outcome':oc,'t1_hit':t1h,'pnl_usd':pnl})
                uze[bi2]=1.0

        pis=ins; pib=inb

    return pd.DataFrame(trades)

print("\nRunning V2 (full 2 years)...")
v2 = run(v3=False)
print(f"Done — {len(v2)} trades")
print("Running V3 (full 2 years)...")
v3t = run(v3=True)
print(f"Done — {len(v3t)} trades")

# ── Reports ───────────────────────────────────────────────────────────────────
def stats(df_t):
    if len(df_t)==0: return
    wins = df_t['outcome'].isin(['T2','BE'])
    t2   = df_t['outcome']=='T2'
    sl   = df_t['outcome']=='SL'
    be   = df_t['outcome']=='BE'
    pnl  = df_t['pnl_usd'].sum()
    risk = ACCOUNT_SIZE*RISK_PCT/100
    return {
        'n': len(df_t),
        'WR': round(wins.mean()*100,1),
        'T2': round(t2.mean()*100,1),
        'BE': round(be.mean()*100,1),
        'SL': round(sl.mean()*100,1),
        'PnL': round(pnl,0),
        'avg': round(pnl/len(df_t),1),
        'R_avg': round(pnl/len(df_t)/risk,2),
    }

# Monthly breakdown
def monthly(df_t, label):
    df_t = df_t.copy()
    df_t['month'] = pd.to_datetime(df_t['entry_time']).dt.to_period('M')
    g = df_t.groupby('month').apply(lambda x: pd.Series({
        'n': len(x),
        'WR': round(x['outcome'].isin(['T2','BE']).mean()*100,0),
        'T2': round((x['outcome']=='T2').mean()*100,0),
        'SL': round((x['outcome']=='SL').mean()*100,0),
        'PnL': round(x['pnl_usd'].sum(),0),
    }), include_groups=False)
    print(f"\n{label} — Monthly breakdown:")
    print(f"{'Month':<10} {'n':>4} {'WR':>6} {'T2%':>6} {'SL%':>6} {'PnL':>8}")
    print('-'*42)
    for m, row in g.iterrows():
        print(f"{str(m):<10} {int(row['n']):>4} {row['WR']:>5.0f}% {row['T2']:>5.0f}% {row['SL']:>5.0f}% ${row['PnL']:>+7.0f}")
    print('-'*42)
    total = df_t['pnl_usd'].sum()
    wins  = df_t['outcome'].isin(['T2','BE']).mean()*100
    print(f"{'TOTAL':<10} {len(df_t):>4} {wins:>5.1f}% {(df_t['outcome']=='T2').mean()*100:>5.1f}% {(df_t['outcome']=='SL').mean()*100:>5.1f}% ${total:>+7.0f}")

s2 = stats(v2)
s3 = stats(v3t)

print(f"\n{'='*55}")
print("HEAD-TO-HEAD — Full 2-Year Backtest (May 2024 - May 2026)")
print(f"{'='*55}")
print(f"{'':18} {'V2':>12} {'V3':>12} {'Delta':>10}")
print(f"{'-'*55}")
for k in ['n','WR','T2','BE','SL','PnL','avg','R_avg']:
    unit = '%' if k in ['WR','T2','BE','SL'] else ('$' if k in ['PnL','avg'] else '')
    v2v = s2[k]; v3v = s3[k]
    delta = v3v - v2v if isinstance(v2v, (int,float)) else ''
    delta_s = f"{delta:+.1f}" if isinstance(delta, float) else f"{delta:+d}"
    print(f"{k:<18} {str(v2v)+unit:>12} {str(v3v)+unit:>12} {delta_s:>10}")

monthly(v2,  "V2 Normal")
monthly(v3t, "V3 Distance Filter")

# Outcome breakdown
print(f"\nV2 outcome breakdown:")
print(v2.groupby('outcome')['pnl_usd'].agg(['count','sum']).rename(columns={'count':'n','sum':'PnL'}))
print(f"\nV3 outcome breakdown:")
print(v3t.groupby('outcome')['pnl_usd'].agg(['count','sum']).rename(columns={'count':'n','sum':'PnL'}))

# Save results
v2.to_csv('s2_v2_sim_results.csv',  index=False)
v3t.to_csv('s2_v3_sim_results.csv', index=False)
print(f"\nSaved: s2_v2_sim_results.csv, s2_v3_sim_results.csv")
