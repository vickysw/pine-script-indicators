"""
ChoCH — Isolate each Tier 1 change to find what helps vs hurts.
Tests:
  A. Baseline         (no HTF, structural SL, no dead zone)
  B. HTF only         (add HTF filter, keep structural SL)
  C. ATR SL 1.5x      (no HTF, ATR SL, no dead zone)
  D. ATR SL 3.0x      (no HTF, ATR SL 3x, no dead zone)
  E. Dead zone only   (no HTF, structural SL, add 5-8pt dead zone)
  F. HTF + struct SL + dead zone  (best combo guess)
  G. HTF + ATR 3x + dead zone     (full Tier 1, better ATR mult)
"""
import pandas as pd, numpy as np, pytz

INITIAL_CAPITAL=5000.0; RISK_PCT=0.010
T1_RR=1.5; T2_RR=2.0; TRAIL_BE_RR=1.0
CHOCH_PIVOT_LEN=5; CHOCH_COOLDOWN=5; HTF_MA_LEN=50
MANUAL_SL_DIST=5.0; CHOCH_SL_CAP=3.0
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
merged=pd.merge_asof(df5[[]].reset_index().sort_values("ts"),h1_sma.sort_values("ts"),on="ts",direction="backward").set_index("ts")
df5["htf_ma"]=merged["htf_ma"].reindex(df5.index)
df5["htf_bull"]=(df5.close>df5.htf_ma).fillna(False)
df5["htf_bear"]=(df5.close<df5.htf_ma).fillna(False)
df5["atr14"]=wilder_atr(df5)

h=df5.high.values; l=df5.low.values; o=df5.open.values; c=df5.close.values; N=len(df5)
macro_ph=pivot_highs(h,CHOCH_PIVOT_LEN); macro_pl=pivot_lows(l,CHOCH_PIVOT_LEN)
htf_bull=df5.htf_bull.values.astype(bool); htf_bear=df5.htf_bear.values.astype(bool)
atr14=df5.atr14.values; idx=df5.index

# Pre-detect all raw ChoCH + store context
print("Detecting raw signals...")
macro_sh1=np.nan; macro_sh2=np.nan; macro_sl1=np.nan; macro_sl2=np.nan
market_trend=0; last_bull_bar=-999; last_bear_bar=-999
raw=[]

for i in range(CHOCH_PIVOT_LEN*2+5,N):
    ts=idx[i]; a=atr14[i] if not np.isnan(atr14[i]) else 1.0
    if not np.isnan(macro_ph[i]): macro_sh2=macro_sh1; macro_sh1=macro_ph[i]
    if not np.isnan(macro_pl[i]): macro_sl2=macro_sl1; macro_sl1=macro_pl[i]
    if not any(np.isnan(v) for v in [macro_sh1,macro_sh2,macro_sl1,macro_sl2]):
        if macro_sh1>macro_sh2 and macro_sl1>macro_sl2: market_trend=1
        elif macro_sh1<macro_sh2 and macro_sl1<macro_sl2: market_trend=-1

    bear_raw=(market_trend==1 and not np.isnan(macro_sl1) and
              c[i]<macro_sl1 and c[i-1]>=macro_sl1 and (i-last_bear_bar)>CHOCH_COOLDOWN)
    bull_raw=(market_trend==-1 and not np.isnan(macro_sh1) and
              c[i]>macro_sh1 and c[i-1]<=macro_sh1 and (i-last_bull_bar)>CHOCH_COOLDOWN)
    if bear_raw: last_bear_bar=i
    if bull_raw: last_bull_bar=i
    if not in_ny(ts): continue

    # structural SL
    struct_sl_sell=macro_sl1 if not np.isnan(macro_sl1) else c[i]+MANUAL_SL_DIST
    if abs(struct_sl_sell-c[i])>MANUAL_SL_DIST*CHOCH_SL_CAP: struct_sl_sell=c[i]+MANUAL_SL_DIST
    struct_sl_buy=macro_sh1 if not np.isnan(macro_sh1) else c[i]-MANUAL_SL_DIST
    if abs(c[i]-struct_sl_buy)>MANUAL_SL_DIST*CHOCH_SL_CAP: struct_sl_buy=c[i]-MANUAL_SL_DIST

    if bear_raw:
        raw.append({"bar_i":i,"ts":ts,"dir":"sell","entry":c[i],
                    "sl_struct":struct_sl_sell,
                    "sl_atr15":c[i]+a*1.5, "sl_atr30":c[i]+a*3.0,
                    "dist_struct":abs(struct_sl_sell-c[i]),
                    "dist_atr15":a*1.5, "dist_atr30":a*3.0,
                    "htf_ok":htf_bear[i]})
    if bull_raw:
        raw.append({"bar_i":i,"ts":ts,"dir":"buy","entry":c[i],
                    "sl_struct":struct_sl_buy,
                    "sl_atr15":c[i]-a*1.5, "sl_atr30":c[i]-a*3.0,
                    "dist_struct":abs(c[i]-struct_sl_buy),
                    "dist_atr15":a*1.5, "dist_atr30":a*3.0,
                    "htf_ok":htf_bull[i]})

raw_df=pd.DataFrame(raw)
print(f"Raw ChoCH (NY): {len(raw_df)}")

def apply_filters(df, use_htf=False, sl_type="struct", dead_min=0, dead_max=0, atr_cap=25):
    sigs=df.copy()
    if use_htf: sigs=sigs[sigs.htf_ok]
    sl_col = "sl_atr15" if sl_type=="atr15" else "sl_atr30" if sl_type=="atr30" else "sl_struct"
    dist_col= "dist_atr15" if sl_type=="atr15" else "dist_atr30" if sl_type=="atr30" else "dist_struct"
    sigs=sigs.assign(sl_used=sigs[sl_col], dist_used=sigs[dist_col])
    if sl_type in ("atr15","atr30"): sigs=sigs[sigs.dist_used<=atr_cap]
    if dead_max>0: sigs=sigs[~((sigs.dist_used>=dead_min)&(sigs.dist_used<=dead_max))]
    return sigs[["bar_i","ts","dir","entry","sl_used","dist_used"]].copy()

def simulate(sig_df):
    if sig_df.empty: return pd.DataFrame()
    sig_map={}
    for _,row in sig_df.iterrows():
        bi=int(row.bar_i)
        if bi not in sig_map: sig_map[bi]=[]
        sig_map[bi].append(row)
    trades=[]; in_trade=False; entry_p=0; init_sl=0; direction=0; t1_done=False
    fr=INITIAL_CAPITAL*RISK_PCT
    for i in range(N):
        if in_trade:
            rd=abs(entry_p-init_sl)
            if rd<=0: in_trade=False; continue
            mv=(c[i]-entry_p)*direction; rm=mv/rd
            if rm>=TRAIL_BE_RR and init_sl!=entry_p: init_sl=entry_p
            if not t1_done:
                if (direction==1 and h[i]>=entry_p+rd*T1_RR) or (direction==-1 and l[i]<=entry_p-rd*T1_RR): t1_done=True
            sl_hit=(direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl)
            t2_hit=(direction==1 and h[i]>=entry_p+rd*T2_RR) or (direction==-1 and l[i]<=entry_p-rd*T2_RR)
            if t2_hit:
                pnl=fr*T1_RR*0.5+fr*T2_RR*0.5 if t1_done else fr*T2_RR
                trades.append({"pnl_usd":round(pnl,2),"outcome":"T2"})
                in_trade=False; t1_done=False; continue
            if sl_hit:
                if init_sl==entry_p: pnl=fr*T1_RR*0.5 if t1_done else 0; reason="BE"
                else: pnl=-fr+(fr*T1_RR*0.5 if t1_done else 0); reason="SL"
                trades.append({"pnl_usd":round(pnl,2),"outcome":reason})
                in_trade=False; t1_done=False; continue
        if not in_trade and i in sig_map:
            row=sig_map[i][0]
            entry_p=row.entry; init_sl=row.sl_used
            direction=1 if row.dir=="buy" else -1
            t1_done=False; in_trade=True
    return pd.DataFrame(trades)

def quick_stats(df_t, months):
    if df_t.empty: return {"n":0,"wr":0,"net":0,"pf":0,"mdd":0,"pm":0,"sl_pct":0}
    n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum()
    net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum(); gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
    pf=round(gw/gl,2) if gl>0 else 999; wr=round(wins/n*100,1)
    eq=np.concatenate([[INITIAL_CAPITAL],INITIAL_CAPITAL+df_t.pnl_usd.cumsum().values])
    peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
    sl_pct=round(losses/n*100,1)
    return {"n":n,"wr":wr,"net":round(net,2),"pf":pf,"mdd":mdd,"pm":round(net/months,2),"sl_pct":sl_pct}

# Run all variants
configs=[
    ("A. Baseline",          dict(use_htf=False, sl_type="struct", dead_min=0,   dead_max=0)),
    ("B. HTF only",           dict(use_htf=True,  sl_type="struct", dead_min=0,   dead_max=0)),
    ("C. ATR 1.5x only",      dict(use_htf=False, sl_type="atr15",  dead_min=0,   dead_max=0)),
    ("D. ATR 3.0x only",      dict(use_htf=False, sl_type="atr30",  dead_min=0,   dead_max=0)),
    ("E. Dead zone only",     dict(use_htf=False, sl_type="struct", dead_min=5.0, dead_max=8.0)),
    ("F. HTF+struct+dead",    dict(use_htf=True,  sl_type="struct", dead_min=5.0, dead_max=8.0)),
    ("G. HTF+ATR3x+dead",     dict(use_htf=True,  sl_type="atr30",  dead_min=5.0, dead_max=8.0)),
    ("H. HTF+ATR1.5x+dead",   dict(use_htf=True,  sl_type="atr15",  dead_min=5.0, dead_max=8.0)),
]

print()
print(f"  {'='*80}")
print(f"  ISOLATION TEST — Each filter separately + combinations")
print(f"  {'='*80}")
print(f"  {'Config':<26} {'Sigs':>5} {'Trd':>5} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8} {'SL%':>6}")
print(f"  {'-'*77}")

best_pm=-9999; best_lbl=""
for lbl, cfg in configs:
    sigs=apply_filters(raw_df, **cfg)
    t=simulate(sigs)
    r=quick_stats(t, months)
    flag=" <--BEST" if r["pm"]>best_pm and r["n"]>10 else ""
    if r["pm"]>best_pm and r["n"]>10: best_pm=r["pm"]; best_lbl=lbl
    print(f"  {lbl:<26} {len(sigs):>5} {r['n']:>5} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8} {r['sl_pct']:>5}%{flag}")

print(f"  {'='*80}")
print(f"  {'S2-ZonePyramid (ref)':<26} {'':>5} {'250':>5} {'40.8':>5}% {'$2513':>9} {'1.28':>5} {'20.3':>6}% {'$105':>8} {'48.8':>5}%")
print(f"  {'='*80}")
print(f"\n  Best config: {best_lbl}  (${best_pm}/mo)")
