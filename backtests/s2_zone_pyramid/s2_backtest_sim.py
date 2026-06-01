"""
S2 Zone-Pyramid — v2 vs v3 Python backtest simulation
Uses: 5M OHLC + 60M for HTF bias
Replicates Pine Script logic as closely as possible
"""
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ── Config (match Pine defaults) ─────────────────────────────────────────────
HTF_LEN         = 50        # 60M EMA length
ZONE_TOUCHES_MIN = 2
ZONE_INVALID_ATR = 1.5
S2_MIN_IMPULSE   = 1.5
S2_MAX_AGE       = 288
S2_KEY_STEP      = 50.0
S2_KEY_RANGE     = 15.0
SL_SKIP_MIN      = 5.0
SL_SKIP_MAX      = 8.0
SL_BUF           = 0.05     # mintick(0.01) * 5
ACCOUNT_SIZE     = 5000
RISK_PCT         = 1.5
POINT_VAL        = 100.0
HTF_DIST_MIN_V3  = 10.0     # v3 only

# NY session UTC: 18:30-23:30 IST = 13:00-18:00 UTC
NY_START_UTC = 13   # hour
NY_END_UTC   = 18   # hour (exclusive)

# ── Load 5M data ──────────────────────────────────────────────────────────────
d5a = pd.read_csv(r'tvHistoryData\OANDA_XAUUSD, 5.csv')
d5b = pd.read_csv(r'OANDA_XAUUSD, 5_30th.csv')
for d in [d5a, d5b]:
    d['dt'] = pd.to_datetime(d['time'], unit='s', utc=True)
    d.set_index('dt', inplace=True)
    d.sort_index(inplace=True)

df = pd.concat([d5a[['open','high','low','close']], d5b[['open','high','low','close']]]).sort_index()
df = df[~df.index.duplicated(keep='last')]
print(f"5M data: {df.index[0]} to {df.index[-1]}  ({len(df)} bars)")

# ── Load 60M data for HTF close ───────────────────────────────────────────────
d60 = pd.read_csv(r'tvHistoryData\OANDA_XAUUSD, 60-1y.csv')
d60['dt'] = pd.to_datetime(d60['time'], unit='s', utc=True)
d60.set_index('dt', inplace=True)
d60.sort_index(inplace=True)
print(f"60M data: {d60.index[0]} to {d60.index[-1]}  ({len(d60)} bars)")

# ── ATR helpers ───────────────────────────────────────────────────────────────
def wilder_rma(series, n):
    """Pine ta.rma = Wilder's EMA, alpha=1/n"""
    result = np.full(len(series), np.nan)
    start = series.first_valid_index()
    if start is None: return pd.Series(result, index=series.index)
    idx = series.index.get_loc(start)
    result[idx] = series.iloc[idx]
    for i in range(idx+1, len(series)):
        if np.isnan(series.iloc[i]):
            result[i] = result[i-1]
        else:
            result[i] = result[i-1] * (1 - 1/n) + series.iloc[i] * (1/n)
    return pd.Series(result, index=series.index)

def pine_ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

# True range
df['prev_close'] = df['close'].shift(1)
df['tr'] = np.maximum(df['high'] - df['low'],
           np.maximum((df['high'] - df['prev_close']).abs(),
                      (df['low']  - df['prev_close']).abs()))
df['atr14']  = wilder_rma(df['tr'], 14)
df['atr14e'] = pine_ema(df['atr14'], 14)

# ── HTF close: forward-fill 60M close onto 5M bars ───────────────────────────
# Pine: request.security with lookahead_off = value of last COMPLETED 60M bar
htf_close_60 = d60['close'].reindex(df.index, method='ffill')
df['htf_close'] = htf_close_60

# htf_ma: EMA50 of htf_close on 5M bars (same as Pine computes it)
df['htf_ma']   = pine_ema(df['htf_close'], HTF_LEN)
df['htf_bear'] = df['htf_close'] < df['htf_ma']
df['htf_bull'] = df['htf_close'] > df['htf_ma']

# HTF distance
df['htf_dist_sell'] = df['htf_ma'] - df['htf_close']   # positive when bear
df['htf_dist_buy']  = df['htf_close'] - df['htf_ma']   # positive when bull

# ── 5M indicators ─────────────────────────────────────────────────────────────
df['ema9']  = pine_ema(df['close'], 9)
df['ema15'] = pine_ema(df['close'], 15)
df['hlc3']  = (df['high'] + df['low'] + df['close']) / 3

# Daily VWAP (price-only, no volume)
df['date'] = df.index.date
df['vwap'] = df.groupby('date')['hlc3'].transform(lambda x: x.expanding().mean())

# Candle patterns
df['strong_red']  = (df['close'] < df['open']) & ((df['open'] - df['close']) > df['atr14'] * 0.5)
df['strong_grn']  = (df['close'] > df['open']) & ((df['close'] - df['open']) > df['atr14'] * 0.5)
df['bear_engulf'] = (df['close'] < df['open']) & (df['close'] < df['low'].shift(1)) & (df['open'] >= df['close'].shift(1))
df['bull_engulf'] = (df['close'] > df['open']) & (df['close'] > df['high'].shift(1)) & (df['open'] <= df['close'].shift(1))

df['conf_sell'] = ((df['ema9'] > df['close']) & (df['ema15'] > df['close'])) | (df['vwap'] > df['close'])
df['conf_buy']  = ((df['ema9'] < df['close']) & (df['ema15'] < df['close'])) | (df['vwap'] < df['close'])

# Session: NY only — 13:00-18:00 UTC
df['in_ny'] = (df.index.hour >= NY_START_UTC) & (df.index.hour < NY_END_UTC)

# ── Zone helpers ──────────────────────────────────────────────────────────────
def impulse_ok(move, atr14e_val):
    return move >= atr14e_val * S2_MIN_IMPULSE

def key_ok(price):
    if S2_KEY_STEP <= 0: return True
    return abs(price - round(price / S2_KEY_STEP) * S2_KEY_STEP) <= S2_KEY_RANGE

# ── Main simulation loop ──────────────────────────────────────────────────────
def run_backtest(use_v3_dist_filter=False):
    trades = []

    # Zone state
    bear_zt, bear_zb, bear_zc, bear_ze = [], [], [], []
    bull_zt, bull_zb, bull_zc, bull_ze = [], [], [], []

    # Touch counter state
    bear_touches = 0
    bull_touches = 0
    bear_ref     = None
    bull_ref     = None

    prev_in_sell = False
    prev_in_buy  = False

    rows = df.values
    cols = list(df.columns)
    ci = {c: i for i, c in enumerate(cols)}

    for bar_i in range(5, len(df)):
        r   = df.iloc[bar_i]
        bi  = bar_i   # bar_index proxy

        o, h, l, c = r['open'], r['high'], r['low'], r['close']
        atr    = r['atr14']
        atr_e  = r['atr14e']
        htf_c  = r['htf_close']
        htf_m  = r['htf_ma']
        hbear  = r['htf_bear']
        hbull  = r['htf_bull']
        inv_buf = atr * ZONE_INVALID_ATR

        if np.isnan(atr) or np.isnan(htf_m): continue

        # v3 distance filters
        dist_ok_sell = (not use_v3_dist_filter) or (HTF_DIST_MIN_V3 <= 0) or \
                       ((htf_m - htf_c) >= HTF_DIST_MIN_V3)
        dist_ok_buy  = (not use_v3_dist_filter) or (HTF_DIST_MIN_V3 <= 0) or \
                       ((htf_c - htf_m) >= HTF_DIST_MIN_V3)

        # ── Zone creation ─────────────────────────────────────────────────────
        prev = df.iloc[bar_i-1:bar_i+1]  # for momentum check
        c0 = df['close'].iloc[bar_i]
        c1 = df['close'].iloc[bar_i-1]
        c2 = df['close'].iloc[bar_i-2]
        c3 = df['close'].iloc[bar_i-3]
        c4 = df['close'].iloc[bar_i-4]
        h4 = df['high'].iloc[bar_i-4]
        l4 = df['low'].iloc[bar_i-4]

        bear_mom = c0 < c1 < c2 < c3 < c4
        bull_mom = c0 > c1 > c2 > c3 > c4

        if bear_mom and hbear:
            zt   = h4
            drop = c4 - c0
            if impulse_ok(drop, atr_e) and key_ok(zt):
                if len(bear_zt) >= 3:
                    bear_zt.pop(0); bear_zb.pop(0)
                    bear_zc.pop(0); bear_ze.pop(0)
                bear_zt.append(zt)
                bear_zb.append(l4)
                bear_zc.append(bi)
                bear_ze.append(0.0)

        if bull_mom and hbull:
            zb   = l4
            rise = c0 - c4
            if impulse_ok(rise, atr_e) and key_ok(zb):
                if len(bull_zt) >= 3:
                    bull_zt.pop(0); bull_zb.pop(0)
                    bull_zc.pop(0); bull_ze.pop(0)
                bull_zt.append(h4)
                bull_zb.append(zb)
                bull_zc.append(bi)
                bull_ze.append(0.0)

        # ── Zone invalidation ─────────────────────────────────────────────────
        for lst in [(bear_zt, bear_zb, bear_zc, bear_ze, True),
                    (bull_zt, bull_zb, bull_zc, bull_ze, False)]:
            zt_l, zb_l, zc_l, ze_l, is_bear = lst
            i2 = len(zt_l) - 1
            while i2 >= 0:
                age_bad   = (bi - zc_l[i2]) > S2_MAX_AGE
                if is_bear:
                    price_bad = c > zt_l[i2] + inv_buf
                else:
                    price_bad = c < zb_l[i2] - inv_buf
                if price_bad or age_bad:
                    zt_l.pop(i2); zb_l.pop(i2)
                    zc_l.pop(i2); ze_l.pop(i2)
                i2 -= 1

        # ── In-zone detection ─────────────────────────────────────────────────
        in_sell = False
        sz_top = sz_bot = None
        sz_idx = -1
        for i2 in range(len(bear_zt)):
            if h >= bear_zb[i2] and l <= bear_zt[i2]:
                in_sell = True
                sz_top  = bear_zt[i2]
                sz_bot  = bear_zb[i2]
                sz_idx  = i2
                break

        in_buy = False
        bz_top = bz_bot = None
        bz_idx = -1
        for i2 in range(len(bull_zt)):
            if h >= bull_zb[i2] and l <= bull_zt[i2]:
                in_buy  = True
                bz_top  = bull_zt[i2]
                bz_bot  = bull_zb[i2]
                bz_idx  = i2
                break

        sell_fresh = sz_idx >= 0 and bear_ze[sz_idx] == 0.0
        buy_fresh  = bz_idx >= 0 and bull_ze[bz_idx] == 0.0

        # ── Touch counter ─────────────────────────────────────────────────────
        if in_sell:
            if bear_ref is None or sz_top != bear_ref:
                bear_touches = 1
                bear_ref     = sz_top
            elif not prev_in_sell:
                bear_touches += 1
        else:
            if bear_ref is not None:
                if bear_ref not in bear_zt:
                    bear_ref     = None
                    bear_touches = 0

        if in_buy:
            if bull_ref is None or bz_bot != bull_ref:
                bull_touches = 1
                bull_ref     = bz_bot
            elif not prev_in_buy:
                bull_touches += 1
        else:
            if bull_ref is not None:
                if bull_ref not in bull_zb:
                    bull_ref     = None
                    bull_touches = 0

        # ── SL distances ──────────────────────────────────────────────────────
        sell_sl_dist = ((sz_top + SL_BUF) - c) if sz_top is not None else 0.0
        buy_sl_dist  = (c - (bz_bot - SL_BUF))  if bz_bot is not None else 0.0

        sl_ok_sell = SL_SKIP_MAX <= 0 or not (SL_SKIP_MIN <= sell_sl_dist <= SL_SKIP_MAX)
        sl_ok_buy  = SL_SKIP_MAX <= 0 or not (SL_SKIP_MIN <= buy_sl_dist  <= SL_SKIP_MAX)

        # ── Confluence + patterns ─────────────────────────────────────────────
        conf_s = bool(r['conf_sell'])
        conf_b = bool(r['conf_buy'])
        sr     = bool(r['strong_red'])
        sg     = bool(r['strong_grn'])
        be     = bool(r['bear_engulf'])
        bu     = bool(r['bull_engulf'])
        in_ny  = bool(r['in_ny'])

        # ── Base signals ──────────────────────────────────────────────────────
        htf_sell_ok = hbear and dist_ok_sell
        htf_buy_ok  = hbull and dist_ok_buy

        base_sell = (in_sell and htf_sell_ok and
                     bear_touches >= ZONE_TOUCHES_MIN and
                     (be or sr) and conf_s)

        base_buy  = (in_buy and htf_buy_ok and
                     bull_touches >= ZONE_TOUCHES_MIN and
                     (bu or sg) and conf_b)

        sig_sell = base_sell and in_ny and sl_ok_sell
        sig_buy  = base_buy  and in_ny and sl_ok_buy

        # ── Record trade ──────────────────────────────────────────────────────
        if sig_sell and sz_top is not None:
            sl_price = sz_top + SL_BUF
            dist = sl_price - c
            if dist > 0:
                risk_usd = ACCOUNT_SIZE * RISK_PCT / 100
                lots = round(risk_usd / (dist * POINT_VAL), 3)
                trades.append({
                    'entry_time': r.name,
                    'direction': 'SHORT',
                    'entry': round(c, 2),
                    'sl': round(sl_price, 2),
                    'dist': round(dist, 2),
                    'lots': lots,
                    'zone_top': round(sz_top, 2),
                    'touches': bear_touches,
                    'fresh': sell_fresh,
                    'htf_dist': round(r['htf_dist_sell'], 2),
                    'bar_i': bar_i,
                })
                bear_ze[sz_idx] = 1.0

        if sig_buy and bz_bot is not None:
            sl_price = bz_bot - SL_BUF
            dist = c - sl_price
            if dist > 0:
                risk_usd = ACCOUNT_SIZE * RISK_PCT / 100
                lots = round(risk_usd / (dist * POINT_VAL), 3)
                trades.append({
                    'entry_time': r.name,
                    'direction': 'LONG',
                    'entry': round(c, 2),
                    'sl': round(sl_price, 2),
                    'dist': round(dist, 2),
                    'lots': lots,
                    'zone_bot': round(bz_bot, 2),
                    'touches': bull_touches,
                    'fresh': buy_fresh,
                    'htf_dist': round(r['htf_dist_buy'], 2),
                    'bar_i': bar_i,
                })
                bull_ze[bz_idx] = 1.0

        prev_in_sell = in_sell
        prev_in_buy  = in_buy

    return pd.DataFrame(trades)

print("\nRunning v2...")
v2 = run_backtest(use_v3_dist_filter=False)
print(f"v2 signals: {len(v2)}")

print("Running v3...")
v3 = run_backtest(use_v3_dist_filter=True)
print(f"v3 signals: {len(v3)}")

# ── Display results ───────────────────────────────────────────────────────────
def show_trades(df_t, label):
    if len(df_t) == 0:
        print(f"\n{label}: no trades")
        return
    print(f"\n{'='*60}")
    print(f"{label} — {len(df_t)} signals")
    print(f"{'='*60}")
    print(f"{'#':<3} {'Date':<12} {'Dir':<6} {'Entry':<8} {'SL':<8} {'Dist':>5} {'T':<3} {'HTF_d':>7} {'Fresh'}")
    print('-'*60)
    for i, (_, r) in enumerate(df_t.iterrows(), 1):
        fresh = 'F' if r.get('fresh', False) else '-'
        print(f"{i:<3} {str(r['entry_time'])[:10]:<12} {r['direction']:<6} "
              f"{r['entry']:<8.2f} {r['sl']:<8.2f} {r['dist']:>5.2f} "
              f"{int(r['touches']):<3} {r['htf_dist']:>7.1f} {fresh}")

show_trades(v2, "V2 — Normal mode")
show_trades(v3, "V3 — Distance filter (>=10pts)")

print(f"\n{'='*60}")
print("COMPARISON SUMMARY")
print(f"{'='*60}")
print(f"V2: {len(v2)} signals")
print(f"V3: {len(v3)} signals  ({len(v2)-len(v3)} removed by distance filter)")

if len(v2) > 0:
    v2_skip = v2[v2['htf_dist'] < 10]
    print(f"\nV2 signals blocked by v3 filter (dist<10):")
    for _, r in v2_skip.iterrows():
        print(f"  {str(r['entry_time'])[:16]}  {r['direction']}  dist={r['htf_dist']:.1f}pts")

print(f"\nV3 direction breakdown:")
if len(v3) > 0:
    print(v3['direction'].value_counts().to_string())
    print(f"\nHTF dist stats (v3 kept trades):")
    print(v3['htf_dist'].describe().round(1).to_string())
