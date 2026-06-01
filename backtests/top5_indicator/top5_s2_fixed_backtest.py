"""Top5 S2 — Fixed risk backtest. Same params as S2-ZonePyramid Normal."""
import pandas as pd, numpy as np, pytz

INITIAL_CAPITAL  = 5000.0
RISK_PCT         = 0.015
SL_BUFFER        = 0.05
ZONE_TOUCHES_MIN = 2
ZONE_INVALID_ATR = 1.5
MAX_ZONE_COUNT   = 3
MIN_IMPULSE_ATR  = 1.5
MAX_ZONE_AGE     = 288
FRESH_ZONE_ONLY  = True
KEY_LEVEL_STEP   = 50.0
KEY_LEVEL_RANGE  = 15.0
IST = pytz.timezone("Asia/Kolkata")

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def wilder_atr(df, n=14):
    tr = pd.concat([df.high-df.low,(df.high-df.close.shift(1)).abs(),(df.low-df.close.shift(1)).abs()],axis=1).max(axis=1)
    r = np.full(len(tr), np.nan); r[n-1] = tr.iloc[:n].mean()
    for i in range(n, len(tr)): r[i] = (1/n)*tr.iloc[i]+(1-1/n)*r[i-1]
    return pd.Series(r, index=df.index)

def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ","_") for c in df.columns]
    if "time" in df.columns:
        df["ts"] = pd.to_datetime(df["time"].astype(np.int64), unit="s", utc=True)
    elif "datetime" in df.columns:
        df["ts"] = pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("ts").sort_index()[["open","high","low","close"]].astype(float)

def session_vwap(df):
    hlc3 = (df.high+df.low+df.close)/3
    vals = np.empty(len(df)); cum_s=0; cum_n=0; prev_day=None
    for i,(ts,v) in enumerate(zip(df.index, hlc3.values)):
        day=ts.date()
        if day!=prev_day: cum_s=0; cum_n=0; prev_day=day
        cum_s+=v; cum_n+=1; vals[i]=cum_s/cum_n
    return pd.Series(vals, index=df.index)

def in_ny(ts):
    t = ts.tz_convert(IST); m = t.hour*60+t.minute
    return 18*60+30 <= m < 23*60+30

def key_ok(p):
    return abs(p - round(p/KEY_LEVEL_STEP)*KEY_LEVEL_STEP) <= KEY_LEVEL_RANGE

# Load data
print("Loading data...")
df5  = load_csv(r"D:\Trade\tvHistoryData\gold_24m.csv")
df1h = load_csv(r"D:\Trade\tvHistoryData\gold_24m_1h.csv")

# Clip to common range
start = max(df5.index[0], df1h.index[0])
end   = min(df5.index[-1], df1h.index[-1])
df5   = df5[start:end]; df1h = df1h[start:end]
months = round((end-start).days/30.5, 1)
print(f"Period: {df5.index[0].date()} to {df5.index[-1].date()} ({months} months, {len(df5):,} bars)")

# Indicators
df5["atr14"]  = wilder_atr(df5)
df5["atr14e"] = ema(df5.atr14, 14)
df5["ema9"]   = ema(df5.close, 9)
df5["ema15"]  = ema(df5.close, 15)
df5["vwap"]   = session_vwap(df5)

h1_ema = ema(df1h.close, 50).rename("htf_ma").reset_index()
df5r   = df5[[]].reset_index()
merged = pd.merge_asof(df5r.sort_values("ts"), h1_ema.sort_values("ts"),
                       on="ts", direction="backward").set_index("ts")
df5["htf_ma"]   = merged["htf_ma"].reindex(df5.index)
df5["htf_bear"] = (df5.close < df5.htf_ma).fillna(False)
df5["htf_bull"] = (df5.close > df5.htf_ma).fillna(False)

o=df5.open.values; h=df5.high.values; l=df5.low.values; c=df5.close.values
a14=df5.atr14.values; a14e=df5.atr14e.values
e9=df5.ema9.values; e15=df5.ema15.values; vw=df5.vwap.values
htf_bear=df5.htf_bear.values; htf_bull=df5.htf_bull.values
idx=df5.index; N=len(df5)

fixed_risk = INITIAL_CAPITAL * RISK_PCT  # always $75, never compounds

bear_zt=[]; bear_zb=[]; bear_zc=[]; bear_ze=[]
bull_zt=[]; bull_zb=[]; bull_zc=[]; bull_ze=[]
bear_ref=np.nan; bear_touches=0; prev_in_sell=False
bull_ref=np.nan; bull_touches=0; prev_in_buy=False
trades=[]; in_trade=False; entry_p=0; init_sl=0; direction=0; t1_done=False

print("Running backtest...")
for i in range(N):
    ts=idx[i]; a=a14[i] if not np.isnan(a14[i]) else 1.0; ae=a14e[i] if not np.isnan(a14e[i]) else 1.0

    # Zone creation
    if i >= 4:
        bm = c[i]<c[i-1] and c[i-1]<c[i-2] and c[i-2]<c[i-3] and c[i-3]<c[i-4]
        um = c[i]>c[i-1] and c[i-1]>c[i-2] and c[i-2]>c[i-3] and c[i-3]>c[i-4]
        if bm and htf_bear[i]:
            zt=h[i-4]; drop=c[i-4]-c[i]
            if drop>=ae*MIN_IMPULSE_ATR and key_ok(zt):
                if len(bear_zt)>=MAX_ZONE_COUNT: bear_zt.pop(0);bear_zb.pop(0);bear_zc.pop(0);bear_ze.pop(0)
                bear_zt.append(zt);bear_zb.append(l[i-4]);bear_zc.append(i);bear_ze.append(False)
        if um and htf_bull[i]:
            zb=l[i-4]; rise=c[i]-c[i-4]
            if rise>=ae*MIN_IMPULSE_ATR and key_ok(zb):
                if len(bull_zt)>=MAX_ZONE_COUNT: bull_zt.pop(0);bull_zb.pop(0);bull_zc.pop(0);bull_ze.pop(0)
                bull_zt.append(h[i-4]);bull_zb.append(zb);bull_zc.append(i);bull_ze.append(False)

    # Zone invalidation
    inv = a * ZONE_INVALID_ATR
    for zi in range(len(bear_zt)-1,-1,-1):
        if c[i]>bear_zt[zi]+inv or (i-bear_zc[zi])>MAX_ZONE_AGE:
            bear_zt.pop(zi);bear_zb.pop(zi);bear_zc.pop(zi);bear_ze.pop(zi)
    for zi in range(len(bull_zt)-1,-1,-1):
        if c[i]<bull_zb[zi]-inv or (i-bull_zc[zi])>MAX_ZONE_AGE:
            bull_zt.pop(zi);bull_zb.pop(zi);bull_zc.pop(zi);bull_ze.pop(zi)

    # In-zone
    in_sell=False; sz_top=np.nan; sz_idx=-1; sell_fresh=False
    for zi in range(len(bear_zt)):
        if h[i]>=bear_zb[zi] and l[i]<=bear_zt[zi]:
            in_sell=True; sz_top=bear_zt[zi]; sz_idx=zi; sell_fresh=not bear_ze[zi]; break
    in_buy=False; bz_bot=np.nan; bz_idx=-1; buy_fresh=False
    for zi in range(len(bull_zt)):
        if h[i]>=bull_zb[zi] and l[i]<=bull_zt[zi]:
            in_buy=True; bz_bot=bull_zb[zi]; bz_idx=zi; buy_fresh=not bull_ze[zi]; break

    # Touch counter
    if in_sell:
        if np.isnan(bear_ref) or sz_top!=bear_ref: bear_touches=1; bear_ref=sz_top
        elif not prev_in_sell: bear_touches+=1
    else:
        if not np.isnan(bear_ref) and bear_ref not in bear_zt: bear_ref=np.nan; bear_touches=0
    if in_buy:
        if np.isnan(bull_ref) or bz_bot!=bull_ref: bull_touches=1; bull_ref=bz_bot
        elif not prev_in_buy: bull_touches+=1
    else:
        if not np.isnan(bull_ref) and bull_ref not in bull_zb: bull_ref=np.nan; bull_touches=0
    prev_in_sell=in_sell; prev_in_buy=in_buy

    # Candle + confluence
    strong_red = c[i]<o[i] and (o[i]-c[i])>a*0.5
    strong_grn = c[i]>o[i] and (c[i]-o[i])>a*0.5
    bear_eng   = i>0 and c[i]<o[i] and c[i]<l[i-1] and o[i]>=c[i-1]
    bull_eng   = i>0 and c[i]>o[i] and c[i]>h[i-1] and o[i]<=c[i-1]
    cs = (e9[i]>c[i] and e15[i]>c[i]) or vw[i]>c[i]
    cb = (e9[i]<c[i] and e15[i]<c[i]) or vw[i]<c[i]

    # Session + SL dead zone
    if not in_ny(ts): continue
    sell_sl_d = (sz_top+SL_BUFFER-c[i]) if not np.isnan(sz_top) else 0
    buy_sl_d  = (c[i]-bz_bot+SL_BUFFER) if not np.isnan(bz_bot) else 0
    sl_ok_s   = not (5.0<=sell_sl_d<=8.0)
    sl_ok_b   = not (5.0<=buy_sl_d<=8.0)

    bs = (in_sell and htf_bear[i] and bear_touches>=ZONE_TOUCHES_MIN
          and (bear_eng or strong_red) and cs
          and (not FRESH_ZONE_ONLY or sell_fresh) and sl_ok_s)
    bb = (in_buy  and htf_bull[i] and bull_touches>=ZONE_TOUCHES_MIN
          and (bull_eng or strong_grn) and cb
          and (not FRESH_ZONE_ONLY or buy_fresh) and sl_ok_b)

    if bs and sz_idx>=0: bear_ze[sz_idx]=True
    if bb and bz_idx>=0: bull_ze[bz_idx]=True

    # Trade management
    if in_trade:
        rd = abs(entry_p-init_sl)
        if rd<=0: in_trade=False; continue
        mv = (c[i]-entry_p)*direction; rm = mv/rd
        if rm>=1.0 and init_sl!=entry_p: init_sl=entry_p
        if not t1_done:
            t1_hit=((direction==1 and h[i]>=entry_p+rd*1.5) or
                    (direction==-1 and l[i]<=entry_p-rd*1.5))
            if t1_hit: t1_done=True
        sl_hit = (direction==1 and l[i]<=init_sl) or (direction==-1 and h[i]>=init_sl)
        t2_hit = ((direction==1 and h[i]>=entry_p+rd*2.0) or
                  (direction==-1 and l[i]<=entry_p-rd*2.0))
        if t2_hit:
            pnl = fixed_risk*1.5*0.5+fixed_risk*2.0*0.5 if t1_done else fixed_risk*2.0
            trades.append({"entry_time":idx[i-1],"exit_time":ts,
                           "direction":"LONG" if direction==1 else "SHORT",
                           "entry":round(entry_p,2),"pnl_usd":round(pnl,2),
                           "outcome":"T2","t1_hit":t1_done})
            in_trade=False; t1_done=False; continue
        if sl_hit:
            if init_sl==entry_p:
                pnl=fixed_risk*1.5*0.5 if t1_done else 0.0; reason="BE"
            else:
                pnl=-fixed_risk+(fixed_risk*1.5*0.5 if t1_done else 0); reason="SL"
            trades.append({"entry_time":idx[i-1],"exit_time":ts,
                           "direction":"LONG" if direction==1 else "SHORT",
                           "entry":round(entry_p,2),"pnl_usd":round(pnl,2),
                           "outcome":reason,"t1_hit":t1_done})
            in_trade=False; t1_done=False; continue

    # Entry (only when flat)
    if not in_trade:
        if bs and not np.isnan(sz_top):
            entry_p=c[i]; init_sl=sz_top+SL_BUFFER; direction=-1; t1_done=False; in_trade=True
        elif bb and not np.isnan(bz_bot):
            entry_p=c[i]; init_sl=bz_bot-SL_BUFFER; direction=1; t1_done=False; in_trade=True

# Results
df_t = pd.DataFrame(trades)
if df_t.empty:
    print("No trades"); exit()

n=len(df_t); wins=(df_t.pnl_usd>0).sum(); losses=(df_t.pnl_usd<0).sum(); be=(df_t.pnl_usd==0).sum()
net=df_t.pnl_usd.sum(); gw=df_t[df_t.pnl_usd>0].pnl_usd.sum(); gl=abs(df_t[df_t.pnl_usd<0].pnl_usd.sum())
pf=round(gw/gl,2) if gl>0 else 999; wr=round(wins/n*100,1); nl=round((wins+be)/n*100,1)
eq=np.concatenate([[5000],5000+df_t.pnl_usd.cumsum().values])
peak=np.maximum.accumulate(eq); mdd=round(((peak-eq)/peak*100).max(),1)
pm=round(net/months,2)

print()
print("="*62)
print("  TOP5 S2 — FIXED RISK $5000 @ 1.5% (24 months)")
print("="*62)
print(f"  Trades   : {n}  ({round(n/months,1)}/month)")
print(f"  W/L/BE   : {wins} / {losses} / {be}")
print(f"  Win Rate : {wr}%   |  Not-Loss: {nl}%")
print(f"  Net P&L  : ${round(net,2):+} (+{round(net/5000*100,1)}% on $5k)")
print(f"  Net/month: ${pm}")
print(f"  PF       : {pf}")
print(f"  Max DD   : {mdd}%")
for oc in ["T2","BE","SL"]:
    sub=df_t[df_t.outcome==oc]
    if len(sub): print(f"  {oc:<4}: {len(sub):>3} ({round(len(sub)/n*100,1)}%)  avg ${round(sub.pnl_usd.mean(),2):+.2f}")
print()
print("="*62)
print("  BASELINE — S2-ZonePyramid Normal (same period)")
print("="*62)
print("  Trades   : 250  (10.5/month)")
print("  W/L/BE   : 102 / 122 / 26")
print("  Win Rate : 40.8%   |  Not-Loss: 51.2%")
print("  Net P&L  : +$2512.5  (+50.2% on $5k)")
print("  Net/month: +$105.13")
print("  PF       : 1.28")
print("  Max DD   : 20.3%")
print()
