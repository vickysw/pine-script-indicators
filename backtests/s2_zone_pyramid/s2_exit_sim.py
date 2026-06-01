"""
S2 Zone-Pyramid v2 vs v3 — Full backtest with SL/T1/T2 exit logic
PnL model matches CSV: -75 / +56.25 / +131.25 / -18.75
"""
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Config ────────────────────────────────────────────────────────────────────
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
MAX_BARS_IN_TRADE = 200   # timeout

# Fixed PnL amounts (matches existing CSV)
PNL_SL       = -75.0
PNL_SL_AFT_T1 = -18.75   # same-bar T1+SL edge case
PNL_BE        = 56.25    # T1 hit then SL at entry
PNL_T2        = 131.25   # T1 then T2

# ── Load + prep 5M data ────────────────────────────────────────────────��──────
d5a = pd.read_csv(r'tvHistoryData\OANDA_XAUUSD, 5.csv')
d5b = pd.read_csv(r'OANDA_XAUUSD, 5_30th.csv')
for d in [d5a, d5b]:
    d['dt'] = pd.to_datetime(d['time'], unit='s', utc=True)
    d.set_index('dt', inplace=True)

df = pd.concat([d5a[['open','high','low','close']],
                d5b[['open','high','low','close']]]).sort_index()
df = df[~df.index.duplicated(keep='last')]

d60 = pd.read_csv(r'tvHistoryData\OANDA_XAUUSD, 60-1y.csv')
d60['dt'] = pd.to_datetime(d60['time'], unit='s', utc=True)
d60.set_index('dt', inplace=True)
d60.sort_index(inplace=True)

# ATR (Wilder RMA)
df['prev_c'] = df['close'].shift(1)
df['tr'] = np.maximum(df['high']-df['low'],
           np.maximum((df['high']-df['prev_c']).abs(),
                      (df['low'] -df['prev_c']).abs()))
def rma(s, n):
    r = np.full(len(s), np.nan)
    for i in range(len(s)):
        if np.isnan(s.iloc[i]): continue
        r[i] = s.iloc[i] if np.isnan(r[i-1]) else r[i-1]*(1-1/n)+s.iloc[i]*(1/n)
    return pd.Series(r, index=s.index)

df['atr14']  = rma(df['tr'], 14)
df['atr14e'] = df['atr14'].ewm(span=14, adjust=False).mean()

htf_c = d60['close'].reindex(df.index, method='ffill')
df['htf_close'] = htf_c
df['htf_ma']    = df['htf_close'].ewm(span=HTF_LEN, adjust=False).mean()
df['htf_bear']  = df['htf_close'] < df['htf_ma']
df['htf_bull']  = df['htf_close'] > df['htf_ma']
df['htf_ds']    = df['htf_ma'] - df['htf_close']   # sell distance
df['htf_db']    = df['htf_close'] - df['htf_ma']   # buy distance

df['ema9']  = df['close'].ewm(span=9,  adjust=False).mean()
df['ema15'] = df['close'].ewm(span=15, adjust=False).mean()
df['hlc3']  = (df['high']+df['low']+df['close'])/3
df['date']  = df.index.date
df['vwap']  = df.groupby('date')['hlc3'].transform(lambda x: x.expanding().mean())

df['sr'] = (df['close']<df['open']) & ((df['open']-df['close'])>df['atr14']*0.5)
df['sg'] = (df['close']>df['open']) & ((df['close']-df['open'])>df['atr14']*0.5)
df['be'] = (df['close']<df['open']) & (df['close']<df['low'].shift(1)) & (df['open']>=df['close'].shift(1))
df['bu'] = (df['close']>df['open']) & (df['close']>df['high'].shift(1)) & (df['open']<=df['close'].shift(1))
df['cs'] = ((df['ema9']>df['close'])&(df['ema15']>df['close']))|(df['vwap']>df['close'])
df['cb'] = ((df['ema9']<df['close'])&(df['ema15']<df['close']))|(df['vwap']<df['close'])
df['ny'] = (df.index.hour>=NY_START_UTC)&(df.index.hour<NY_END_UTC)

# ── Exit simulator ────────────────────────────────────────────────────────────
def exit_trade(entry_bar_i, direction, entry, sl, t1, t2):
    """Simulate trade from bar after entry signal to SL/T1/T2."""
    t1_hit = False
    sl_moved_to_be = False

    for j in range(entry_bar_i+1, min(entry_bar_i+MAX_BARS_IN_TRADE+1, len(df))):
        bar = df.iloc[j]
        hi, lo = bar['high'], bar['low']

        if direction == 'SHORT':
            # Check T1 and SL on same bar (edge case)
            if lo <= t1 and hi >= sl and not t1_hit:
                return 'SL', True, PNL_SL_AFT_T1   # same bar T1+SL

            if not t1_hit:
                if hi >= sl:
                    return 'SL', False, PNL_SL
                if lo <= t1:
                    t1_hit = True
                    sl_moved_to_be = True
            else:
                # SL now at entry (BE)
                if hi >= entry:
                    return 'BE', True, PNL_BE
                if lo <= t2:
                    return 'T2', True, PNL_T2
        else:  # LONG
            if hi >= t1 and lo <= sl and not t1_hit:
                return 'SL', True, PNL_SL_AFT_T1

            if not t1_hit:
                if lo <= sl:
                    return 'SL', False, PNL_SL
                if hi >= t1:
                    t1_hit = True
            else:
                if lo <= entry:
                    return 'BE', True, PNL_BE
                if hi >= t2:
                    return 'T2', True, PNL_T2

    # Timeout
    if t1_hit:
        return 'BE', True, PNL_BE
    return 'SL', False, PNL_SL

# ── Main backtest loop ────────────────────────────────────────────────────────
def run_backtest(v3=False):
    trades = []
    bear_zt,bear_zb,bear_zc,bear_ze = [],[],[],[]
    bull_zt,bull_zb,bull_zc,bull_ze = [],[],[],[]
    bear_touches=bull_touches=0
    bear_ref=bull_ref=None
    prev_in_sell=prev_in_buy=False

    for bi in range(5, len(df)):
        r   = df.iloc[bi]
        c,h,l = r['close'],r['high'],r['low']
        atr = r['atr14']; atre = r['atr14e']
        htf_c_ = r['htf_close']; htf_m = r['htf_ma']
        hbear = bool(r['htf_bear']); hbull = bool(r['htf_bull'])
        if np.isnan(atr) or np.isnan(htf_m): continue

        inv = atr * ZONE_INVALID_ATR
        c0=df['close'].iloc[bi]; c1=df['close'].iloc[bi-1]
        c2=df['close'].iloc[bi-2]; c3=df['close'].iloc[bi-3]
        c4=df['close'].iloc[bi-4]
        h4=df['high'].iloc[bi-4]; l4=df['low'].iloc[bi-4]

        # Zone creation
        if c0<c1<c2<c3<c4 and hbear:
            drop = c4-c0
            zt = h4
            if drop>=atre*S2_MIN_IMPULSE and (S2_KEY_STEP<=0 or abs(zt-round(zt/S2_KEY_STEP)*S2_KEY_STEP)<=S2_KEY_RANGE):
                if len(bear_zt)>=3:
                    bear_zt.pop(0);bear_zb.pop(0);bear_zc.pop(0);bear_ze.pop(0)
                bear_zt.append(zt);bear_zb.append(l4);bear_zc.append(bi);bear_ze.append(0.0)

        if c0>c1>c2>c3>c4 and hbull:
            rise = c0-c4; zb=l4
            zt2=h4
            if rise>=atre*S2_MIN_IMPULSE and (S2_KEY_STEP<=0 or abs(zb-round(zb/S2_KEY_STEP)*S2_KEY_STEP)<=S2_KEY_RANGE):
                if len(bull_zt)>=3:
                    bull_zt.pop(0);bull_zb.pop(0);bull_zc.pop(0);bull_ze.pop(0)
                bull_zt.append(zt2);bull_zb.append(zb);bull_zc.append(bi);bull_ze.append(0.0)

        # Invalidation
        for zts,zbs,zcs,zes,is_b in[(bear_zt,bear_zb,bear_zc,bear_ze,True),(bull_zt,bull_zb,bull_zc,bull_ze,False)]:
            i2=len(zts)-1
            while i2>=0:
                age_bad=(bi-zcs[i2])>S2_MAX_AGE
                pb=c>zts[i2]+inv if is_b else c<zbs[i2]-inv
                if pb or age_bad:
                    zts.pop(i2);zbs.pop(i2);zcs.pop(i2);zes.pop(i2)
                i2-=1

        # In-zone
        in_sell=False; sz_top=sz_bot=None; sz_idx=-1
        for i2 in range(len(bear_zt)):
            if h>=bear_zb[i2] and l<=bear_zt[i2]:
                in_sell=True;sz_top=bear_zt[i2];sz_bot=bear_zb[i2];sz_idx=i2;break

        in_buy=False; bz_top=bz_bot=None; bz_idx=-1
        for i2 in range(len(bull_zt)):
            if h>=bull_zb[i2] and l<=bull_zt[i2]:
                in_buy=True;bz_top=bull_zt[i2];bz_bot=bull_zb[i2];bz_idx=i2;break

        sell_fresh=sz_idx>=0 and bear_ze[sz_idx]==0.0
        buy_fresh =bz_idx>=0 and bull_ze[bz_idx]==0.0

        # Touch counter
        if in_sell:
            if bear_ref is None or sz_top!=bear_ref: bear_touches=1;bear_ref=sz_top
            elif not prev_in_sell: bear_touches+=1
        else:
            if bear_ref is not None and bear_ref not in bear_zt: bear_ref=None;bear_touches=0

        if in_buy:
            if bull_ref is None or bz_bot!=bull_ref: bull_touches=1;bull_ref=bz_bot
            elif not prev_in_buy: bull_touches+=1
        else:
            if bull_ref is not None and bull_ref not in bull_zb: bull_ref=None;bull_touches=0

        # SL filter
        ssd = (sz_top+SL_BUF-c) if sz_top else 0.0
        bsd = (c-(bz_bot-SL_BUF)) if bz_bot else 0.0
        sl_ok_s = SL_SKIP_MAX<=0 or not(SL_SKIP_MIN<=ssd<=SL_SKIP_MAX)
        sl_ok_b = SL_SKIP_MAX<=0 or not(SL_SKIP_MIN<=bsd<=SL_SKIP_MAX)

        dist_ok_s = not v3 or (htf_m-htf_c_)>=HTF_DIST_MIN_V3
        dist_ok_b = not v3 or (htf_c_-htf_m)>=HTF_DIST_MIN_V3

        cs_ = bool(r['cs']); cb_ = bool(r['cb'])
        sr_=bool(r['sr']); sg_=bool(r['sg']); be_=bool(r['be']); bu_=bool(r['bu'])
        ny_ = bool(r['ny'])

        base_s = in_sell and hbear and dist_ok_s and bear_touches>=ZONE_TOUCHES_MIN and (be_ or sr_) and cs_
        base_b = in_buy  and hbull and dist_ok_b and bull_touches>=ZONE_TOUCHES_MIN and (bu_ or sg_) and cb_

        sig_s = base_s and ny_ and sl_ok_s
        sig_b = base_b and ny_ and sl_ok_b

        # Execute signals
        if sig_s and sz_top:
            sl_p = sz_top+SL_BUF
            dist = sl_p - c
            if dist > 0:
                t1 = c - dist*1.5
                t2 = c - dist*2.0
                outcome, t1h, pnl = exit_trade(bi, 'SHORT', c, sl_p, t1, t2)
                trades.append({'entry_time':r.name,'direction':'SHORT',
                    'entry':round(c,2),'sl':round(sl_p,2),'dist':round(dist,2),
                    't1':round(t1,2),'t2':round(t2,2),'touches':bear_touches,
                    'htf_dist':round(r['htf_ds'],2),'fresh':sell_fresh,
                    'outcome':outcome,'t1_hit':t1h,'pnl_usd':pnl})
                bear_ze[sz_idx]=1.0

        if sig_b and bz_bot:
            sl_p = bz_bot-SL_BUF
            dist = c - sl_p
            if dist > 0:
                t1 = c + dist*1.5
                t2 = c + dist*2.0
                outcome, t1h, pnl = exit_trade(bi, 'LONG', c, sl_p, t1, t2)
                trades.append({'entry_time':r.name,'direction':'LONG',
                    'entry':round(c,2),'sl':round(sl_p,2),'dist':round(dist,2),
                    't1':round(t1,2),'t2':round(t2,2),'touches':bull_touches,
                    'htf_dist':round(r['htf_db'],2),'fresh':buy_fresh,
                    'outcome':outcome,'t1_hit':t1h,'pnl_usd':pnl})
                bull_ze[bz_idx]=1.0

        prev_in_sell=in_sell; prev_in_buy=in_buy

    return pd.DataFrame(trades)

print("Running V2...")
v2 = run_backtest(v3=False)
print("Running V3...")
v3 = run_backtest(v3=True)

# ── Print results ─────────────────────────────────────────────────────────────
def report(df_t, label):
    if len(df_t)==0:
        print(f"\n{label}: No trades"); return
    wins = df_t['outcome'].isin(['T2','BE'])
    t2   = df_t['outcome']=='T2'
    wr   = wins.mean()*100
    t2r  = t2.mean()*100
    pnl  = df_t['pnl_usd'].sum()
    avg  = pnl/len(df_t)

    print(f"\n{'='*65}")
    print(f"{label}")
    print(f"{'='*65}")
    print(f"{'#':<3} {'Date':<12} {'Dir':<6} {'Entry':<8} {'Dist':>5} {'T':>2} {'HTFd':>6} {'Out':<5} {'T1':>3} {'PnL':>7} {'Cum':>8}")
    print(f"{'-'*65}")
    cum=0
    for i,(_, r) in enumerate(df_t.iterrows(),1):
        cum += r['pnl_usd']
        t1s = 'Y' if r['t1_hit'] else 'N'
        print(f"{i:<3} {str(r['entry_time'])[:10]:<12} {r['direction']:<6} "
              f"{r['entry']:<8.2f} {r['dist']:>5.2f} {int(r['touches']):>2} "
              f"{r['htf_dist']:>6.1f} {r['outcome']:<5} {t1s:>3} "
              f"${r['pnl_usd']:>+6.0f} ${cum:>+7.0f}")

    print(f"{'-'*65}")
    print(f"TOTAL  {len(df_t)} trades | WR={wr:.1f}% | T2-rate={t2r:.1f}% | "
          f"PnL=${pnl:+.0f} | avg=${avg:+.1f}/trade")
    print(f"\nOutcome breakdown:")
    print(df_t.groupby('outcome')['pnl_usd'].agg(['count','sum','mean']).round(1).to_string())

report(v2, "V2 — Normal (no distance filter)")
report(v3, "V3 — Distance filter >= 10pts")

# Side-by-side summary
print(f"\n{'='*65}")
print("HEAD-TO-HEAD SUMMARY")
print(f"{'='*65}")
for lbl, df_t in [('V2', v2), ('V3', v3)]:
    if len(df_t)==0: continue
    wins = df_t['outcome'].isin(['T2','BE'])
    t2   = df_t['outcome']=='T2'
    sl   = df_t['outcome']=='SL'
    print(f"{lbl}: {len(df_t):3d} trades | "
          f"WR={wins.mean()*100:.1f}% | "
          f"T2={t2.mean()*100:.1f}% | "
          f"SL={sl.mean()*100:.1f}% | "
          f"PnL=${df_t['pnl_usd'].sum():+.0f} | "
          f"avg=${df_t['pnl_usd'].mean():+.1f}/trade")

removed = len(v2)-len(v3)
if len(v2)>0 and len(v3)>0:
    pnl_diff = v3['pnl_usd'].sum() - v2['pnl_usd'].sum()
    print(f"\nFilter removed : {removed} trades")
    print(f"PnL difference : ${pnl_diff:+.0f}")
