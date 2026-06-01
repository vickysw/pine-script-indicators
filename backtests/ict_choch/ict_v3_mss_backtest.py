"""
ICT V3 MSS — Python Backtest
Faithful translation of ICT-Trading-System-V3-MSS.pine
Signals tested:
  1. MSS (Bearish/Bullish) — sweep-confirmed micro reversal + HTF aligned
  2. Continuation — OB/FVG zone + engulf/strong candle + HTF aligned

Fixed risk: $5000 account
  MSS risk       : 0.75% = $37.50
  Continuation   : 1.50% = $75.00
Exit model: T1 50% at 1.5R, trail SL to BE at 1R, T2 remaining at 2.0R
Session: NY only 18:30-23:30 IST
"""

import pandas as pd
import numpy as np
import pytz

# ── Config ─────────────────────────────────────────────────────────────────
INITIAL_CAPITAL   = 5000.0
MSS_RISK_PCT      = 0.0075   # 0.75%
CONT_RISK_PCT     = 0.015    # 1.5%
T1_RR             = 1.5
T2_RR             = 2.0
TRAIL_BE_RR       = 1.0

# ICT V3 params (Pine defaults)
SWING_PIVOT_LEN   = 15    # for liquidity grabs
STRUCT_PIVOT_LEN  = 5     # for BOS
CHOCH_PIVOT_LEN   = 5     # for macro trend (market_trend)
MSS_PIVOT_LEN     = 3     # for micro trend
MSS_SWEEP_WINDOW  = 8     # bars: sweep must precede MSS within this window
MSS_COOLDOWN      = 5     # bars between MSS signals
HTF_MA_LEN        = 50    # SMA50 on 60M
MANUAL_SL_DIST    = 5.0   # fallback SL distance
MAX_SL_MULT       = 4.0   # cap SL at MANUAL_SL_DIST * 4

IST = pytz.timezone("Asia/Kolkata")

# ── Data loading ───────────────────────────────────────────────────────────
def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    if "time" in df.columns:
        df["ts"] = pd.to_datetime(df["time"].astype(np.int64), unit="s", utc=True)
    elif "datetime" in df.columns:
        df["ts"] = pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("ts").sort_index()[["open","high","low","close"]].astype(float)

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()

def wilder_atr(df, n=14):
    tr = pd.concat([df.high-df.low,
                    (df.high-df.close.shift(1)).abs(),
                    (df.low-df.close.shift(1)).abs()], axis=1).max(axis=1)
    r = np.full(len(tr), np.nan)
    r[n-1] = tr.iloc[:n].mean()
    for i in range(n, len(tr)):
        r[i] = (1/n)*tr.iloc[i] + (1-1/n)*r[i-1]
    return pd.Series(r, index=df.index)

def pivot_highs(high_arr, n):
    """Pine: ta.pivothigh(high, n, n) — bar[i-n] is pivot if highest in [i-2n..i]."""
    N = len(high_arr)
    ph = np.full(N, np.nan)
    for i in range(2*n, N):
        center = i - n
        window = high_arr[i-2*n : i+1]
        if high_arr[center] == np.max(window) and np.sum(window == high_arr[center]) == 1:
            ph[i] = high_arr[center]  # confirmed at bar i (n bars after pivot)
    return ph

def pivot_lows(low_arr, n):
    N = len(low_arr)
    pl = np.full(N, np.nan)
    for i in range(2*n, N):
        center = i - n
        window = low_arr[i-2*n : i+1]
        if low_arr[center] == np.min(window) and np.sum(window == low_arr[center]) == 1:
            pl[i] = low_arr[center]
    return pl

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

# ── HTF SMA50 forward-filled ───────────────────────────────────────────────
h1_sma = sma(df1h.close, HTF_MA_LEN).rename("htf_ma").reset_index()
df5r   = df5[[]].reset_index()
merged = pd.merge_asof(df5r.sort_values("ts"), h1_sma.sort_values("ts"),
                       on="ts", direction="backward").set_index("ts")
df5["htf_ma"]   = merged["htf_ma"].reindex(df5.index)
df5["htf_bull"] = (df5.close > df5.htf_ma).fillna(False)
df5["htf_bear"] = (df5.close < df5.htf_ma).fillna(False)

# ── ATR ────────────────────────────────────────────────────────────────────
df5["atr14"] = wilder_atr(df5)

# ── Pre-compute pivot arrays ───────────────────────────────────────────────
print("Computing pivots...")
h = df5.high.values; l = df5.low.values
o = df5.open.values; c = df5.close.values
N = len(df5)

# Swing pivots (15-bar) for liquidity grabs
swing_ph = pivot_highs(h, SWING_PIVOT_LEN)
swing_pl = pivot_lows(l,  SWING_PIVOT_LEN)

# Structure pivots (5-bar) for BOS
struct_ph = pivot_highs(h, STRUCT_PIVOT_LEN)
struct_pl = pivot_lows(l,  STRUCT_PIVOT_LEN)

# Macro pivots (5-bar) for market_trend (ChoCH)
macro_ph = pivot_highs(h, CHOCH_PIVOT_LEN)
macro_pl = pivot_lows(l,  CHOCH_PIVOT_LEN)

# Micro pivots (3-bar) for micro_trend (MSS)
micro_ph = pivot_highs(h, MSS_PIVOT_LEN)
micro_pl = pivot_lows(l,  MSS_PIVOT_LEN)

htf_bull = df5.htf_bull.values.astype(bool)
htf_bear = df5.htf_bear.values.astype(bool)
atr14    = df5.atr14.values
idx      = df5.index

# ── Main loop ─────────────────────────────────────────────────────────────
print("Running backtest...")

# Running state
last_swing_h = np.nan; last_swing_l = np.nan
last_struct_h = np.nan; last_struct_l = np.nan

macro_sh1 = np.nan; macro_sh2 = np.nan
macro_sl1 = np.nan; macro_sl2 = np.nan
market_trend = 0

micro_sh1 = np.nan; micro_sh2 = np.nan
micro_sl1 = np.nan; micro_sl2 = np.nan
micro_trend = 0

last_bull_lq_bar = -999; last_bear_lq_bar = -999
last_bull_mss_bar = -999; last_bear_mss_bar = -999

# OB tracking (simplified: 4-candle momentum = OB)
bear_ob_top = np.nan; bear_ob_bot = np.nan
bull_ob_top = np.nan; bull_ob_bot = np.nan

signals = []  # list of dicts

for i in range(max(SWING_PIVOT_LEN*2+1, CHOCH_PIVOT_LEN*2+1, 50), N):
    ts = idx[i]
    a  = atr14[i] if not np.isnan(atr14[i]) else 1.0
    cr = h[i] - l[i]
    if cr <= 0: continue

    # Update swing pivots
    if not np.isnan(swing_ph[i]): last_swing_h = swing_ph[i]
    if not np.isnan(swing_pl[i]): last_swing_l = swing_pl[i]

    # Update structure pivots
    if not np.isnan(struct_ph[i]): last_struct_h = struct_ph[i]
    if not np.isnan(struct_pl[i]): last_struct_l = struct_pl[i]

    # Update macro trend (market_trend)
    if not np.isnan(macro_ph[i]):
        macro_sh2 = macro_sh1; macro_sh1 = macro_ph[i]
    if not np.isnan(macro_pl[i]):
        macro_sl2 = macro_sl1; macro_sl1 = macro_pl[i]
    if not (np.isnan(macro_sh1) or np.isnan(macro_sh2) or
            np.isnan(macro_sl1) or np.isnan(macro_sl2)):
        if macro_sh1 > macro_sh2 and macro_sl1 > macro_sl2: market_trend = 1
        elif macro_sh1 < macro_sh2 and macro_sl1 < macro_sl2: market_trend = -1

    # Update micro trend (micro_trend)
    if not np.isnan(micro_ph[i]):
        micro_sh2 = micro_sh1; micro_sh1 = micro_ph[i]
    if not np.isnan(micro_pl[i]):
        micro_sl2 = micro_sl1; micro_sl1 = micro_pl[i]
    if not (np.isnan(micro_sh1) or np.isnan(micro_sh2) or
            np.isnan(micro_sl1) or np.isnan(micro_sl2)):
        if micro_sh1 > micro_sh2 and micro_sl1 > micro_sl2: micro_trend = 1
        elif micro_sh1 < micro_sh2 and micro_sl1 < micro_sl2: micro_trend = -1

    # Liquidity grabs
    lw = min(o[i], c[i]) - l[i]
    uw = h[i] - max(o[i], c[i])

    bull_lq = (not np.isnan(last_swing_l) and
               l[i] < last_swing_l and c[i] > last_swing_l and c[i] > o[i] and
               lw > cr * 0.25 and c[i] > (l[i] + cr * 0.4))
    bear_lq = (not np.isnan(last_swing_h) and
               h[i] > last_swing_h and c[i] < last_swing_h and c[i] < o[i] and
               uw > cr * 0.25 and c[i] < (h[i] - cr * 0.4))

    if bull_lq: last_bull_lq_bar = i
    if bear_lq: last_bear_lq_bar = i

    # OB tracking (5-candle momentum same as Pine)
    if i >= 4:
        if (c[i]<c[i-1] and c[i-1]<c[i-2] and c[i-2]<c[i-3] and c[i-3]<c[i-4] and htf_bear[i]):
            bear_ob_top = h[i-4]; bear_ob_bot = l[i-4]
        if (c[i]>c[i-1] and c[i-1]>c[i-2] and c[i-2]>c[i-3] and c[i-3]>c[i-4] and htf_bull[i]):
            bull_ob_top = h[i-4]; bull_ob_bot = l[i-4]
        # Invalidate OBs
        if not np.isnan(bear_ob_top) and c[i] > bear_ob_top: bear_ob_top = bear_ob_bot = np.nan
        if not np.isnan(bull_ob_bot) and c[i] < bull_ob_bot: bull_ob_top = bull_ob_bot = np.nan

    # MSS signals (V3 core)
    bull_mss = (micro_trend == -1 and not np.isnan(micro_sh1) and
                c[i] > micro_sh1 and c[i-1] <= micro_sh1 and
                (i - last_bull_lq_bar) <= MSS_SWEEP_WINDOW and
                (i - last_bull_mss_bar) > MSS_COOLDOWN)

    bear_mss = (micro_trend == 1 and not np.isnan(micro_sl1) and
                c[i] < micro_sl1 and c[i-1] >= micro_sl1 and
                (i - last_bear_lq_bar) <= MSS_SWEEP_WINDOW and
                (i - last_bear_mss_bar) > MSS_COOLDOWN)

    if bull_mss: last_bull_mss_bar = i
    if bear_mss: last_bear_mss_bar = i

    # HTF-aligned MSS (perfect reversal)
    perfect_buy  = htf_bull[i] and bull_mss
    perfect_sell = htf_bear[i] and bear_mss

    # Continuation (OB-based)
    in_bear_ob = not np.isnan(bear_ob_top) and h[i] >= bear_ob_bot and l[i] <= bear_ob_top
    in_bull_ob = not np.isnan(bull_ob_bot) and h[i] >= bull_ob_bot and l[i] <= bull_ob_top
    bull_engulf = i>0 and c[i]>o[i] and c[i]>h[i-1] and o[i]<=c[i-1]
    bear_engulf = i>0 and c[i]<o[i] and c[i]<l[i-1] and o[i]>=c[i-1]
    body_pct    = abs(c[i]-o[i])/cr
    strong_bull = c[i]>o[i] and body_pct > 0.7
    strong_bear = c[i]<o[i] and body_pct > 0.7

    bull_cont = in_bull_ob and htf_bull[i] and (bull_engulf or strong_bull)
    bear_cont = in_bear_ob and htf_bear[i] and (bear_engulf or strong_bear)

    # Session filter
    if not in_ny(ts): continue

    # SL calculation for MSS
    def mss_sl_sell():
        if not np.isnan(last_swing_h):
            dist = abs(last_swing_h - c[i])
            if dist <= MANUAL_SL_DIST * MAX_SL_MULT:
                return last_swing_h
        return c[i] + MANUAL_SL_DIST

    def mss_sl_buy():
        if not np.isnan(last_swing_l):
            dist = abs(c[i] - last_swing_l)
            if dist <= MANUAL_SL_DIST * MAX_SL_MULT:
                return last_swing_l
        return c[i] - MANUAL_SL_DIST

    # SL for continuation (OB bottom/top)
    def cont_sl_sell():
        if not np.isnan(bear_ob_top): return bear_ob_top
        return c[i] + MANUAL_SL_DIST

    def cont_sl_buy():
        if not np.isnan(bull_ob_bot): return bull_ob_bot
        return c[i] - MANUAL_SL_DIST

    # Emit signals (priority: perfect reversal > continuation)
    if perfect_sell:
        sl = mss_sl_sell()
        if sl > c[i] and (sl - c[i]) > 0:
            signals.append({"bar_i":i, "ts":ts, "sig_type":"MSS",
                            "direction":"sell", "entry":c[i], "sl":sl,
                            "risk_pct": MSS_RISK_PCT})
    elif perfect_buy:
        sl = mss_sl_buy()
        if sl < c[i] and (c[i] - sl) > 0:
            signals.append({"bar_i":i, "ts":ts, "sig_type":"MSS",
                            "direction":"buy", "entry":c[i], "sl":sl,
                            "risk_pct": MSS_RISK_PCT})
    elif bear_cont and not perfect_sell:
        sl = cont_sl_sell()
        if sl > c[i] and (sl - c[i]) > 0:
            signals.append({"bar_i":i, "ts":ts, "sig_type":"CONT",
                            "direction":"sell", "entry":c[i], "sl":sl,
                            "risk_pct": CONT_RISK_PCT})
    elif bull_cont and not perfect_buy:
        sl = cont_sl_buy()
        if sl < c[i] and (c[i] - sl) > 0:
            signals.append({"bar_i":i, "ts":ts, "sig_type":"CONT",
                            "direction":"buy", "entry":c[i], "sl":sl,
                            "risk_pct": CONT_RISK_PCT})

sig_df = pd.DataFrame(signals)
print(f"Total signals detected: {len(sig_df)}")
if len(sig_df) > 0:
    print(f"  MSS:  {len(sig_df[sig_df.sig_type=='MSS'])}")
    print(f"  CONT: {len(sig_df[sig_df.sig_type=='CONT'])}")

# ── Simulate trades ────────────────────────────────────────────────────────
def simulate(sig_df, mode="ALL"):
    if mode == "MSS":
        sigs = sig_df[sig_df.sig_type=="MSS"].copy()
    elif mode == "CONT":
        sigs = sig_df[sig_df.sig_type=="CONT"].copy()
    else:
        sigs = sig_df.copy()

    if sigs.empty: return pd.DataFrame()

    sig_map = {}
    for _, row in sigs.iterrows():
        bi = int(row["bar_i"])
        if bi not in sig_map: sig_map[bi] = []
        sig_map[bi].append(row)

    trades = []
    in_trade = False; entry_p=0; init_sl=0; direction=0; t1_done=False; fixed_risk=0

    for i in range(N):
        if in_trade:
            rd = abs(entry_p - init_sl)
            if rd <= 0: in_trade=False; continue
            mv = (c[i]-entry_p)*direction; rm = mv/rd
            if rm >= TRAIL_BE_RR and init_sl != entry_p: init_sl = entry_p
            if not t1_done:
                t1_hit = ((direction== 1 and h[i]>=entry_p+rd*T1_RR) or
                          (direction==-1 and l[i]<=entry_p-rd*T1_RR))
                if t1_hit: t1_done = True
            sl_hit = ((direction== 1 and l[i]<=init_sl) or
                      (direction==-1 and h[i]>=init_sl))
            t2_hit = ((direction== 1 and h[i]>=entry_p+rd*T2_RR) or
                      (direction==-1 and l[i]<=entry_p-rd*T2_RR))
            if t2_hit:
                pnl = fixed_risk*T1_RR*0.5+fixed_risk*T2_RR*0.5 if t1_done else fixed_risk*T2_RR
                trades.append({"entry_time":idx[i-1],"exit_time":idx[i],
                               "direction":"LONG" if direction==1 else "SHORT",
                               "pnl_usd":round(pnl,2),"outcome":"T2","t1_hit":t1_done,
                               "sig_type":sig_type})
                in_trade=False; t1_done=False; continue
            if sl_hit:
                if init_sl == entry_p:
                    pnl = fixed_risk*T1_RR*0.5 if t1_done else 0.0; reason="BE"
                else:
                    pnl = -fixed_risk+(fixed_risk*T1_RR*0.5 if t1_done else 0); reason="SL"
                trades.append({"entry_time":idx[i-1],"exit_time":idx[i],
                               "direction":"LONG" if direction==1 else "SHORT",
                               "pnl_usd":round(pnl,2),"outcome":reason,"t1_hit":t1_done,
                               "sig_type":sig_type})
                in_trade=False; t1_done=False; continue

        if not in_trade and i in sig_map:
            row = sig_map[i][0]
            entry_p=row["entry"]; init_sl=row["sl"]
            direction=1 if row["direction"]=="buy" else -1
            fixed_risk = INITIAL_CAPITAL * row["risk_pct"]
            sig_type = row["sig_type"]
            t1_done=False; in_trade=True

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
    print(f"  Net P&L  : ${round(net,2):+}  (+{round(net/INITIAL_CAPITAL*100,1)}% on $5k)")
    print(f"  Net/month: ${pm}")
    print(f"  PF       : {pf}")
    print(f"  Max DD   : {mdd}%")
    for oc in ["T2","BE","SL"]:
        sub=df_t[df_t.outcome==oc]
        if len(sub): print(f"  {oc:<4}: {len(sub):>3} ({round(len(sub)/n*100,1)}%)  avg ${round(sub.pnl_usd.mean(),2):+.2f}")
    return {"label":label,"n":n,"wr":wr,"nl":nl,"net":round(net,2),"pf":pf,"mdd":mdd,"pm":pm}

# Run all modes
t_mss  = simulate(sig_df, "MSS")
t_cont = simulate(sig_df, "CONT")
t_all  = simulate(sig_df, "ALL")

r_mss  = print_stats(t_mss,  "ICT V3 MSS only (0.75% risk)", months)
r_cont = print_stats(t_cont, "ICT V3 Continuation only (1.5% risk)", months)
r_all  = print_stats(t_all,  "ICT V3 ALL signals combined", months)

# Summary table
print(f"\n\n  {'='*70}")
print(f"  FINAL COMPARISON — Fixed Risk $5000 — 24 months")
print(f"  {'='*70}")
print(f"  {'Mode':<28} {'Trades':>7} {'WR%':>6} {'Net$':>9} {'PF':>5} {'MaxDD':>7} {'$/mo':>8}")
print(f"  {'-'*65}")
for r in [r_mss, r_cont, r_all]:
    if r: print(f"  {r['label'][:28]:<28} {r['n']:>7} {r['wr']:>5}% {r['net']:>9} {r['pf']:>5} {r['mdd']:>6}% {r['pm']:>8}")
print(f"  {'S2-ZonePyramid Normal (baseline)'[:28]:<28} {'250':>7} {'40.8':>5}% {'$2513':>9} {'1.28':>5} {'20.3':>6}% {'$105':>8}")
print(f"  {'='*70}")
