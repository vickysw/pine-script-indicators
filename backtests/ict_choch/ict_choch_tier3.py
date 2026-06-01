"""
ChoCH Tier 3 — Fine-tuning
Base: pivot=2, dead zone 5-8pt, structural SL ($760/mo baseline)

Part 1 — T1/T2 RR grid:
  Current: T1=1.5R close 50%, trail BE, T2=2.0R
  Test all sensible RR combos + no-partial options

Part 2 — Retest Entry:
  Instead of entering on ChoCH bar close, wait up to N bars
  for price to pull back to broken level → better entry, better R:R
  Test retest_window = 3, 6, 10, 20 bars
"""
import pandas as pd, numpy as np, pytz

INITIAL_CAPITAL=5000.0; RISK_PCT=0.010
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

# ── Detect raw ChoCH signals ───────────────────────────────────────────────
print("Detecting signals...")
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
        raw.append({"bar_i":i,"dir":"sell","entry":c[i],"sl":sl,
                    "broken_level":macro_sl1})  # broken HL = retest target
    if bull_r:
        sl=macro_sh1 if not np.isnan(macro_sh1) else c[i]-MANUAL_SL_DIST
        if abs(c[i]-sl)>MANUAL_SL_DIST*CHOCH_SL_CAP: sl=c[i]-MANUAL_SL_DIST
        d=abs(c[i]-sl)
        if SL_DEAD_MIN<=d<=SL_DEAD_MAX: continue
        raw.append({"bar_i":i,"dir":"buy","entry":c[i],"sl":sl,
                    "broken_level":macro_sh1})  # broken LH = retest target

raw_df=pd.DataFrame(raw)
print(f"Base signals: {len(raw_df)}")

# ── Simulate with variable RR ──────────────────────────────────────────────
def simulate_rr(sig_df, t1_rr, t2_rr, use_t1=True):
    """use_t1=False = no partial close, pure T2."""
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
            # Trail to BE (only when T1 active)
            if use_t1 and rm>=1.0 and init_sl!=entry_p: init_sl=entry_p
            # T1
            if use_t1 and not t1_done:
                if (direction==1 and h[i]>=entry_p+rd*t1_rr) or (direction==-1 and l[i]<=entry_p-rd*t1_rr):
                    t1_done=True
            sl_hit=(direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl)
            t2_hit=(direction==1 and h[i]>=entry_p+rd*t2_rr) or (direction==-1 and l[i]<=entry_p-rd*t2_rr)
            if t2_hit:
                if use_t1:
                    pnl=fr*t1_rr*0.5+fr*t2_rr*0.5 if t1_done else fr*t2_rr
                else:
                    pnl=fr*t2_rr
                trades.append({"pnl_usd":round(pnl,2),"outcome":"T2"})
                in_trade=False; t1_done=False; continue
            if sl_hit:
                if use_t1 and init_sl==entry_p:
                    pnl=fr*t1_rr*0.5 if t1_done else 0; reason="BE"
                else:
                    pnl=-fr+(fr*t1_rr*0.5 if (use_t1 and t1_done) else 0); reason="SL"
                trades.append({"pnl_usd":round(pnl,2),"outcome":reason})
                in_trade=False; t1_done=False; continue
        if not in_trade and i in sig_map:
            row=sig_map[i][0]
            entry_p=row.entry; init_sl=row.sl
            direction=1 if row.dir=="buy" else -1
            t1_done=False; in_trade=True
    return pd.DataFrame(trades)

# ── Simulate with retest entry ─────────────────────────────────────────────
def simulate_retest(sig_df, retest_window, t1_rr=1.5, t2_rr=2.0):
    """Wait up to retest_window bars for price to pull back to broken level."""
    if sig_df.empty: return pd.DataFrame()

    # Build retest signal list: look ahead up to retest_window bars
    retest_sigs=[]
    for _,row in sig_df.iterrows():
        bi=int(row.bar_i); found=False
        broken=row.broken_level
        for j in range(bi+1, min(bi+retest_window+1, N)):
            if row.dir=="sell":
                # Price retests broken HL from below (high touches it)
                if h[j]>=broken and c[j]<broken:
                    new_sl=row.sl
                    d=abs(c[j]-new_sl)
                    if not (SL_DEAD_MIN<=d<=SL_DEAD_MAX):
                        retest_sigs.append({"bar_i":j,"dir":"sell",
                                           "entry":c[j],"sl":new_sl})
                    found=True; break
            else:
                # Price retests broken LH from above (low touches it)
                if l[j]<=broken and c[j]>broken:
                    new_sl=row.sl
                    d=abs(c[j]-new_sl)
                    if not (SL_DEAD_MIN<=d<=SL_DEAD_MAX):
                        retest_sigs.append({"bar_i":j,"dir":"buy",
                                           "entry":c[j],"sl":new_sl})
                    found=True; break
        # No retest found — skip (don't enter)

    if not retest_sigs: return pd.DataFrame()
    retest_df=pd.DataFrame(retest_sigs)

    sig_map={}
    for _,row in retest_df.iterrows():
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
            if rm>=1.0 and init_sl!=entry_p: init_sl=entry_p
            if not t1_done:
                if (direction==1 and h[i]>=entry_p+rd*t1_rr) or (direction==-1 and l[i]<=entry_p-rd*t1_rr):
                    t1_done=True
            sl_hit=(direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl)
            t2_hit=(direction==1 and h[i]>=entry_p+rd*t2_rr) or (direction==-1 and l[i]<=entry_p-rd*t2_rr)
            if t2_hit:
                pnl=fr*t1_rr*0.5+fr*t2_rr*0.5 if t1_done else fr*t2_rr
                trades.append({"pnl_usd":round(pnl,2),"outcome":"T2","fills":len(retest_sigs)})
                in_trade=False; t1_done=False; continue
            if sl_hit:
                if init_sl==entry_p: pnl=fr*t1_rr*0.5 if t1_done else 0; reason="BE"
                else: pnl=-fr+(fr*t1_rr*0.5 if t1_done else 0); reason="SL"
                trades.append({"pnl_usd":round(pnl,2),"outcome":reason,"fills":len(retest_sigs)})
                in_trade=False; t1_done=False; continue
        if not in_trade and i in sig_map:
            row=sig_map[i][0]
            entry_p=row.entry; init_sl=row.sl
            direction=1 if row.dir=="buy" else -1
            t1_done=False; in_trade=True
    return pd.DataFrame(trades), len(retest_sigs)

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

# ══ PART 1: RR Grid ═══════════════════════════════════════════════════════
print()
print(f"  {'='*78}")
print(f"  PART 1 — T1/T2 RR GRID  (1193 signals, pivot=2, dead zone)")
print(f"  {'='*78}")
print(f"  {'Config':<28} {'Trd':>5} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8} {'SL%':>6}")
print(f"  {'-'*74}")

rr_configs=[
    ("Baseline: T1=1.5R T2=2.0R",  1.5, 2.0, True),
    ("T1=1.0R  T2=2.0R",            1.0, 2.0, True),
    ("T1=1.5R  T2=3.0R",            1.5, 3.0, True),
    ("T1=2.0R  T2=3.0R",            2.0, 3.0, True),
    ("No T1    T2=2.0R (pure)",      1.5, 2.0, False),
    ("No T1    T2=3.0R (pure)",      1.5, 3.0, False),
    ("No T1    T2=4.0R (pure)",      1.5, 4.0, False),
]

rr_results=[]
for lbl,t1,t2,use_t1 in rr_configs:
    t=simulate_rr(raw_df,t1,t2,use_t1); r=stats(t,months)
    rr_results.append((lbl,r))
    flag=" <--" if r["pm"]==max([stats(simulate_rr(raw_df,a,b,u),months)["pm"]
                                  for _,a,b,u in rr_configs],default=0) else ""
    print(f"  {lbl:<28} {r['n']:>5} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8} {r['sl_pct']:>5}%")

best_rr=max(rr_results,key=lambda x:x[1]["pm"] if x[1]["n"]>10 else -9999)
print(f"\n  Best RR config: {best_rr[0]}  -> ${best_rr[1]['pm']}/mo  PF {best_rr[1]['pf']}")

# ══ PART 2: Retest Entry ══════════════════════════════════════════════════
print()
print(f"  {'='*78}")
print(f"  PART 2 — RETEST ENTRY  (wait N bars for pullback to broken level)")
print(f"  {'='*78}")
print(f"  {'Config':<30} {'Fills':>6} {'Trd':>5} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8}")
print(f"  {'-'*76}")

# Baseline: immediate entry
b=simulate_rr(raw_df,1.5,2.0,True); rb=stats(b,months)
print(f"  {'Baseline (immediate entry)':<30} {len(raw_df):>6} {rb['n']:>5} {rb['wr']:>5}% {rb['net']:>9} {rb['pf']:>5} {rb['mdd']:>6}% {rb['pm']:>8}")

for win in [3,6,10,20]:
    result=simulate_retest(raw_df,win,1.5,2.0)
    if isinstance(result,tuple): t,fills=result
    else: t=result; fills=0
    r=stats(t,months)
    pct_fill=round(fills/max(len(raw_df),1)*100,1)
    print(f"  {'Retest window='+str(win)+' bars':<30} {fills:>6} {r['n']:>5} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8}  ({pct_fill}% fill rate)")

print()
print(f"  {'='*78}")
print(f"  FINAL SUMMARY — Best of each Tier 3 option")
print(f"  {'='*78}")
print(f"  Baseline:        WR {rb['wr']}% | PF {rb['pf']} | MaxDD {rb['mdd']}% | ${rb['pm']}/mo")
print(f"  Best RR:         {best_rr[0]} -> PF {best_rr[1]['pf']} | ${best_rr[1]['pm']}/mo")
