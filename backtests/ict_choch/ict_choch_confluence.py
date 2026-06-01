"""
ChoCH Tier 2 — Confluence Filter Test
Base: pivot=2, dead zone 5-8pt, structural SL (best config so far)

Confluence variants:
  A. Baseline        — no confluence (current best: $760/mo)
  B. EMA9 only       — EMA9 > close (sell) / EMA9 < close (buy)
  C. EMA9+EMA15      — both EMAs above/below close
  D. VWAP only       — VWAP > close (sell) / VWAP < close (buy)
  E. EMA OR VWAP     — (EMA9+EMA15) OR VWAP  [same as S2-ZonePyramid]
  F. EMA AND VWAP    — both must agree (strictest)
"""
import pandas as pd, numpy as np, pytz

INITIAL_CAPITAL=5000.0; RISK_PCT=0.010
T1_RR=1.5; T2_RR=2.0; TRAIL_BE_RR=1.0
CHOCH_PIVOT_LEN=2; CHOCH_COOLDOWN=5
HTF_MA_LEN=50; MANUAL_SL_DIST=5.0; CHOCH_SL_CAP=3.0
SL_DEAD_MIN=5.0; SL_DEAD_MAX=8.0
IST=pytz.timezone("Asia/Kolkata")

def sma(s,n): return s.rolling(n).mean()
def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def wilder_atr(df,n=14):
    tr=pd.concat([df.high-df.low,(df.high-df.close.shift(1)).abs(),(df.low-df.close.shift(1)).abs()],axis=1).max(axis=1)
    r=np.full(len(tr),np.nan); r[n-1]=tr.iloc[:n].mean()
    for i in range(n,len(tr)): r[i]=(1/n)*tr.iloc[i]+(1-1/n)*r[i-1]
    return pd.Series(r,index=df.index)
def session_vwap(df):
    hlc3=(df.high+df.low+df.close)/3
    vals=np.empty(len(df)); cs=0; cn=0; pd_=None
    for i,(ts,v) in enumerate(zip(df.index,hlc3.values)):
        d=ts.date()
        if d!=pd_: cs=0; cn=0; pd_=d
        cs+=v; cn+=1; vals[i]=cs/cn
    return pd.Series(vals,index=df.index)
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
df5["ema9"]  = ema(df5.close, 9)
df5["ema15"] = ema(df5.close, 15)
df5["vwap"]  = session_vwap(df5)

h=df5.high.values; l=df5.low.values
o=df5.open.values; c=df5.close.values; N=len(df5)
macro_ph=pivot_highs(h,CHOCH_PIVOT_LEN); macro_pl=pivot_lows(l,CHOCH_PIVOT_LEN)
atr14=df5.atr14.values
e9=df5.ema9.values; e15=df5.ema15.values; vw=df5.vwap.values
idx=df5.index

print("Detecting ChoCH signals...")
macro_sh1=np.nan; macro_sh2=np.nan; macro_sl1=np.nan; macro_sl2=np.nan
market_trend=0; last_bull=-999; last_bear=-999
raw=[]

for i in range(CHOCH_PIVOT_LEN*2+5,N):
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

    # Confluence flags
    cf_e9_sell   = e9[i]  > c[i]
    cf_e15_sell  = e15[i] > c[i]
    cf_vwap_sell = vw[i]  > c[i]
    cf_e9_buy    = e9[i]  < c[i]
    cf_e15_buy   = e15[i] < c[i]
    cf_vwap_buy  = vw[i]  < c[i]

    # SL
    if bear_r:
        sl=macro_sl1 if not np.isnan(macro_sl1) else c[i]+MANUAL_SL_DIST
        if abs(sl-c[i])>MANUAL_SL_DIST*CHOCH_SL_CAP: sl=c[i]+MANUAL_SL_DIST
        d=abs(sl-c[i])
        if SL_DEAD_MIN<=d<=SL_DEAD_MAX: continue
        raw.append({"bar_i":i,"dir":"sell","entry":c[i],"sl":sl,
                    "e9_ok":cf_e9_sell,"e15_ok":cf_e15_sell,"vwap_ok":cf_vwap_sell})
    if bull_r:
        sl=macro_sh1 if not np.isnan(macro_sh1) else c[i]-MANUAL_SL_DIST
        if abs(c[i]-sl)>MANUAL_SL_DIST*CHOCH_SL_CAP: sl=c[i]-MANUAL_SL_DIST
        d=abs(c[i]-sl)
        if SL_DEAD_MIN<=d<=SL_DEAD_MAX: continue
        raw.append({"bar_i":i,"dir":"buy","entry":c[i],"sl":sl,
                    "e9_ok":cf_e9_buy,"e15_ok":cf_e15_buy,"vwap_ok":cf_vwap_buy})

raw_df=pd.DataFrame(raw)
print(f"Base signals (pivot=2, dead zone): {len(raw_df)}")

def apply_conf(df, mode):
    if mode=="none":   return df
    if mode=="e9":     return df[df.e9_ok]
    if mode=="e9e15":  return df[df.e9_ok & df.e15_ok]
    if mode=="vwap":   return df[df.vwap_ok]
    if mode=="or":     return df[(df.e9_ok & df.e15_ok) | df.vwap_ok]
    if mode=="and":    return df[(df.e9_ok & df.e15_ok) & df.vwap_ok]
    return df

def simulate(sigs):
    if sigs.empty: return pd.DataFrame()
    sig_map={}
    for _,row in sigs.iterrows():
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
            entry_p=row.entry; init_sl=row.sl
            direction=1 if row.dir=="buy" else -1
            t1_done=False; in_trade=True
    return pd.DataFrame(trades)

def stats(df_t, months):
    if df_t.empty: return {"n":0,"wr":0,"net":0,"pf":0,"mdd":0,"pm":0,"sl_pct":0}
    n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum()
    net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum(); gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
    pf=round(gw/gl,2) if gl>0 else 999; wr=round(wins/n*100,1)
    eq=np.concatenate([[INITIAL_CAPITAL],INITIAL_CAPITAL+df_t.pnl_usd.cumsum().values])
    peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
    return {"n":n,"wr":wr,"net":round(net,2),"pf":pf,"mdd":mdd,
            "pm":round(net/months,2),"sl_pct":round(losses/n*100,1)}

configs=[
    ("A. Baseline (no confluence)", "none"),
    ("B. EMA9 only",                "e9"),
    ("C. EMA9 + EMA15",             "e9e15"),
    ("D. VWAP only",                "vwap"),
    ("E. EMA9+15 OR VWAP",          "or"),
    ("F. EMA9+15 AND VWAP",         "and"),
]

print()
print(f"  {'='*76}")
print(f"  CONFLUENCE FILTER TEST  (base: pivot=2, dead zone 5-8pt)")
print(f"  {'='*76}")
print(f"  {'Config':<28} {'Sigs':>5} {'Trd':>5} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8} {'SL%':>6}")
print(f"  {'-'*74}")

results=[]
for lbl,mode in configs:
    sigs=apply_conf(raw_df,mode)
    t=simulate(sigs); r=stats(t,months)
    results.append((lbl,len(sigs),r))
    print(f"  {lbl:<28} {len(sigs):>5} {r['n']:>5} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8} {r['sl_pct']:>5}%")

print(f"  {'='*76}")

# Delta vs baseline
base=results[0][2]
print(f"\n  IMPROVEMENT vs Baseline (A):")
print(f"  {'Config':<28} {'WR delta':>9} {'PF delta':>9} {'$/mo delta':>11} {'Trades lost':>12}")
print(f"  {'-'*65}")
for lbl,nsigs,r in results[1:]:
    dwr=round(r['wr']-base['wr'],1)
    dpf=round(r['pf']-base['pf'],2)
    dpm=round(r['pm']-base['pm'],1)
    dt=r['n']-base['n']
    flag=" <--BEST" if dpf>0 and dpm>-50 else ""
    print(f"  {lbl:<28} {dwr:>+8}% {dpf:>+9} {dpm:>+10} {dt:>12}{flag}")

best_pf=max(results[1:],key=lambda x: x[2]['pf'] if x[2]['n']>10 else 0)
best_pm=max(results[1:],key=lambda x: x[2]['pm'] if x[2]['n']>10 else -9999)
print(f"\n  Best PF  : {best_pf[0]} — PF {best_pf[2]['pf']} ({best_pf[2]['pm']:+.0f}/mo)")
print(f"  Best $/mo: {best_pm[0]} — ${best_pm[2]['pm']}/mo (PF {best_pm[2]['pf']})")
print(f"\n  Confluence % breakdown (how often each fires):")
print(f"  EMA9 aligned   : {round(raw_df.e9_ok.mean()*100,1)}% of signals")
print(f"  EMA15 aligned  : {round(raw_df.e15_ok.mean()*100,1)}% of signals")
print(f"  VWAP aligned   : {round(raw_df.vwap_ok.mean()*100,1)}% of signals")
print(f"  EMA9+15 aligned: {round((raw_df.e9_ok&raw_df.e15_ok).mean()*100,1)}% of signals")
print(f"  OR aligned     : {round(((raw_df.e9_ok&raw_df.e15_ok)|raw_df.vwap_ok).mean()*100,1)}% of signals")
print(f"  AND aligned    : {round(((raw_df.e9_ok&raw_df.e15_ok)&raw_df.vwap_ok).mean()*100,1)}% of signals")
