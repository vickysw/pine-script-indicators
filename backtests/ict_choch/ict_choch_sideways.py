"""
ChoCH Tier 4 — Sideways Market Filter
Base: pivot=2, dead zone 5-8pt, no T1, T2=2R ($1222/mo)

Sideways filter: skip signal if ATR14 < Nth percentile of last K bars
Tests:
  ATR percentile thresholds: 20, 25, 30, 35, 40, 50
  Lookback windows: 50, 100, 200 bars
"""
import pandas as pd, numpy as np, pytz

INITIAL_CAPITAL=5000.0; RISK_PCT=0.010
T2_RR=2.0; TRAIL_BE_RR=1.0
CHOCH_PIVOT_LEN=2; CHOCH_COOLDOWN=5
HTF_MA_LEN=50; MANUAL_SL_DIST=5.0; CHOCH_SL_CAP=3.0
SL_DEAD_MIN=5.0; SL_DEAD_MAX=8.0
IST=pytz.timezone("Asia/Kolkata")

def sma(s,n): return s.rolling(n).mean()
def wilder_atr(df,n=14):
    tr=pd.concat([df.high-df.low,(df.high-df.close.shift(1)).abs(),(df.low-df.close.shift(1)).abs()],axis=1).max(axis=1)
    r=np.full(len(tr),np.nan); r[n-1]=tr.iloc[:n].mean()
    for i in range(n,len(tr)): r[i]=(1/n)*tr.iloc[i]+(1-1/n)*r[i-1]
    return pd.Series(r,index=df.index)
def pivot_highs(arr,n):
    N=len(arr); ph=np.full(N,np.nan)
    for i in range(2*n,N):
        cx=i-n; w=arr[i-2*n:i+1]
        if arr[cx]==np.max(w) and np.sum(w==arr[cx])==1: ph[i]=arr[cx]
    return ph
def pivot_lows(arr,n):
    N=len(arr); pl=np.full(N,np.nan)
    for i in range(2*n,N):
        cx=i-n; w=arr[i-2*n:i+1]
        if arr[cx]==np.min(w) and np.sum(w==arr[cx])==1: pl[i]=arr[cx]
    return pl
def load_csv(path):
    df=pd.read_csv(path); df.columns=[c.strip().lower().replace(" ","_") for c in df.columns]
    if "time" in df.columns: df["ts"]=pd.to_datetime(df["time"].astype(np.int64),unit="s",utc=True)
    elif "datetime" in df.columns: df["ts"]=pd.to_datetime(df["datetime"],utc=True)
    return df.set_index("ts").sort_index()[["open","high","low","close"]].astype(float)
def in_ny(ts):
    t=ts.tz_convert(IST); m=t.hour*60+t.minute
    return 18*60+30<=m<23*60+30

print("Loading...")
df5=load_csv(r"D:\Trade\tvHistoryData\gold_24m.csv")
df1h=load_csv(r"D:\Trade\tvHistoryData\gold_24m_1h.csv")
start=max(df5.index[0],df1h.index[0]); end=min(df5.index[-1],df1h.index[-1])
df5=df5[start:end]; df1h=df1h[start:end]; months=round((end-start).days/30.5,1)
h1_sma=sma(df1h.close,HTF_MA_LEN).rename("htf_ma").reset_index()
merged=pd.merge_asof(df5[[]].reset_index().sort_values("ts"),h1_sma.sort_values("ts"),
                     on="ts",direction="backward").set_index("ts")
df5["htf_ma"]=merged["htf_ma"].reindex(df5.index)
df5["atr14"]=wilder_atr(df5)

h=df5.high.values; l=df5.low.values
o=df5.open.values; c=df5.close.values; N=len(df5)
macro_ph=pivot_highs(h,CHOCH_PIVOT_LEN); macro_pl=pivot_lows(l,CHOCH_PIVOT_LEN)
atr14=df5.atr14.values; idx=df5.index

# Pre-compute ATR percentiles for different lookbacks
print("Computing ATR percentiles...")
atr_series=pd.Series(atr14, index=df5.index)
atr_pcts={}
for lb in [50,100,200]:
    atr_pcts[lb]=atr_series.rolling(lb).quantile(0.01)  # placeholder, computed per threshold below

# Detect base signals
print("Detecting ChoCH signals...")
macro_sh1=np.nan; macro_sh2=np.nan; macro_sl1=np.nan; macro_sl2=np.nan
market_trend=0; last_bull=-999; last_bear=-999
raw=[]

for i in range(CHOCH_PIVOT_LEN*2+5, N):
    ts=idx[i]
    if not np.isnan(macro_ph[i]): macro_sh2=macro_sh1; macro_sh1=macro_ph[i]
    if not np.isnan(macro_pl[i]): macro_sl2=macro_sl1; macro_sl1=macro_pl[i]
    if not any(np.isnan(v) for v in [macro_sh1,macro_sh2,macro_sl1,macro_sl2]):
        if macro_sh1>macro_sh2 and macro_sl1>macro_sl2: market_trend=1
        elif macro_sh1<macro_sh2 and macro_sl1<macro_sl2: market_trend=-1

    bear_r=(market_trend==1 and not np.isnan(macro_sl1) and
            c[i]<macro_sl1 and c[i-1]>=macro_sl1 and (i-last_bear)>CHOCH_COOLDOWN)
    bull_r=(market_trend==-1 and not np.isnan(macro_sh1) and
            c[i]>macro_sh1 and c[i-1]<=macro_sh1 and (i-last_bull)>CHOCH_COOLDOWN)
    if bear_r: last_bear=i
    if bull_r: last_bull=i
    if not in_ny(ts): continue

    if bear_r:
        sl=macro_sl1 if not np.isnan(macro_sl1) else c[i]+MANUAL_SL_DIST
        if abs(sl-c[i])>MANUAL_SL_DIST*CHOCH_SL_CAP: sl=c[i]+MANUAL_SL_DIST
        d=abs(sl-c[i])
        if SL_DEAD_MIN<=d<=SL_DEAD_MAX: continue
        raw.append({"bar_i":i,"dir":"sell","entry":c[i],"sl":sl,"atr":atr14[i]})
    if bull_r:
        sl=macro_sh1 if not np.isnan(macro_sh1) else c[i]-MANUAL_SL_DIST
        if abs(c[i]-sl)>MANUAL_SL_DIST*CHOCH_SL_CAP: sl=c[i]-MANUAL_SL_DIST
        d=abs(c[i]-sl)
        if SL_DEAD_MIN<=d<=SL_DEAD_MAX: continue
        raw.append({"bar_i":i,"dir":"buy","entry":c[i],"sl":sl,"atr":atr14[i]})

raw_df=pd.DataFrame(raw)
print(f"Base signals: {len(raw_df)}\n")

def simulate(sigs):
    if sigs.empty: return pd.DataFrame()
    sig_map={}
    for _,row in sigs.iterrows():
        bi=int(row.bar_i)
        if bi not in sig_map: sig_map[bi]=[]
        sig_map[bi].append(row)
    trades=[]; in_trade=False; entry_p=0; init_sl=0; direction=0
    fr=INITIAL_CAPITAL*RISK_PCT
    for i in range(N):
        if in_trade:
            rd=abs(entry_p-init_sl)
            if rd<=0: in_trade=False; continue
            sl_hit=(direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl)
            t2_hit=(direction==1 and h[i]>=entry_p+rd*T2_RR) or (direction==-1 and l[i]<=entry_p-rd*T2_RR)
            if t2_hit:
                trades.append({"pnl_usd":round(fr*T2_RR,2),"outcome":"T2"})
                in_trade=False; continue
            if sl_hit:
                trades.append({"pnl_usd":round(-fr,2),"outcome":"SL"})
                in_trade=False; continue
        if not in_trade and i in sig_map:
            row=sig_map[i][0]
            entry_p=row.entry; init_sl=row.sl
            direction=1 if row.dir=="buy" else -1; in_trade=True
    return pd.DataFrame(trades)

def stats(df_t, months):
    if df_t.empty: return {"n":0,"wr":0,"net":0,"pf":0,"mdd":0,"pm":0,"sl_pct":0}
    n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum()
    net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum()
    gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
    pf=round(gw/gl,2) if gl>0 else 999; wr=round(wins/n*100,1)
    eq=np.concatenate([[INITIAL_CAPITAL],INITIAL_CAPITAL+df_t.pnl_usd.cumsum().values])
    peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
    return {"n":n,"wr":wr,"net":round(net,2),"pf":pf,"mdd":mdd,
            "pm":round(net/months,2),"sl_pct":round(losses/n*100,1)}

# ── ATR Percentile Grid ────────────────────────────────────────────────────
print(f"  {'='*76}")
print(f"  SIDEWAYS FILTER — ATR percentile threshold x lookback window")
print(f"  {'='*76}")
print(f"  {'Config':<26} {'Blocked':>8} {'Sigs':>6} {'Trd':>5} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8}")
print(f"  {'-'*74}")

# Baseline
t_base=simulate(raw_df); r_base=stats(t_base,months)
print(f"  {'Baseline (no sideways)':<26} {'0':>8} {len(raw_df):>6} {r_base['n']:>5} "
      f"{r_base['wr']:>5}% {r_base['net']:>9} {r_base['pf']:>5} {r_base['mdd']:>6}% {r_base['pm']:>8}")

best_pm=r_base['pm']; best_cfg=""
results=[]

for lb in [50, 100, 200]:
    atr_roll=atr_series.rolling(lb)
    for pct in [20, 25, 30, 35, 40, 50]:
        threshold=atr_roll.quantile(pct/100)
        # Apply filter: keep signals where ATR >= threshold at that bar
        threshold_at_bar=threshold.values
        mask=[]
        for _,row in raw_df.iterrows():
            bi=int(row.bar_i)
            thr=threshold_at_bar[bi] if not np.isnan(threshold_at_bar[bi]) else 0
            mask.append(atr14[bi] >= thr)
        filtered=raw_df[mask].copy()
        blocked=len(raw_df)-len(filtered)
        t=simulate(filtered); r=stats(t,months)
        flag=" <--" if r['pm']>best_pm and r['n']>20 else ""
        if r['pm']>best_pm and r['n']>20: best_pm=r['pm']; best_cfg=f"LB={lb} PCT={pct}"
        results.append((lb,pct,blocked,len(filtered),r))
        print(f"  {'LB='+str(lb)+' PCT='+str(pct)+'%':<26} {blocked:>8} {len(filtered):>6} {r['n']:>5} "
              f"{r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8}{flag}")
    print(f"  {'-'*74}")

print(f"  {'='*76}")
print(f"\n  Baseline: WR {r_base['wr']}% | PF {r_base['pf']} | MaxDD {r_base['mdd']}% | ${r_base['pm']}/mo")
if best_cfg:
    print(f"  Best    : {best_cfg} -> ${best_pm}/mo")
else:
    print(f"  Best    : Baseline (no sideways filter needed)")

# Show what % of time market is "sideways" at different thresholds
print(f"\n  Market sideways % by threshold (LB=100):")
atr_roll100=atr_series.rolling(100)
for pct in [20,25,30,35,40,50]:
    thr=atr_roll100.quantile(pct/100)
    sideways_pct=round((atr_series<thr).mean()*100,1)
    print(f"  PCT={pct}% threshold: market sideways {sideways_pct}% of time")
