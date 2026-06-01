"""
ICT ChoCH — Tier 1 Optimized Backtest
Improvements vs baseline:
  1. HTF alignment ON    — ChoCH only in HTF direction
  2. Session filter      — NY only 18:30-23:30 IST (was already present)
  3. ATR-based SL        — entry ± 1.5x ATR (replaces noisy structural pivot SL)
  4. SL dead zone        — skip if SL dist in 5-8pt range (spread/slippage kills R:R)

Baseline (no filters, structural SL):
  678 trades | 46.5% WR | +$10,263 | PF 1.65 | MaxDD 7.6% | +$429/mo
"""

import pandas as pd, numpy as np, pytz

# ── Config ─────────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 5000.0
RISK_PCT        = 0.010     # 1.0% = $50 fixed
T1_RR           = 1.5
T2_RR           = 2.0
TRAIL_BE_RR     = 1.0

CHOCH_PIVOT_LEN = 5
CHOCH_COOLDOWN  = 5
HTF_MA_LEN      = 50

# Tier 1 params
ATR_SL_MULT     = 1.5       # SL = entry ± ATR * this
ATR_SL_MAX      = 25.0      # hard cap on SL distance (pts)
SL_DEAD_MIN     = 5.0       # dead zone: skip if SL dist >= this
SL_DEAD_MAX     = 8.0       # dead zone: skip if SL dist <= this

IST = pytz.timezone("Asia/Kolkata")

# ── Helpers ────────────────────────────────────────────────────────────────
def sma(s, n): return s.rolling(n).mean()
def wilder_atr(df, n=14):
    tr = pd.concat([df.high-df.low,
                    (df.high-df.close.shift(1)).abs(),
                    (df.low-df.close.shift(1)).abs()], axis=1).max(axis=1)
    r = np.full(len(tr), np.nan); r[n-1] = tr.iloc[:n].mean()
    for i in range(n, len(tr)): r[i] = (1/n)*tr.iloc[i]+(1-1/n)*r[i-1]
    return pd.Series(r, index=df.index)

def pivot_highs(arr, n):
    N=len(arr); ph=np.full(N, np.nan)
    for i in range(2*n, N):
        c=i-n; w=arr[i-2*n:i+1]
        if arr[c]==np.max(w) and np.sum(w==arr[c])==1: ph[i]=arr[c]
    return ph

def pivot_lows(arr, n):
    N=len(arr); pl=np.full(N, np.nan)
    for i in range(2*n, N):
        c=i-n; w=arr[i-2*n:i+1]
        if arr[c]==np.min(w) and np.sum(w==arr[c])==1: pl[i]=arr[c]
    return pl

def load_csv(path):
    df=pd.read_csv(path)
    df.columns=[c.strip().lower().replace(" ","_") for c in df.columns]
    if "time" in df.columns:
        df["ts"]=pd.to_datetime(df["time"].astype(np.int64), unit="s", utc=True)
    elif "datetime" in df.columns:
        df["ts"]=pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("ts").sort_index()[["open","high","low","close"]].astype(float)

def in_ny(ts):
    t=ts.tz_convert(IST); m=t.hour*60+t.minute
    return 18*60+30 <= m < 23*60+30

# ── Load ───────────────────────────────────────────────────────────────────
print("Loading data...")
df5  = load_csv(r"D:\Trade\tvHistoryData\gold_24m.csv")
df1h = load_csv(r"D:\Trade\tvHistoryData\gold_24m_1h.csv")
start=max(df5.index[0], df1h.index[0])
end  =min(df5.index[-1], df1h.index[-1])
df5=df5[start:end]; df1h=df1h[start:end]
months=round((end-start).days/30.5, 1)
print(f"Period: {df5.index[0].date()} to {df5.index[-1].date()}  ({months} months, {len(df5):,} bars)")

# HTF SMA50
h1_sma=sma(df1h.close, HTF_MA_LEN).rename("htf_ma").reset_index()
merged=pd.merge_asof(df5[[]].reset_index().sort_values("ts"),
                     h1_sma.sort_values("ts"), on="ts",
                     direction="backward").set_index("ts")
df5["htf_ma"]  =merged["htf_ma"].reindex(df5.index)
df5["htf_bull"]=(df5.close > df5.htf_ma).fillna(False)
df5["htf_bear"]=(df5.close < df5.htf_ma).fillna(False)
df5["atr14"]   =wilder_atr(df5)

# Pivots
print("Computing pivots...")
h=df5.high.values; l=df5.low.values
o=df5.open.values; c=df5.close.values; N=len(df5)
macro_ph=pivot_highs(h, CHOCH_PIVOT_LEN)
macro_pl=pivot_lows(l,  CHOCH_PIVOT_LEN)
htf_bull=df5.htf_bull.values.astype(bool)
htf_bear=df5.htf_bear.values.astype(bool)
atr14=df5.atr14.values
idx=df5.index

# ── Detection loop ─────────────────────────────────────────────────────────
print("Running optimized backtest (Tier 1 filters)...")
macro_sh1=np.nan; macro_sh2=np.nan
macro_sl1=np.nan; macro_sl2=np.nan
market_trend=0
last_bull_choch_bar=-999; last_bear_choch_bar=-999
signals=[]; blocked={"htf":0,"dead":0,"cap":0}

for i in range(CHOCH_PIVOT_LEN*2+5, N):
    ts=idx[i]; a=atr14[i] if not np.isnan(atr14[i]) else 1.0

    if not np.isnan(macro_ph[i]): macro_sh2=macro_sh1; macro_sh1=macro_ph[i]
    if not np.isnan(macro_pl[i]): macro_sl2=macro_sl1; macro_sl1=macro_pl[i]

    if not any(np.isnan(v) for v in [macro_sh1,macro_sh2,macro_sl1,macro_sl2]):
        if macro_sh1>macro_sh2 and macro_sl1>macro_sl2: market_trend=1
        elif macro_sh1<macro_sh2 and macro_sl1<macro_sl2: market_trend=-1

    # Raw ChoCH detection (no filters yet)
    bear_choch_raw = (market_trend==1  and not np.isnan(macro_sl1)
                      and c[i]<macro_sl1 and c[i-1]>=macro_sl1
                      and (i-last_bear_choch_bar)>CHOCH_COOLDOWN)
    bull_choch_raw = (market_trend==-1 and not np.isnan(macro_sh1)
                      and c[i]>macro_sh1 and c[i-1]<=macro_sh1
                      and (i-last_bull_choch_bar)>CHOCH_COOLDOWN)

    if bear_choch_raw: last_bear_choch_bar=i
    if bull_choch_raw: last_bull_choch_bar=i

    # Session filter
    if not in_ny(ts): continue

    # ── TIER 1 FILTER 1: HTF Alignment ──────────────────────────────────
    bear_choch = bear_choch_raw and htf_bear[i]   # SHORT only in HTF bear
    bull_choch = bull_choch_raw and htf_bull[i]   # LONG  only in HTF bull

    if bear_choch_raw and not htf_bear[i]: blocked["htf"]+=1
    if bull_choch_raw and not htf_bull[i]: blocked["htf"]+=1

    # ── TIER 1 FILTER 2+3: ATR SL + Dead zone ───────────────────────────
    if bear_choch:
        sl       = c[i] + a * ATR_SL_MULT
        sl_dist  = sl - c[i]
        if sl_dist > ATR_SL_MAX:
            blocked["cap"]+=1; bear_choch=False
        elif SL_DEAD_MIN <= sl_dist <= SL_DEAD_MAX:
            blocked["dead"]+=1; bear_choch=False
        else:
            signals.append({"bar_i":i,"ts":ts,"direction":"sell",
                            "entry":c[i],"sl":sl,"sl_dist":round(sl_dist,2)})

    if bull_choch:
        sl       = c[i] - a * ATR_SL_MULT
        sl_dist  = c[i] - sl
        if sl_dist > ATR_SL_MAX:
            blocked["cap"]+=1; bull_choch=False
        elif SL_DEAD_MIN <= sl_dist <= SL_DEAD_MAX:
            blocked["dead"]+=1; bull_choch=False
        else:
            signals.append({"bar_i":i,"ts":ts,"direction":"buy",
                            "entry":c[i],"sl":sl,"sl_dist":round(sl_dist,2)})

sig_df=pd.DataFrame(signals)
total_raw = len(sig_df)+blocked["htf"]+blocked["dead"]+blocked["cap"]
print(f"\nSignal pipeline:")
print(f"  Raw ChoCH (NY session)   : {total_raw}")
print(f"  Blocked HTF misaligned   : {blocked['htf']} ({round(blocked['htf']/max(total_raw,1)*100,1)}%)")
print(f"  Blocked SL dead zone     : {blocked['dead']} ({round(blocked['dead']/max(total_raw,1)*100,1)}%)")
print(f"  Blocked SL too large     : {blocked['cap']} ({round(blocked['cap']/max(total_raw,1)*100,1)}%)")
print(f"  Final signals            : {len(sig_df)}")

# ── Simulate ───────────────────────────────────────────────────────────────
def simulate(sig_df):
    if sig_df.empty: return pd.DataFrame()
    sig_map={}
    for _,row in sig_df.iterrows():
        bi=int(row.bar_i)
        if bi not in sig_map: sig_map[bi]=[]
        sig_map[bi].append(row)

    trades=[]; in_trade=False; entry_p=0; init_sl=0; direction=0
    t1_done=False; fixed_risk=INITIAL_CAPITAL*RISK_PCT

    for i in range(N):
        if in_trade:
            rd=abs(entry_p-init_sl)
            if rd<=0: in_trade=False; continue
            mv=(c[i]-entry_p)*direction; rm=mv/rd
            if rm>=TRAIL_BE_RR and init_sl!=entry_p: init_sl=entry_p
            if not t1_done:
                if (direction==1 and h[i]>=entry_p+rd*T1_RR) or \
                   (direction==-1 and l[i]<=entry_p-rd*T1_RR): t1_done=True
            sl_hit=(direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl)
            t2_hit=(direction==1 and h[i]>=entry_p+rd*T2_RR) or \
                   (direction==-1 and l[i]<=entry_p-rd*T2_RR)
            if t2_hit:
                pnl=fixed_risk*T1_RR*0.5+fixed_risk*T2_RR*0.5 if t1_done else fixed_risk*T2_RR
                trades.append({"entry_time":idx[i-1],"exit_time":idx[i],
                               "direction":"LONG" if direction==1 else "SHORT",
                               "pnl_usd":round(pnl,2),"outcome":"T2","t1_hit":t1_done})
                in_trade=False; t1_done=False; continue
            if sl_hit:
                if init_sl==entry_p: pnl=fixed_risk*T1_RR*0.5 if t1_done else 0; reason="BE"
                else: pnl=-fixed_risk+(fixed_risk*T1_RR*0.5 if t1_done else 0); reason="SL"
                trades.append({"entry_time":idx[i-1],"exit_time":idx[i],
                               "direction":"LONG" if direction==1 else "SHORT",
                               "pnl_usd":round(pnl,2),"outcome":reason,"t1_hit":t1_done})
                in_trade=False; t1_done=False; continue
        if not in_trade and i in sig_map:
            row=sig_map[i][0]
            entry_p=row.entry; init_sl=row.sl
            direction=1 if row.direction=="buy" else -1
            t1_done=False; in_trade=True

    return pd.DataFrame(trades)

trades=simulate(sig_df)

# ── Stats ──────────────────────────────────────────────────────────────────
def full_stats(df_t, label, months):
    if df_t.empty: print(f"\n  {label}: no trades"); return {}
    n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum()
    be=(df_t.pnl_usd==0).sum()
    net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum()
    gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
    pf=round(gw/gl,2) if gl>0 else 999
    wr=round(wins/n*100,1); nl=round((wins+be)/n*100,1)
    eq=np.concatenate([[INITIAL_CAPITAL],INITIAL_CAPITAL+df_t.pnl_usd.cumsum().values])
    peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
    pm=round(net/months,2)
    print(f"\n  {'='*60}")
    print(f"  {label}")
    print(f"  {'-'*60}")
    print(f"  Trades   : {n}  ({round(n/months,1)}/month)")
    print(f"  W/L/BE   : {wins} / {losses} / {be}")
    print(f"  Win Rate : {wr}%   |  Not-Loss: {nl}%")
    print(f"  Net P&L  : ${round(net,2):+}  ({round(net/INITIAL_CAPITAL*100,1)}% on $5k)")
    print(f"  Net/month: ${pm}")
    print(f"  PF       : {pf}")
    print(f"  Max DD   : {mdd}%")
    for oc,lbl in [("T2","Full TP"),("BE","Break-even"),("SL","Stop Loss")]:
        sub=df_t[df_t.outcome==oc]
        if len(sub):
            print(f"  {lbl:<12}: {len(sub):>3} ({round(len(sub)/n*100,1)}%)  avg ${round(sub.pnl_usd.mean(),2):+.2f}")
    return {"n":n,"wr":wr,"nl":nl,"net":round(net,2),"pf":pf,"mdd":mdd,"pm":pm}

r=full_stats(trades, "ICT ChoCH — Tier 1 Optimized", months)

# SL distance distribution
if not sig_df.empty:
    print(f"\n  SL distance stats (ATR-based):")
    print(f"  Mean : {sig_df.sl_dist.mean():.1f} pts")
    print(f"  Min  : {sig_df.sl_dist.min():.1f} pts")
    print(f"  Max  : {sig_df.sl_dist.max():.1f} pts")
    print(f"  <10pt: {(sig_df.sl_dist<10).sum()}")
    print(f"  10-20: {((sig_df.sl_dist>=10)&(sig_df.sl_dist<20)).sum()}")
    print(f"  >20pt: {(sig_df.sl_dist>=20).sum()}")

# Comparison
print(f"\n\n  {'='*65}")
print(f"  COMPARISON — ChoCH Baseline vs Tier 1 Optimized")
print(f"  {'='*65}")
print(f"  {'Version':<35} {'WR%':>5} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8}")
print(f"  {'-'*62}")
print(f"  {'Baseline (no HTF, struct SL)':<35} {'46.5':>5}% {'$10263':>9} {'1.65':>5} {'7.6':>6}% {'$429':>8}")
if r:
    print(f"  {'Tier 1 (HTF+ATR SL+dead zone)':<35} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8}")
print(f"  {'S2-ZonePyramid (baseline ref)':<35} {'40.8':>5}% {'$2513':>9} {'1.28':>5} {'20.3':>6}% {'$105':>8}")
print(f"  {'='*65}")
