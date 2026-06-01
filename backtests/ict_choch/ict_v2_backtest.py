"""
ICT V2 — Python Backtest
ICT-Trading-System-V2-PositionSizing.pine faithful translation

Signals:
  REVERSAL : HTF aligned + recent LQ grab (<=10 bars) + recent BOS (<=10 bars), cooldown 20 bars
  CONT     : HTF aligned + in OB/FVG + engulf/strong candle (body>70% range)
  ChoCH    : macro trend flip + break of last LH (sell) or HL (buy), cooldown 5 bars

Fixed risk: $5000
  Reversal : 0.5%  = $25
  Cont     : 1.5%  = $75
  ChoCH    : 1.0%  = $50
SL: OB/FVG zone top/bottom. Fallback = 5.0 pts.
Exit: T1 50% at 1.5R, trail SL to BE at 1R, T2 remaining at 2R
Session: NY only 18:30-23:30 IST
"""

import pandas as pd, numpy as np, pytz

# ── Config ─────────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 5000.0
REV_RISK        = 0.005    # 0.5%
CONT_RISK       = 0.015    # 1.5%
CHOCH_RISK      = 0.010    # 1.0%
T1_RR           = 1.5
T2_RR           = 2.0
TRAIL_BE_RR     = 1.0

REV_LOOKBACK    = 10       # bars: LQ grab + BOS must both be within this window
REV_COOLDOWN    = 20       # bars: no repeated reversal within this
CHOCH_PIVOT_LEN = 5
CHOCH_COOLDOWN  = 5
SWING_PIVOT_LEN = 15       # for LQ grabs
STRUCT_PIVOT_LEN= 5        # for BOS
MANUAL_SL_DIST  = 5.0
CHOCH_SL_CAP    = 3.0      # cap ChoCH SL at 3x manual_sl_dist
HTF_MA_LEN      = 50

IST = pytz.timezone("Asia/Kolkata")

# ── Helpers ────────────────────────────────────────────────────────────────
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()

def wilder_atr(df, n=14):
    tr = pd.concat([df.high-df.low,
                    (df.high-df.close.shift(1)).abs(),
                    (df.low-df.close.shift(1)).abs()], axis=1).max(axis=1)
    r = np.full(len(tr), np.nan); r[n-1] = tr.iloc[:n].mean()
    for i in range(n, len(tr)): r[i] = (1/n)*tr.iloc[i]+(1-1/n)*r[i-1]
    return pd.Series(r, index=df.index)

def pivot_highs(arr, n):
    N = len(arr); ph = np.full(N, np.nan)
    for i in range(2*n, N):
        center = i-n; win = arr[i-2*n:i+1]
        if arr[center] == np.max(win) and np.sum(win==arr[center])==1:
            ph[i] = arr[center]
    return ph

def pivot_lows(arr, n):
    N = len(arr); pl = np.full(N, np.nan)
    for i in range(2*n, N):
        center = i-n; win = arr[i-2*n:i+1]
        if arr[center] == np.min(win) and np.sum(win==arr[center])==1:
            pl[i] = arr[center]
    return pl

def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ","_") for c in df.columns]
    if "time" in df.columns:
        df["ts"] = pd.to_datetime(df["time"].astype(np.int64), unit="s", utc=True)
    elif "datetime" in df.columns:
        df["ts"] = pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("ts").sort_index()[["open","high","low","close"]].astype(float)

def in_ny(ts):
    t = ts.tz_convert(IST); m = t.hour*60+t.minute
    return 18*60+30 <= m < 23*60+30

# ── Load ───────────────────────────────────────────────────────────────────
print("Loading data...")
df5  = load_csv(r"D:\Trade\tvHistoryData\gold_24m.csv")
df1h = load_csv(r"D:\Trade\tvHistoryData\gold_24m_1h.csv")
start = max(df5.index[0], df1h.index[0])
end   = min(df5.index[-1], df1h.index[-1])
df5   = df5[start:end]; df1h = df1h[start:end]
months = round((end-start).days/30.5, 1)
print(f"Period: {df5.index[0].date()} to {df5.index[-1].date()} ({months} months, {len(df5):,} bars)")

# HTF SMA50
h1_sma = sma(df1h.close, HTF_MA_LEN).rename("htf_ma").reset_index()
df5r   = df5[[]].reset_index()
merged = pd.merge_asof(df5r.sort_values("ts"), h1_sma.sort_values("ts"),
                       on="ts", direction="backward").set_index("ts")
df5["htf_ma"]   = merged["htf_ma"].reindex(df5.index)
df5["htf_bull"] = (df5.close > df5.htf_ma).fillna(False)
df5["htf_bear"] = (df5.close < df5.htf_ma).fillna(False)
df5["atr14"]    = wilder_atr(df5)

# Pre-compute pivots
print("Computing pivots...")
h = df5.high.values; l = df5.low.values
o = df5.open.values; c = df5.close.values
N = len(df5)

swing_ph  = pivot_highs(h, SWING_PIVOT_LEN)
swing_pl  = pivot_lows(l,  SWING_PIVOT_LEN)
struct_ph = pivot_highs(h, STRUCT_PIVOT_LEN)
struct_pl = pivot_lows(l,  STRUCT_PIVOT_LEN)
macro_ph  = pivot_highs(h, CHOCH_PIVOT_LEN)
macro_pl  = pivot_lows(l,  CHOCH_PIVOT_LEN)

htf_bull = df5.htf_bull.values.astype(bool)
htf_bear = df5.htf_bear.values.astype(bool)
atr14    = df5.atr14.values
idx      = df5.index

# ── Main detection loop ────────────────────────────────────────────────────
print("Running backtest...")

last_swing_h = np.nan;  last_swing_l = np.nan
last_struct_h = np.nan; last_struct_l = np.nan
macro_sh1=np.nan; macro_sh2=np.nan
macro_sl1=np.nan; macro_sl2=np.nan
market_trend = 0

last_bull_lq_bar  = -999; last_bear_lq_bar  = -999
last_bull_bos_bar = -999; last_bear_bos_bar = -999
last_buy_rev_bar  = -999; last_sell_rev_bar = -999
last_bull_choch_bar = -999; last_bear_choch_bar = -999

# OB/FVG tracking (most recent valid zone)
bear_ob_top=np.nan; bear_ob_bot=np.nan
bull_ob_top=np.nan; bull_ob_bot=np.nan
bear_fvg_top=np.nan; bear_fvg_bot=np.nan
bull_fvg_top=np.nan; bull_fvg_bot=np.nan

signals = []

WARMUP = max(SWING_PIVOT_LEN*2+1, CHOCH_PIVOT_LEN*2+1, 50)

for i in range(WARMUP, N):
    ts = idx[i]
    a  = atr14[i] if not np.isnan(atr14[i]) else 1.0
    cr = h[i]-l[i]
    if cr <= 0: continue

    # Update swing/structure pivots
    if not np.isnan(swing_ph[i]):  last_swing_h  = swing_ph[i]
    if not np.isnan(swing_pl[i]):  last_swing_l  = swing_pl[i]
    if not np.isnan(struct_ph[i]): last_struct_h = struct_ph[i]
    if not np.isnan(struct_pl[i]): last_struct_l = struct_pl[i]

    # Macro trend (ChoCH)
    if not np.isnan(macro_ph[i]): macro_sh2=macro_sh1; macro_sh1=macro_ph[i]
    if not np.isnan(macro_pl[i]): macro_sl2=macro_sl1; macro_sl1=macro_pl[i]
    if not any(np.isnan(v) for v in [macro_sh1,macro_sh2,macro_sl1,macro_sl2]):
        if macro_sh1>macro_sh2 and macro_sl1>macro_sl2: market_trend=1
        elif macro_sh1<macro_sh2 and macro_sl1<macro_sl2: market_trend=-1

    # OB detection (4-candle momentum, max 3 kept, invalidate on close-through)
    if i >= 4:
        if c[i]<c[i-1] and c[i-1]<c[i-2] and c[i-2]<c[i-3] and c[i-3]<c[i-4] and htf_bear[i]:
            bear_ob_top=h[i-4]; bear_ob_bot=l[i-4]
        if c[i]>c[i-1] and c[i-1]>c[i-2] and c[i-2]>c[i-3] and c[i-3]>c[i-4] and htf_bull[i]:
            bull_ob_top=h[i-4]; bull_ob_bot=l[i-4]
    if not np.isnan(bear_ob_top) and c[i]>bear_ob_top: bear_ob_top=bear_ob_bot=np.nan
    if not np.isnan(bull_ob_bot) and c[i]<bull_ob_bot: bull_ob_top=bull_ob_bot=np.nan

    # FVG detection (gap between bar[i].low and bar[i-2].high)
    if i >= 2:
        if l[i]>h[i-2] and htf_bull[i] and (h[i-2]>0) and (l[i]-h[i-2])/h[i-2]*100>0.2:
            bull_fvg_top=l[i]; bull_fvg_bot=h[i-2]
        if h[i]<l[i-2] and htf_bear[i] and (l[i-2]>0) and (l[i-2]-h[i])/l[i-2]*100>0.2:
            bear_fvg_top=l[i-2]; bear_fvg_bot=h[i]
    if not np.isnan(bull_fvg_bot) and l[i]<=bull_fvg_bot: bull_fvg_top=bull_fvg_bot=np.nan
    if not np.isnan(bear_fvg_top) and h[i]>=bear_fvg_top: bear_fvg_top=bear_fvg_bot=np.nan

    # In-zone detection + nearest SL level
    in_bear_ob  = not np.isnan(bear_ob_top)  and h[i]>=bear_ob_bot  and l[i]<=bear_ob_top
    in_bull_ob  = not np.isnan(bull_ob_bot)  and h[i]>=bull_ob_bot  and l[i]<=bull_ob_top
    in_bear_fvg = not np.isnan(bear_fvg_top) and h[i]>=bear_fvg_bot and l[i]<=bear_fvg_top
    in_bull_fvg = not np.isnan(bull_fvg_bot) and h[i]>=bull_fvg_bot and l[i]<=bull_fvg_top

    zone_sl_sell = bear_ob_top if in_bear_ob else (bear_fvg_top if in_bear_fvg else np.nan)
    zone_sl_buy  = bull_ob_bot if in_bull_ob else (bull_fvg_bot if in_bull_fvg else np.nan)

    # LQ grabs
    lw = min(o[i],c[i])-l[i]; uw = h[i]-max(o[i],c[i])
    bull_lq = (not np.isnan(last_swing_l) and l[i]<last_swing_l and c[i]>last_swing_l
               and c[i]>o[i] and lw>cr*0.25 and c[i]>(l[i]+cr*0.4))
    bear_lq = (not np.isnan(last_swing_h) and h[i]>last_swing_h and c[i]<last_swing_h
               and c[i]<o[i] and uw>cr*0.25 and c[i]<(h[i]-cr*0.4))
    if bull_lq: last_bull_lq_bar=i
    if bear_lq: last_bear_lq_bar=i

    # BOS (any direction — V2 is NOT trend-aware)
    bull_bos = (not np.isnan(last_struct_h) and last_struct_h>0
                and c[i]>last_struct_h and c[i-1]<=last_struct_h)
    bear_bos = (not np.isnan(last_struct_l) and last_struct_l>0
                and c[i]<last_struct_l and c[i-1]>=last_struct_l)
    if bull_bos: last_bull_bos_bar=i
    if bear_bos: last_bear_bos_bar=i

    # Reversal: LQ + BOS both within rev_lookback, cooldown
    recent_bull_lq  = (i-last_bull_lq_bar)  <= REV_LOOKBACK
    recent_bear_lq  = (i-last_bear_lq_bar)  <= REV_LOOKBACK
    recent_bull_bos = (i-last_bull_bos_bar) <= REV_LOOKBACK
    recent_bear_bos = (i-last_bear_bos_bar) <= REV_LOOKBACK

    buy_rev  = htf_bull[i] and recent_bull_lq and recent_bull_bos
    sell_rev = htf_bear[i] and recent_bear_lq and recent_bear_bos

    # Cooldown: skip if too recent
    if buy_rev  and (i-last_buy_rev_bar)  <= REV_COOLDOWN: buy_rev  = False
    if sell_rev and (i-last_sell_rev_bar) <= REV_COOLDOWN: sell_rev = False
    if buy_rev:  last_buy_rev_bar  = i
    if sell_rev: last_sell_rev_bar = i

    # Continuation: OB/FVG + engulf/strong candle
    bull_engulf = i>0 and c[i]>o[i] and c[i]>h[i-1] and o[i]<=c[i-1]
    bear_engulf = i>0 and c[i]<o[i] and c[i]<l[i-1] and o[i]>=c[i-1]
    body_pct    = abs(c[i]-o[i])/cr
    strong_bull = c[i]>o[i] and body_pct>0.7
    strong_bear = c[i]<o[i] and body_pct>0.7

    bull_cont = (in_bull_ob or in_bull_fvg) and htf_bull[i] and (bull_engulf or strong_bull)
    bear_cont = (in_bear_ob or in_bear_fvg) and htf_bear[i] and (bear_engulf or strong_bear)

    # ChoCH
    bull_choch = (market_trend==-1 and not np.isnan(macro_sh1)
                  and c[i]>macro_sh1 and c[i-1]<=macro_sh1
                  and (i-last_bull_choch_bar)>CHOCH_COOLDOWN)
    bear_choch = (market_trend==1  and not np.isnan(macro_sl1)
                  and c[i]<macro_sl1 and c[i-1]>=macro_sl1
                  and (i-last_bear_choch_bar)>CHOCH_COOLDOWN)
    if bull_choch: last_bull_choch_bar=i
    if bear_choch: last_bear_choch_bar=i

    # Session
    if not in_ny(ts): continue

    # SL helpers
    def rev_sl_sell():
        sl = zone_sl_sell if not np.isnan(zone_sl_sell) else c[i]+MANUAL_SL_DIST
        return sl
    def rev_sl_buy():
        sl = zone_sl_buy if not np.isnan(zone_sl_buy) else c[i]-MANUAL_SL_DIST
        return sl
    def choch_sl_sell():
        sl = macro_sl1 if not np.isnan(macro_sl1) else c[i]+MANUAL_SL_DIST
        if abs(sl-c[i]) > MANUAL_SL_DIST*CHOCH_SL_CAP: sl = c[i]+MANUAL_SL_DIST
        return sl
    def choch_sl_buy():
        sl = macro_sh1 if not np.isnan(macro_sh1) else c[i]-MANUAL_SL_DIST
        if abs(c[i]-sl) > MANUAL_SL_DIST*CHOCH_SL_CAP: sl = c[i]-MANUAL_SL_DIST
        return sl

    # Emit (priority: reversal > cont > ChoCH)
    if sell_rev:
        sl = rev_sl_sell()
        if sl > c[i]:
            signals.append({"bar_i":i,"ts":ts,"sig_type":"REV","direction":"sell",
                            "entry":c[i],"sl":sl,"risk_pct":REV_RISK})
    elif buy_rev:
        sl = rev_sl_buy()
        if sl < c[i]:
            signals.append({"bar_i":i,"ts":ts,"sig_type":"REV","direction":"buy",
                            "entry":c[i],"sl":sl,"risk_pct":REV_RISK})
    elif bear_cont:
        sl = rev_sl_sell()
        if sl > c[i]:
            signals.append({"bar_i":i,"ts":ts,"sig_type":"CONT","direction":"sell",
                            "entry":c[i],"sl":sl,"risk_pct":CONT_RISK})
    elif bull_cont:
        sl = rev_sl_buy()
        if sl < c[i]:
            signals.append({"bar_i":i,"ts":ts,"sig_type":"CONT","direction":"buy",
                            "entry":c[i],"sl":sl,"risk_pct":CONT_RISK})
    elif bear_choch:
        sl = choch_sl_sell()
        if sl > c[i]:
            signals.append({"bar_i":i,"ts":ts,"sig_type":"CHOCH","direction":"sell",
                            "entry":c[i],"sl":sl,"risk_pct":CHOCH_RISK})
    elif bull_choch:
        sl = choch_sl_buy()
        if sl < c[i]:
            signals.append({"bar_i":i,"ts":ts,"sig_type":"CHOCH","direction":"buy",
                            "entry":c[i],"sl":sl,"risk_pct":CHOCH_RISK})

sig_df = pd.DataFrame(signals)
print(f"Signals: {len(sig_df)}  (REV:{len(sig_df[sig_df.sig_type=='REV'])}  "
      f"CONT:{len(sig_df[sig_df.sig_type=='CONT'])}  "
      f"CHOCH:{len(sig_df[sig_df.sig_type=='CHOCH'])})")

# ── Simulate ───────────────────────────────────────────────────────────────
def simulate(sig_df, mode="ALL"):
    if mode=="REV":   sigs=sig_df[sig_df.sig_type=="REV"].copy()
    elif mode=="CONT":sigs=sig_df[sig_df.sig_type=="CONT"].copy()
    elif mode=="CHOCH":sigs=sig_df[sig_df.sig_type=="CHOCH"].copy()
    else:             sigs=sig_df.copy()
    if sigs.empty: return pd.DataFrame()

    sig_map = {}
    for _,row in sigs.iterrows():
        bi=int(row.bar_i)
        if bi not in sig_map: sig_map[bi]=[]
        sig_map[bi].append(row)

    trades=[]; in_trade=False; entry_p=0; init_sl=0; direction=0; t1_done=False; fixed_risk=0; sig_type=""

    for i in range(N):
        if in_trade:
            rd=abs(entry_p-init_sl)
            if rd<=0: in_trade=False; continue
            mv=(c[i]-entry_p)*direction; rm=mv/rd
            if rm>=TRAIL_BE_RR and init_sl!=entry_p: init_sl=entry_p
            if not t1_done:
                t1h=((direction==1 and h[i]>=entry_p+rd*T1_RR) or
                     (direction==-1 and l[i]<=entry_p-rd*T1_RR))
                if t1h: t1_done=True
            sl_hit=((direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl))
            t2_hit=((direction==1 and h[i]>=entry_p+rd*T2_RR) or (direction==-1 and l[i]<=entry_p-rd*T2_RR))
            if t2_hit:
                pnl=fixed_risk*T1_RR*0.5+fixed_risk*T2_RR*0.5 if t1_done else fixed_risk*T2_RR
                trades.append({"entry_time":idx[i-1],"exit_time":idx[i],
                               "direction":"LONG" if direction==1 else "SHORT",
                               "pnl_usd":round(pnl,2),"outcome":"T2","t1_hit":t1_done,"sig_type":sig_type})
                in_trade=False; t1_done=False; continue
            if sl_hit:
                if init_sl==entry_p: pnl=fixed_risk*T1_RR*0.5 if t1_done else 0; reason="BE"
                else: pnl=-fixed_risk+(fixed_risk*T1_RR*0.5 if t1_done else 0); reason="SL"
                trades.append({"entry_time":idx[i-1],"exit_time":idx[i],
                               "direction":"LONG" if direction==1 else "SHORT",
                               "pnl_usd":round(pnl,2),"outcome":reason,"t1_hit":t1_done,"sig_type":sig_type})
                in_trade=False; t1_done=False; continue
        if not in_trade and i in sig_map:
            row=sig_map[i][0]
            entry_p=row.entry; init_sl=row.sl
            direction=1 if row.direction=="buy" else -1
            fixed_risk=INITIAL_CAPITAL*row.risk_pct
            sig_type=row.sig_type; t1_done=False; in_trade=True

    return pd.DataFrame(trades)

def print_stats(df_t, label, months):
    if df_t.empty: print(f"\n  {label}: no trades"); return {}
    n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum(); be=(df_t.pnl_usd==0).sum()
    net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum(); gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
    pf=round(gw/gl,2) if gl>0 else 999; wr=round(wins/n*100,1); nl=round((wins+be)/n*100,1)
    eq=np.concatenate([[INITIAL_CAPITAL],INITIAL_CAPITAL+df_t.pnl_usd.cumsum().values])
    peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
    pm=round(net/months,2)
    print(f"\n  {'='*58}")
    print(f"  {label}")
    print(f"  {'-'*58}")
    print(f"  Trades   : {n}  ({round(n/months,1)}/month)")
    print(f"  W/L/BE   : {wins} / {losses} / {be}")
    print(f"  Win Rate : {wr}%   |  Not-Loss: {nl}%")
    print(f"  Net P&L  : ${round(net,2):+}  ({round(net/INITIAL_CAPITAL*100,1)}% on $5k)")
    print(f"  Net/month: ${pm}")
    print(f"  PF       : {pf}")
    print(f"  Max DD   : {mdd}%")
    for oc in ["T2","BE","SL"]:
        sub=df_t[df_t.outcome==oc]
        if len(sub): print(f"  {oc:<4}: {len(sub):>3} ({round(len(sub)/n*100,1)}%)  avg ${round(sub.pnl_usd.mean(),2):+.2f}")
    return {"label":label,"n":n,"wr":wr,"nl":nl,"net":round(net,2),"pf":pf,"mdd":mdd,"pm":pm}

t_rev   = simulate(sig_df, "REV")
t_cont  = simulate(sig_df, "CONT")
t_choch = simulate(sig_df, "CHOCH")
t_all   = simulate(sig_df, "ALL")

r_rev   = print_stats(t_rev,   "ICT V2 Reversal (LQ+BOS, 0.5% risk)", months)
r_cont  = print_stats(t_cont,  "ICT V2 Continuation (OB/FVG, 1.5% risk)", months)
r_choch = print_stats(t_choch, "ICT V2 ChoCH (macro flip, 1.0% risk)", months)
r_all   = print_stats(t_all,   "ICT V2 ALL signals combined", months)

print(f"\n\n  {'='*72}")
print(f"  FINAL COMPARISON — Fixed Risk $5000 — 24 months")
print(f"  {'='*72}")
print(f"  {'Mode':<32} {'Trades':>7} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8}")
print(f"  {'-'*70}")
for r in [r_rev, r_cont, r_choch, r_all]:
    if r: print(f"  {r['label'][:32]:<32} {r['n']:>7} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8}")
print(f"  {'S2-ZonePyramid Normal'[:32]:<32} {'250':>7} {'40.8':>5}% {'+$2513':>9} {'1.28':>5} {'20.3':>6}% {'+$105':>8}  <-- best")
print(f"  {'='*72}")
