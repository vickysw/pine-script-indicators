"""ChoCH pivot length grid search: 2,3,5,7,10,15,20 — dead zone ON, structural SL."""
import pandas as pd, numpy as np, pytz

INITIAL_CAPITAL=5000.0; RISK_PCT=0.010
T1_RR=1.5; T2_RR=2.0; TRAIL_BE_RR=1.0
HTF_MA_LEN=50; CHOCH_COOLDOWN=5
MANUAL_SL_DIST=5.0; CHOCH_SL_CAP=3.0
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

print("Loading data...")
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
atr14=df5.atr14.values; idx=df5.index

def run_pivot_len(plen):
    macro_ph=pivot_highs(h,plen); macro_pl=pivot_lows(l,plen)
    macro_sh1=np.nan; macro_sh2=np.nan; macro_sl1=np.nan; macro_sl2=np.nan
    market_trend=0; last_bull=-999; last_bear=-999
    signals=[]

    for i in range(plen*2+5,N):
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
            signals.append({"bar_i":i,"dir":"sell","entry":c[i],"sl":sl})
        if bull_r:
            sl=macro_sh1 if not np.isnan(macro_sh1) else c[i]-MANUAL_SL_DIST
            if abs(c[i]-sl)>MANUAL_SL_DIST*CHOCH_SL_CAP: sl=c[i]-MANUAL_SL_DIST
            d=abs(c[i]-sl)
            if SL_DEAD_MIN<=d<=SL_DEAD_MAX: continue
            signals.append({"bar_i":i,"dir":"buy","entry":c[i],"sl":sl})

    if not signals: return {"n":0,"wr":0,"net":0,"pf":0,"mdd":0,"pm":0,"sl_pct":0,"pf_score":0}

    sig_map={}
    for s in signals:
        bi=s["bar_i"]
        if bi not in sig_map: sig_map[bi]=[]
        sig_map[bi].append(s)

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
            entry_p=row["entry"]; init_sl=row["sl"]
            direction=1 if row["dir"]=="buy" else -1
            t1_done=False; in_trade=True

    if not trades: return {"n":0,"wr":0,"net":0,"pf":0,"mdd":0,"pm":0,"sl_pct":0,"pf_score":0}
    df_t=pd.DataFrame(trades)
    n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum()
    net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum(); gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
    pf=round(gw/gl,2) if gl>0 else 999; wr=round(wins/n*100,1)
    eq=np.concatenate([[INITIAL_CAPITAL],INITIAL_CAPITAL+df_t.pnl_usd.cumsum().values])
    peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
    sl_pct=round(losses/n*100,1); pm=round(net/months,2)
    pf_score=round(pf * (n/months),2)   # PF × trades/mo = combined score
    return {"n":n,"wr":wr,"net":round(net,2),"pf":pf,"mdd":mdd,"pm":pm,"sl_pct":sl_pct,"pf_score":pf_score}

print("\nRunning grid search: pivot_len in [2,3,5,7,10,15,20]...\n")
print(f"  {'Pivot':>6} {'Sigs':>6} {'Trd':>5} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8} {'SL%':>6} {'Score':>7}")
print(f"  {'-'*75}")

results=[]
for plen in [2,3,5,7,10,15,20]:
    macro_ph=pivot_highs(h,plen); macro_pl=pivot_lows(l,plen)
    # count raw signals first
    macro_sh1=np.nan; macro_sh2=np.nan; macro_sl1=np.nan; macro_sl2=np.nan
    mt=0; lb=-999; lbr=-999; raw_n=0
    for i in range(plen*2+5,N):
        if not np.isnan(macro_ph[i]): macro_sh2=macro_sh1; macro_sh1=macro_ph[i]
        if not np.isnan(macro_pl[i]): macro_sl2=macro_sl1; macro_sl1=macro_pl[i]
        if not any(np.isnan(v) for v in [macro_sh1,macro_sh2,macro_sl1,macro_sl2]):
            if macro_sh1>macro_sh2 and macro_sl1>macro_sl2: mt=1
            elif macro_sh1<macro_sh2 and macro_sl1<macro_sl2: mt=-1
        if in_ny(idx[i]):
            if mt==1 and not np.isnan(macro_sl1) and c[i]<macro_sl1 and c[i-1]>=macro_sl1 and (i-lb)>CHOCH_COOLDOWN: raw_n+=1; lb=i
            if mt==-1 and not np.isnan(macro_sh1) and c[i]>macro_sh1 and c[i-1]<=macro_sh1 and (i-lbr)>CHOCH_COOLDOWN: raw_n+=1; lbr=i

    r=run_pivot_len(plen)
    flag=" ***" if r.get("pf_score",0)==max([run_pivot_len(p).get("pf_score",0) for p in [2,3,5,7,10,15,20] if p==plen],default=0) else ""
    results.append((plen,raw_n,r))
    print(f"  {plen:>6} {raw_n:>6} {r['n']:>5} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8} {r['sl_pct']:>5}% {r.get('pf_score',0):>7}")

# Find best
best=max(results, key=lambda x: x[2].get("pm",0) if x[2]["n"]>10 else 0)
best_pf=max(results, key=lambda x: x[2].get("pf",0) if x[2]["n"]>10 else 0)
best_dd=min(results, key=lambda x: x[2].get("mdd",999) if x[2]["n"]>10 else 999)

print(f"\n  Best $/mo   : pivot_len={best[0]}  (${best[2]['pm']}/mo)")
print(f"  Best PF     : pivot_len={best_pf[0]}  (PF {best_pf[2]['pf']})")
print(f"  Lowest MaxDD: pivot_len={best_dd[0]}  ({best_dd[2]['mdd']}%)")
print(f"\n  Baseline (pivot=5, no dead zone): 46.5% WR | $10,263 | PF 1.65 | MaxDD 7.6% | $429/mo")
print(f"  Best E (pivot=5, dead zone):      47.0% WR | $11,363 | PF 1.72 | MaxDD 7.5% | $475/mo")
