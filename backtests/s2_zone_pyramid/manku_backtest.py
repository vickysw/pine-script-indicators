"""
Manku Top 5 — Python Backtest
Mirrors Manku-Top5-Indicator.pine logic exactly.
Runs all 5 setups on 5M / 15M / 30M XAUUSD data.
Produces:
  • Per-setup stats (win rate, PF, avg R, max DD)
  • Full signal log  → D:/Trade/manku_signals.csv
  • Summary table    → D:/Trade/manku_summary.csv
"""

import pandas as pd
import numpy as np
import pytz
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

# ─── Config ────────────────────────────────────────────────────────────────────
FILES = {
    "5M":  r"D:\Trade\tvHistoryData\gold_24m.csv",      # 24-month 5M with volume
    "15M": r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 15.csv",
    "30M": r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 30.csv",
}

# Risk params (mirrors strategy V2 defaults)
RISK = {
    1: 0.5,   # Setup #1 pending
    2: 1.5,   # Setup #2 continuation
    3: 0.5,   # Setup #3 reversal
    4: 0.5,   # Setup #4 reversal
    5: 0.5,   # Setup #5 reversal
}
INITIAL_CAPITAL     = 5000.0
FINAL_TARGET_RR     = 2.0
TRAIL_BE_RR         = 1.0
PARTIAL_RR          = 1.5
TIME_STOP_BARS      = 60
DAILY_DD_PCT        = 5.0    # funded rule: 5% of daily start balance
TOTAL_DD_PCT        = 10.0   # funded rule: 10% max total loss
MAX_TRADES_DAY      = 3
CONSEC_LOSS_STOP    = 2

# Setup tuning
WAIT_BARS_50M       = 10
WAIT_BAND_ATR       = 1.0
ZONE_TOUCHES_MIN    = 2
ZONE_INVALID_ATR    = 1.5
S2_MIN_IMPULSE_ATR  = 1.5
S2_MAX_ZONE_AGE     = 288     # bars
S2_FRESH_ZONE_ONLY  = True
S2_KEY_LEVEL_STEP   = 50.0
S2_KEY_LEVEL_RANGE  = 15.0
S2_SL_SKIP_MIN      = 3.0   # skip SL distance in this "dead zone" range
S2_SL_SKIP_MAX      = 6.0   # (0% win rate historically)

SWEEP_PIVOT_LEN     = 15
SWEEP_MIN_WICK_PCT  = 30.0

FAKE_BREAK_WINDOW   = 3

FIB_PIVOT_LEN       = 10
FIB_BAND_LOW        = 50.0
FIB_BAND_HIGH       = 61.8
FIB_INVALIDATE      = 79.0
FIB_MIN_IMPULSE_ATR = 2.0

ATR_LOOKBACK        = 100
ATR_MIN_PCT         = 30

HTF_MA_LEN          = 50

# Session windows (IST / UTC+5:30)
IST = pytz.timezone("Asia/Kolkata")
SESSION_START = (18, 30)   # NY session start
SESSION_END   = (23, 30)   # NY session end

ADD_LONDON_SESSION = False  # London session kills S2 (13.6% win) — NY only
LONDON_START = (13,  0)    # ~8:00 AM London winter (1:00 PM IST)
LONDON_END   = (21,  0)    # ~4:30 PM London (9:00 PM IST)

SETUPS_ON = {1: True, 2: True, 3: True, 4: False, 5: True}

# ─── Indicator helpers ─────────────────────────────────────────────────────────

def wilder_rma(series: pd.Series, n: int) -> pd.Series:
    """Wilder's RMA (same as Pine ta.rma / ta.atr internals)."""
    alpha = 1.0 / n
    result = np.empty(len(series))
    result[:] = np.nan
    first_valid = series.first_valid_index()
    if first_valid is None:
        return pd.Series(result, index=series.index)
    idx0 = series.index.get_loc(first_valid)
    if idx0 + n - 1 >= len(series):
        return pd.Series(result, index=series.index)
    result[idx0 + n - 1] = series.iloc[idx0: idx0 + n].mean()
    for i in range(idx0 + n, len(series)):
        result[i] = alpha * series.iloc[i] + (1 - alpha) * result[i - 1]
    return pd.Series(result, index=series.index)


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return wilder_rma(tr, n)


def atr_ema(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """EMA-smoothed ATR — used for S2 impulse quality check (matches indicator)."""
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return ema(tr, n)


def atr_percentile(atr_series: pd.Series, lookback: int, pct: float) -> pd.Series:
    return atr_series.rolling(lookback).quantile(pct / 100.0)


def pivot_high(series: pd.Series, left: int, right: int) -> pd.Series:
    """Returns pivot high value at confirmation bar (bar_index), else NaN."""
    result = pd.Series(np.nan, index=series.index)
    arr = series.values
    for i in range(left, len(arr) - right):
        window = arr[i - left: i + right + 1]
        if arr[i] == np.nanmax(window) and np.sum(window == arr[i]) == 1:
            result.iloc[i + right] = arr[i]
    return result


def pivot_low(series: pd.Series, left: int, right: int) -> pd.Series:
    result = pd.Series(np.nan, index=series.index)
    arr = series.values
    for i in range(left, len(arr) - right):
        window = arr[i - left: i + right + 1]
        if arr[i] == np.nanmin(window) and np.sum(window == arr[i]) == 1:
            result.iloc[i + right] = arr[i]
    return result


def vwap_daily(df: pd.DataFrame) -> pd.Series:
    """Simple daily VWAP reset each calendar day."""
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    vol  = pd.Series(1.0, index=df.index)  # no volume in CSV → equal weight
    day  = df.index.date
    cum_tp  = (hlc3 * vol).groupby(day, sort=False).cumsum()
    cum_vol = vol.groupby(day, sort=False).cumsum()
    return cum_tp / cum_vol


def in_session(ts: pd.Timestamp) -> bool:
    ist = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(IST)
    cur = ist.hour * 60 + ist.minute
    if SESSION_START[0] * 60 + SESSION_START[1] <= cur <= SESSION_END[0] * 60 + SESSION_END[1]:
        return True
    if ADD_LONDON_SESSION:
        if LONDON_START[0] * 60 + LONDON_START[1] <= cur <= LONDON_END[0] * 60 + LONDON_END[1]:
            return True
    return False


def get_session_label(ts: pd.Timestamp) -> str:
    ist = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(IST)
    cur = ist.hour * 60 + ist.minute
    if SESSION_START[0] * 60 + SESSION_START[1] <= cur <= SESSION_END[0] * 60 + SESSION_END[1]:
        return "NY"
    if ADD_LONDON_SESSION:
        if LONDON_START[0] * 60 + LONDON_START[1] <= cur <= LONDON_END[0] * 60 + LONDON_END[1]:
            return "London"
    return "other"


# ─── Load & prep data ──────────────────────────────────────────────────────────

def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Detect format: TV unix-timestamp vs gold_6m datetime string
    if "time" in df.columns:
        # TV format: unix seconds + HTF MA columns
        df = df.rename(columns={"time": "ts"})
        cols = ["ts", "open", "high", "low", "close"]
        # htf_ma.1 = ta.ema on 1H (preferred); htf_ma = ta.sma
        if "htf_ma.1" in df.columns:
            df = df.rename(columns={"htf_ma.1": "htf_ma_tv"})
            cols.append("htf_ma_tv")
        elif "htf_ma" in df.columns:
            df = df.rename(columns={"htf_ma": "htf_ma_tv"})
            cols.append("htf_ma_tv")
        df = df[cols].copy()
        df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="s", utc=True)
    else:
        # gold_6m format: Datetime string, has Volume
        df = df.rename(columns={"datetime": "ts"})
        cols = ["ts", "open", "high", "low", "close"]
        df = df[cols].copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)

    df = df.set_index("ts").sort_index()
    df = df.astype(float)
    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["atr14"]     = atr(df, 14)
    df["atr14_ema"] = atr_ema(df, 14)
    df["atr_q"]     = atr_percentile(df["atr14"], ATR_LOOKBACK, ATR_MIN_PCT)
    df["range_ok"]  = df["atr14"] > df["atr_q"]
    df["ema9"]      = ema(df["close"], 9)
    df["ema15"]     = ema(df["close"], 15)
    df["vwap"]      = vwap_daily(df)
    # HTF bias priority:
    #   1. TV-exported 1H EMA column in THIS file (15M/30M files)
    #   2. Separate OANDA_XAUUSD, 60.csv file (exact request.security match)
    #   3. Fallback: resample close to 1H and compute EMA(50)
    HTF_1H_PATH = r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 60-1y.csv"
    if "htf_ma_tv" in df.columns:
        df["htf_ma"] = df["htf_ma_tv"]
    else:
        # Build base from resample (covers full history)
        h1_self = df["close"].resample("1h").last().dropna()
        h1_ema_self = ema(h1_self, HTF_MA_LEN)
        htf_base = h1_ema_self.reindex(df.index, method="ffill")
        df["htf_ma"] = htf_base

        # Overlay with exact TV 1H EMA where the 60.csv file covers
        try:
            h1_df = pd.read_csv(HTF_1H_PATH)
            h1_df.columns = [c.strip().lower().replace(" ", "_") for c in h1_df.columns]
            # Use htf_ma.1 = EMA column exported by indicator
            val_col = "htf_ma.1" if "htf_ma.1" in h1_df.columns else "htf_ma"
            h1_df["ts"] = pd.to_datetime(h1_df["time"].astype(int), unit="s", utc=True)
            h1_df = h1_df.set_index("ts")[[val_col]].rename(columns={val_col: "htf1h"}).sort_index()
            # Forward-fill 1H values into sub-hourly bars
            merged = h1_df.reindex(df.index, method="ffill")
            covered = merged["htf1h"].notna()
            df.loc[covered, "htf_ma"] = merged.loc[covered, "htf1h"]
            n_covered = covered.sum()
            n_total   = len(df)
            print(f"    1H TV file: {n_covered}/{n_total} bars covered ({n_covered*100//n_total}%)")
        except Exception as e:
            print(f"    1H file not loaded: {e}")

    df["htf_bull"] = df["close"] > df["htf_ma"]
    df["htf_bear"] = df["close"] < df["htf_ma"]
    df["in_sess"]   = df.index.map(in_session)
    # Candle helpers
    df["cr"]        = df["high"] - df["low"]
    df["uw"]        = df["high"] - df[["open", "close"]].max(axis=1)
    df["lw"]        = df[["open", "close"]].min(axis=1) - df["low"]
    df["body_pct"]  = np.where(df["cr"] > 0, (df["close"] - df["open"]).abs() / df["cr"], 0)
    df["strong_red"]  = (df["close"] < df["open"]) & (df["body_pct"] > 0.55)
    df["strong_grn"]  = (df["close"] > df["open"]) & (df["body_pct"] > 0.55)
    df["bear_engulf"] = (df["close"] < df["open"]) & (df["close"] < df["low"].shift(1)) & (df["open"] >= df["close"].shift(1))
    df["bull_engulf"] = (df["close"] > df["open"]) & (df["close"] > df["high"].shift(1)) & (df["open"] <= df["close"].shift(1))
    # Pivots
    df["ph5"]  = pivot_high(df["high"], 5, 5)
    df["pl5"]  = pivot_low(df["low"],   5, 5)
    df["ph_sw"] = pivot_high(df["high"], SWEEP_PIVOT_LEN, SWEEP_PIVOT_LEN)
    df["pl_sw"] = pivot_low(df["low"],   SWEEP_PIVOT_LEN, SWEEP_PIVOT_LEN)
    df["ph_fib"] = pivot_high(df["high"], FIB_PIVOT_LEN, FIB_PIVOT_LEN)
    df["pl_fib"] = pivot_low(df["low"],   FIB_PIVOT_LEN, FIB_PIVOT_LEN)
    return df


# ─── Signal detection engine ───────────────────────────────────────────────────

def s2_key_ok(price: float) -> bool:
    if S2_KEY_LEVEL_STEP <= 0:
        return True
    return abs(price - round(price / S2_KEY_LEVEL_STEP) * S2_KEY_LEVEL_STEP) <= S2_KEY_LEVEL_RANGE


def detect_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Walk bar-by-bar (simulate Pine execution) and detect all 5 setup signals.
    Returns df with columns: s1_sell, s1_buy, s2_sell, s2_buy, ... s5_buy
    and SL/TP columns per signal.
    """
    n = len(df)
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    atr14 = df["atr14"].values
    atr14_ema = df["atr14_ema"].values
    range_ok = df["range_ok"].values
    ema9  = df["ema9"].values
    ema15 = df["ema15"].values
    vwap  = df["vwap"].values
    htf_bull = df["htf_bull"].values
    htf_bear = df["htf_bear"].values
    in_sess  = df["in_sess"].values
    cr, uw, lw = df["cr"].values, df["uw"].values, df["lw"].values
    strong_red = df["strong_red"].values
    strong_grn = df["strong_grn"].values
    bear_engulf = df["bear_engulf"].values
    bull_engulf = df["bull_engulf"].values

    # Output arrays
    sig = {f"s{i}_{d}": np.zeros(n, dtype=bool) for i in range(1, 6) for d in ("sell", "buy")}
    sl_arr  = {f"s{i}_{d}": np.full(n, np.nan) for i in range(1, 6) for d in ("sell", "buy")}
    tp_arr  = {f"s{i}_{d}": np.full(n, np.nan) for i in range(1, 6) for d in ("sell", "buy")}

    # State — zones
    bear_zt, bear_zb, bear_zc, bear_ze = [], [], [], []   # top, bottom, created_bar, entered
    bull_zt, bull_zb, bull_zc, bull_ze = [], [], [], []

    # State — pivots
    last_sh = np.nan      # struct high (5,5)
    last_sl = np.nan      # struct low  (5,5)
    last_sh_hist = np.full(n, np.nan)  # historical for S4
    last_sl_hist = np.full(n, np.nan)
    last_sw_h = np.nan   # swing high (sweep)
    last_sw_l = np.nan
    fib_imp_h, fib_imp_l = np.nan, np.nan
    fib_imp_h_bar, fib_imp_l_bar = -999, -999

    # State — S1
    bars_sell_band = 0
    bars_buy_band  = 0

    # State — S2 touch counter (per-zone ref)
    bear_zone_ref   = np.nan
    bull_zone_ref   = np.nan
    bear_touches    = 0
    bull_touches    = 0

    # State — S3 wick
    last_wick_top = np.nan
    last_wick_bot = np.nan

    MINTICK = 0.01
    BUF = MINTICK * 5

    for i in range(n):
        a14 = atr14[i] if not np.isnan(atr14[i]) else 1.0
        a14e = atr14_ema[i] if not np.isnan(atr14_ema[i]) else 1.0

        # Update pivots
        if not np.isnan(df["ph5"].iloc[i]):
            last_sh = df["ph5"].iloc[i]
        if not np.isnan(df["pl5"].iloc[i]):
            last_sl = df["pl5"].iloc[i]
        last_sh_hist[i] = last_sh
        last_sl_hist[i] = last_sl
        if not np.isnan(df["ph_sw"].iloc[i]):
            last_sw_h = df["ph_sw"].iloc[i]
        if not np.isnan(df["pl_sw"].iloc[i]):
            last_sw_l = df["pl_sw"].iloc[i]
        if not np.isnan(df["ph_fib"].iloc[i]):
            fib_imp_h = df["ph_fib"].iloc[i]
            fib_imp_h_bar = i - FIB_PIVOT_LEN
        if not np.isnan(df["pl_fib"].iloc[i]):
            fib_imp_l = df["pl_fib"].iloc[i]
            fib_imp_l_bar = i - FIB_PIVOT_LEN

        # 4-candle momentum for zone creation
        if i >= 4:
            bear_mom = c[i] < c[i-1] < c[i-2] < c[i-3] < c[i-4]
            bull_mom = c[i] > c[i-1] > c[i-2] > c[i-3] > c[i-4]

            if bear_mom and htf_bear[i]:
                zt = h[i-4]
                drop = c[i-4] - c[i]
                if (drop >= a14e * S2_MIN_IMPULSE_ATR) and s2_key_ok(zt):
                    if len(bear_zt) >= 3:
                        bear_zt.pop(0); bear_zb.pop(0); bear_zc.pop(0); bear_ze.pop(0)
                    bear_zt.append(zt); bear_zb.append(l[i-4])
                    bear_zc.append(i);  bear_ze.append(False)

            if bull_mom and htf_bull[i]:
                zb = l[i-4]
                rise = c[i] - c[i-4]
                if (rise >= a14e * S2_MIN_IMPULSE_ATR) and s2_key_ok(zb):
                    if len(bull_zt) >= 3:
                        bull_zt.pop(0); bull_zb.pop(0); bull_zc.pop(0); bull_ze.pop(0)
                    bull_zt.append(h[i-4]); bull_zb.append(zb)
                    bull_zc.append(i);      bull_ze.append(False)

        # Zone invalidation (price break OR age)
        inv_buf = a14 * ZONE_INVALID_ATR
        for zi in range(len(bear_zt) - 1, -1, -1):
            price_bad = c[i] > bear_zt[zi] + inv_buf
            age_bad   = (i - bear_zc[zi]) > S2_MAX_ZONE_AGE
            if price_bad or age_bad:
                bear_zt.pop(zi); bear_zb.pop(zi); bear_zc.pop(zi); bear_ze.pop(zi)
        for zi in range(len(bull_zt) - 1, -1, -1):
            price_bad = c[i] < bull_zb[zi] - inv_buf
            age_bad   = (i - bull_zc[zi]) > S2_MAX_ZONE_AGE
            if price_bad or age_bad:
                bull_zt.pop(zi); bull_zb.pop(zi); bull_zc.pop(zi); bull_ze.pop(zi)

        # In-zone detection
        in_sell = False; sz_top = np.nan; sz_bot = np.nan; sz_idx = -1
        for zi in range(len(bear_zt)):
            if h[i] >= bear_zb[zi] and l[i] <= bear_zt[zi]:
                in_sell = True; sz_top = bear_zt[zi]; sz_bot = bear_zb[zi]; sz_idx = zi; break
        sell_fresh = (sz_idx >= 0 and not bear_ze[sz_idx]) if sz_idx >= 0 else False

        in_buy = False; bz_top = np.nan; bz_bot = np.nan; bz_idx = -1
        for zi in range(len(bull_zt)):
            if h[i] >= bull_zb[zi] and l[i] <= bull_zt[zi]:
                in_buy = True; bz_top = bull_zt[zi]; bz_bot = bull_zb[zi]; bz_idx = zi; break
        buy_fresh = (bz_idx >= 0 and not bull_ze[bz_idx]) if bz_idx >= 0 else False

        # Common gate
        sideways = not bool(range_ok[i]) if not np.isnan(range_ok[i]) else True
        sess_ok  = bool(in_sess[i])
        # Skip first 30 min of session (18:30–18:59 IST) — overlap noise kills S2
        _ist = df.index[i].tz_convert(IST)
        s2_session_ok = not (_ist.hour == 18 and _ist.minute >= 30)
        gate_ok  = (not sideways) and sess_ok

        # ── Setup #1: 50-Min Wait ──────────────────────────────────────────────
        if not np.isnan(sz_bot) and abs(c[i] - sz_bot) <= a14 * WAIT_BAND_ATR:
            bars_sell_band = min(bars_sell_band + 1, WAIT_BARS_50M * 3)
        else:
            bars_sell_band = 0
        if not np.isnan(bz_top) and abs(c[i] - bz_top) <= a14 * WAIT_BAND_ATR:
            bars_buy_band = min(bars_buy_band + 1, WAIT_BARS_50M * 3)
        else:
            bars_buy_band = 0

        if SETUPS_ON[1] and gate_ok and htf_bear[i]:
            if bars_sell_band >= WAIT_BARS_50M and c[i] < o[i] and i > 0 and c[i] < l[i-1] and in_sell:
                sig["s1_sell"][i] = True
                sl_val = sz_top + BUF
                sl_arr["s1_sell"][i] = sl_val
                tp_arr["s1_sell"][i] = c[i] - (sl_val - c[i]) * FINAL_TARGET_RR

        if SETUPS_ON[1] and gate_ok and htf_bull[i]:
            if bars_buy_band >= WAIT_BARS_50M and c[i] > o[i] and i > 0 and c[i] > h[i-1] and in_buy:
                sig["s1_buy"][i] = True
                sl_val = bz_bot - BUF
                sl_arr["s1_buy"][i] = sl_val
                tp_arr["s1_buy"][i] = c[i] + (c[i] - sl_val) * FINAL_TARGET_RR

        # ── Setup #2: Zone Pyramid — per-zone touch counter ────────────────────
        if in_sell:
            if np.isnan(bear_zone_ref) or sz_top != bear_zone_ref:
                bear_touches = 1; bear_zone_ref = sz_top
            elif (i > 0 and not (h[i-1] >= bear_zb[sz_idx] and l[i-1] <= bear_zt[sz_idx])):
                bear_touches += 1
        else:
            # Check if ref zone still exists
            if not np.isnan(bear_zone_ref):
                if bear_zone_ref not in bear_zt:
                    bear_zone_ref = np.nan; bear_touches = 0

        if in_buy:
            if np.isnan(bull_zone_ref) or bz_top != bull_zone_ref:
                bull_touches = 1; bull_zone_ref = bz_top
            elif (i > 0 and not (h[i-1] >= bull_zb[bz_idx] and l[i-1] <= bull_zt[bz_idx])):
                bull_touches += 1
        else:
            if not np.isnan(bull_zone_ref):
                if bull_zone_ref not in bull_zt:
                    bull_zone_ref = np.nan; bull_touches = 0

        ema_above = ema9[i] > c[i] and ema15[i] > c[i]
        ema_below = ema9[i] < c[i] and ema15[i] < c[i]
        vwap_above = vwap[i] > c[i]
        vwap_below = vwap[i] < c[i]
        s2_sell_conf = ema_above or vwap_above
        s2_buy_conf  = ema_below or vwap_below

        if SETUPS_ON[2] and gate_ok and s2_session_ok and htf_bear[i] and in_sell and bear_touches >= ZONE_TOUCHES_MIN:
            if (bear_engulf[i] or strong_red[i]) and s2_sell_conf:
                if not S2_FRESH_ZONE_ONLY or sell_fresh:
                    sl_val = sz_top + BUF
                    sl_dist = abs(c[i] - sl_val)
                    if not (S2_SL_SKIP_MIN <= sl_dist <= S2_SL_SKIP_MAX):
                        sig["s2_sell"][i] = True
                        sl_arr["s2_sell"][i] = sl_val
                        tp_arr["s2_sell"][i] = c[i] - (sl_val - c[i]) * FINAL_TARGET_RR
                        if sz_idx >= 0: bear_ze[sz_idx] = True

        if SETUPS_ON[2] and gate_ok and s2_session_ok and htf_bull[i] and in_buy and bull_touches >= ZONE_TOUCHES_MIN:
            if (bull_engulf[i] or strong_grn[i]) and s2_buy_conf:
                if not S2_FRESH_ZONE_ONLY or buy_fresh:
                    sl_val = bz_bot - BUF
                    sl_dist = abs(c[i] - sl_val)
                    if not (S2_SL_SKIP_MIN <= sl_dist <= S2_SL_SKIP_MAX):
                        sig["s2_buy"][i] = True
                        sl_arr["s2_buy"][i] = sl_val
                        tp_arr["s2_buy"][i] = c[i] + (c[i] - sl_val) * FINAL_TARGET_RR
                        if bz_idx >= 0: bull_ze[bz_idx] = True

        # ── Setup #3: Liquidity Sweep ──────────────────────────────────────────
        bear_sweep = False; bull_sweep = False
        if not np.isnan(last_sw_h) and cr[i] > 0:
            if (h[i] > last_sw_h and c[i] < last_sw_h and c[i] < o[i] and
                    uw[i] > cr[i] * (SWEEP_MIN_WICK_PCT / 100) and
                    c[i] < (h[i] - cr[i] * 0.4)):
                bear_sweep = True
        if not np.isnan(last_sw_l) and cr[i] > 0:
            if (l[i] < last_sw_l and c[i] > last_sw_l and c[i] > o[i] and
                    lw[i] > cr[i] * (SWEEP_MIN_WICK_PCT / 100) and
                    c[i] > (l[i] + cr[i] * 0.4)):
                bull_sweep = True

        if bear_sweep: last_wick_top = h[i]
        if bull_sweep: last_wick_bot = l[i]

        if SETUPS_ON[3] and gate_ok and htf_bear[i] and bear_sweep:
            sl_val = h[i] + BUF
            sig["s3_sell"][i] = True
            sl_arr["s3_sell"][i] = sl_val
            tp_arr["s3_sell"][i] = c[i] - (sl_val - c[i]) * FINAL_TARGET_RR

        if SETUPS_ON[3] and gate_ok and htf_bull[i] and bull_sweep:
            sl_val = l[i] - BUF
            sig["s3_buy"][i] = True
            sl_arr["s3_buy"][i] = sl_val
            tp_arr["s3_buy"][i] = c[i] + (c[i] - sl_val) * FINAL_TARGET_RR

        # ── Setup #4: Fake Breakout (historical struct levels) ─────────────────
        bear_fake = False; bull_fake = False
        if SETUPS_ON[4]:
            for fw in range(1, min(FAKE_BREAK_WINDOW + 1, i + 1)):
                lvl_h = last_sh_hist[i - fw]
                if not np.isnan(lvl_h) and c[i - fw] > lvl_h and c[i] < lvl_h and c[i] < o[i]:
                    bear_fake = True; break
            for fw in range(1, min(FAKE_BREAK_WINDOW + 1, i + 1)):
                lvl_l = last_sl_hist[i - fw]
                if not np.isnan(lvl_l) and c[i - fw] < lvl_l and c[i] > lvl_l and c[i] > o[i]:
                    bull_fake = True; break

            if gate_ok and htf_bear[i] and bear_fake:
                fb_h = np.nan
                for fw in range(1, min(FAKE_BREAK_WINDOW + 1, i + 1)):
                    lvl_h = last_sh_hist[i - fw]
                    if not np.isnan(lvl_h) and c[i - fw] > lvl_h:
                        fb_h = h[i - fw] if np.isnan(fb_h) else max(fb_h, h[i - fw])
                if not np.isnan(fb_h):
                    sl_val = fb_h + BUF
                    sig["s4_sell"][i] = True
                    sl_arr["s4_sell"][i] = sl_val
                    tp_arr["s4_sell"][i] = c[i] - (sl_val - c[i]) * FINAL_TARGET_RR

            if gate_ok and htf_bull[i] and bull_fake:
                fb_l = np.nan
                for fw in range(1, min(FAKE_BREAK_WINDOW + 1, i + 1)):
                    lvl_l = last_sl_hist[i - fw]
                    if not np.isnan(lvl_l) and c[i - fw] < lvl_l:
                        fb_l = l[i - fw] if np.isnan(fb_l) else min(fb_l, l[i - fw])
                if not np.isnan(fb_l):
                    sl_val = fb_l - BUF
                    sig["s4_buy"][i] = True
                    sl_arr["s4_buy"][i] = sl_val
                    tp_arr["s4_buy"][i] = c[i] + (c[i] - sl_val) * FINAL_TARGET_RR

        # ── Setup #5: Fib 50-61% Reject (tight close check) ───────────────────
        if SETUPS_ON[5]:
            is_bear_imp = (not np.isnan(fib_imp_h) and not np.isnan(fib_imp_l) and
                           fib_imp_l_bar > fib_imp_h_bar and
                           (fib_imp_h - fib_imp_l) > a14 * FIB_MIN_IMPULSE_ATR)
            is_bull_imp = (not np.isnan(fib_imp_h) and not np.isnan(fib_imp_l) and
                           fib_imp_h_bar > fib_imp_l_bar and
                           (fib_imp_h - fib_imp_l) > a14 * FIB_MIN_IMPULSE_ATR)

            if is_bear_imp:
                rng = fib_imp_h - fib_imp_l
                f50      = fib_imp_l + rng * FIB_BAND_LOW  / 100
                f61      = fib_imp_l + rng * FIB_BAND_HIGH / 100
                f_inv    = fib_imp_l + rng * FIB_INVALIDATE / 100
                in_zone  = h[i] >= f50 and c[i] <= f61
                if gate_ok and htf_bear[i] and in_zone and (bear_engulf[i] or strong_red[i]):
                    sl_val = f_inv + BUF
                    sig["s5_sell"][i] = True
                    sl_arr["s5_sell"][i] = sl_val
                    tp_arr["s5_sell"][i] = c[i] - (sl_val - c[i]) * FINAL_TARGET_RR

            if is_bull_imp:
                rng = fib_imp_h - fib_imp_l
                f50      = fib_imp_h - rng * FIB_BAND_LOW  / 100
                f61      = fib_imp_h - rng * FIB_BAND_HIGH / 100
                f_inv    = fib_imp_h - rng * FIB_INVALIDATE / 100
                in_zone  = l[i] <= f50 and c[i] >= f61
                if gate_ok and htf_bull[i] and in_zone and (bull_engulf[i] or strong_grn[i]):
                    sl_val = f_inv - BUF
                    sig["s5_buy"][i] = True
                    sl_arr["s5_buy"][i] = sl_val
                    tp_arr["s5_buy"][i] = c[i] + (c[i] - sl_val) * FINAL_TARGET_RR

    # Attach to df
    for k, v in sig.items():
        df[k] = v
    for k, v in sl_arr.items():
        df["sl_" + k] = v
    for k, v in tp_arr.items():
        df["tp_" + k] = v

    return df


# ─── Trade simulation ──────────────────────────────────────────────────────────

SETUP_NAMES = {1: "50min-Wait", 2: "Zone-Pyramid", 3: "Liq-Sweep",
               4: "Fake-Break",  5: "Fib-Reject"}


def simulate_trades(df: pd.DataFrame, tf_label: str) -> pd.DataFrame:
    """
    Walk bar-by-bar, enter on signal close, manage SL/TP/trail/time-stop.
    Returns DataFrame of completed trades.
    """
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values

    trades = []
    equity = INITIAL_CAPITAL

    # Daily state
    last_date   = None
    trades_today = 0
    day_start_eq = equity
    consec_losses = 0

    in_trade = False
    entry_price = sl = tp = init_sl = 0.0
    direction   = 0   # +1 long, -1 short
    entry_bar   = 0
    setup_id    = 0
    partial_done = False

    def trade_ok():
        nonlocal trades_today, day_start_eq
        daily_dd_hit  = (equity - day_start_eq) <= -(day_start_eq * DAILY_DD_PCT / 100)
        total_dd_hit  = (INITIAL_CAPITAL - equity) >= INITIAL_CAPITAL * TOTAL_DD_PCT / 100
        cap_hit       = trades_today >= MAX_TRADES_DAY
        loss_hit      = consec_losses >= CONSEC_LOSS_STOP
        return not (daily_dd_hit or total_dd_hit or cap_hit or loss_hit)

    for i in range(len(df)):
        ts   = df.index[i]
        date = ts.date()

        # Day reset — consec_losses resets each day (Manku: "stop for today" not forever)
        if date != last_date:
            trades_today  = 0
            day_start_eq  = equity
            consec_losses = 0
            last_date     = date

        # Manage open trade
        if in_trade:
            r_dist = abs(entry_price - init_sl)
            move   = (c[i] - entry_price) if direction == 1 else (entry_price - c[i])
            r_mul  = move / r_dist if r_dist > 0 else 0

            # Trail to BE
            if r_mul >= TRAIL_BE_RR and sl != entry_price:
                sl = entry_price

            # Partial close at PARTIAL_RR → in this simulation we treat it as full close
            if r_mul >= PARTIAL_RR and not partial_done:
                # close all at partial_rr level for simplicity
                exit_price = entry_price + direction * r_dist * PARTIAL_RR
                pnl_r = PARTIAL_RR
                risk_amt = equity * RISK[setup_id] / 100
                pnl_usd  = risk_amt * pnl_r
                equity  += pnl_usd
                trades.append({
                    "tf": tf_label, "setup": setup_id,
                    "setup_name": SETUP_NAMES[setup_id],
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "entry_time": df.index[entry_bar],
                    "exit_time":  ts,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "sl": init_sl, "tp": tp, "exit_reason": "partial-TP",
                    "pnl_r": round(pnl_r, 3), "pnl_usd": round(pnl_usd, 2),
                    "equity": round(equity, 2)
                })
                if pnl_usd < 0: consec_losses += 1
                else:           consec_losses = 0
                in_trade = False; partial_done = False
                continue

            # Time-stop
            if TIME_STOP_BARS > 0 and (i - entry_bar) >= TIME_STOP_BARS and r_mul < TRAIL_BE_RR:
                exit_price = c[i]
                pnl_r = move / r_dist if r_dist > 0 else 0
                risk_amt = equity * RISK[setup_id] / 100
                pnl_usd  = risk_amt * pnl_r
                equity  += pnl_usd
                trades.append({
                    "tf": tf_label, "setup": setup_id,
                    "setup_name": SETUP_NAMES[setup_id],
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "entry_time": df.index[entry_bar],
                    "exit_time":  ts,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "sl": init_sl, "tp": tp, "exit_reason": "time-stop",
                    "pnl_r": round(pnl_r, 3), "pnl_usd": round(pnl_usd, 2),
                    "equity": round(equity, 2)
                })
                if pnl_usd < 0: consec_losses += 1
                else:           consec_losses = 0
                in_trade = False; partial_done = False
                continue

            # Check SL hit (wick)
            sl_hit = (direction == 1 and l[i] <= sl) or (direction == -1 and h[i] >= sl)
            tp_hit = (direction == 1 and h[i] >= tp) or (direction == -1 and l[i] <= tp)

            if tp_hit:
                exit_price = tp
                pnl_r = abs(tp - entry_price) / r_dist if r_dist > 0 else 0
                risk_amt = equity * RISK[setup_id] / 100
                pnl_usd  = risk_amt * pnl_r
                equity  += pnl_usd
                trades.append({
                    "tf": tf_label, "setup": setup_id,
                    "setup_name": SETUP_NAMES[setup_id],
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "entry_time": df.index[entry_bar],
                    "exit_time":  ts,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "sl": init_sl, "tp": tp, "exit_reason": "TP",
                    "pnl_r": round(pnl_r, 3), "pnl_usd": round(pnl_usd, 2),
                    "equity": round(equity, 2)
                })
                consec_losses = 0
                in_trade = False; partial_done = False
                continue

            if sl_hit:
                exit_price = sl
                pnl_r = -abs(exit_price - entry_price) / r_dist if r_dist > 0 else -1
                risk_amt = equity * RISK[setup_id] / 100
                pnl_usd  = risk_amt * pnl_r
                equity  += pnl_usd
                trades.append({
                    "tf": tf_label, "setup": setup_id,
                    "setup_name": SETUP_NAMES[setup_id],
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "entry_time": df.index[entry_bar],
                    "exit_time":  ts,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "sl": init_sl, "tp": tp, "exit_reason": "SL",
                    "pnl_r": round(pnl_r, 3), "pnl_usd": round(pnl_usd, 2),
                    "equity": round(equity, 2)
                })
                consec_losses += 1
                in_trade = False; partial_done = False
                continue

        # Check new entries (only when flat + risk filters pass)
        if not in_trade and trade_ok():
            # Priority: S1→S2→S3→S4→S5
            for sid in [1, 2, 3, 4, 5]:
                for d in ["sell", "buy"]:
                    key = f"s{sid}_{d}"
                    if df[key].iloc[i]:
                        sl_val = df[f"sl_{key}"].iloc[i]
                        tp_val = df[f"tp_{key}"].iloc[i]
                        if np.isnan(sl_val) or np.isnan(tp_val): continue
                        risk_dist = abs(c[i] - sl_val)
                        if risk_dist <= 0: continue
                        entry_price = c[i]
                        sl          = sl_val
                        tp          = tp_val
                        init_sl     = sl_val
                        direction   = 1 if d == "buy" else -1
                        entry_bar   = i
                        setup_id    = sid
                        in_trade    = True
                        partial_done = False
                        trades_today += 1
                        break
                if in_trade: break

    return pd.DataFrame(trades)


# ─── Stats ─────────────────────────────────────────────────────────────────────

def stats(trades_df: pd.DataFrame, label: str = "") -> dict:
    if trades_df.empty:
        return {"label": label, "trades": 0}
    n = len(trades_df)
    wins = (trades_df["pnl_r"] > 0).sum()
    win_rate = wins / n * 100
    gross_win  = trades_df[trades_df["pnl_r"] > 0]["pnl_usd"].sum()
    gross_loss = abs(trades_df[trades_df["pnl_r"] <= 0]["pnl_usd"].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    avg_win  = trades_df[trades_df["pnl_r"] > 0]["pnl_r"].mean() if wins > 0 else 0
    avg_loss = trades_df[trades_df["pnl_r"] <= 0]["pnl_r"].mean() if (n - wins) > 0 else 0
    net_pnl  = trades_df["pnl_usd"].sum()
    # Max drawdown on equity curve
    eq = trades_df["equity"].values
    peak = np.maximum.accumulate(np.concatenate([[INITIAL_CAPITAL], eq]))
    dd = (peak[1:] - eq) / peak[1:] * 100
    max_dd = dd.max() if len(dd) > 0 else 0
    return {
        "label": label, "trades": n, "wins": int(wins),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "avg_win_r": round(avg_win, 2),
        "avg_loss_r": round(avg_loss, 2),
        "net_pnl_usd": round(net_pnl, 2),
        "max_dd_pct": round(max_dd, 1),
        "final_equity": round(trades_df["equity"].iloc[-1], 2) if n > 0 else INITIAL_CAPITAL
    }


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    all_trades = []
    summary_rows = []

    for tf, path in FILES.items():
        print(f"\n{'='*60}")
        print(f"  Processing {tf} — {path}")
        print(f"{'='*60}")
        df = load(path)
        print(f"  Bars: {len(df)}  |  {df.index[0]} to {df.index[-1]}")
        df = prepare(df)
        df = detect_signals(df)
        total_signals = sum(df[f"s{i}_{d}"].sum() for i in range(1, 6) for d in ("sell","buy"))
        print(f"  Total signals: {total_signals}")

        trades_df = simulate_trades(df, tf)
        all_trades.append(trades_df)

        # Per-setup breakdown
        setup_rows = []
        print(f"\n  {'Setup':<20} {'N':>4} {'Win%':>6} {'PF':>6} {'AvgW':>7} {'AvgL':>7} {'NetPnL':>9} {'MaxDD%':>7}")
        print(f"  {'-'*65}")
        for sid in [1, 2, 3, 4, 5]:
            s_trades = trades_df[trades_df["setup"] == sid] if not trades_df.empty else pd.DataFrame()
            st = stats(s_trades, f"{tf} #{sid} {SETUP_NAMES[sid]}")
            setup_rows.append(st)
            if st["trades"] > 0:
                print(f"  #{sid} {SETUP_NAMES[sid]:<15} {st['trades']:>4}  {st['win_rate']:>5.1f}%  "
                      f"{st['profit_factor']:>5.2f}  {st['avg_win_r']:>+6.2f}R  {st['avg_loss_r']:>+6.2f}R  "
                      f"${st['net_pnl_usd']:>8.0f}  {st['max_dd_pct']:>5.1f}%")
            else:
                print(f"  #{sid} {SETUP_NAMES[sid]:<15} {'--':>4}")
            st["tf"] = tf; st["setup_id"] = sid
            summary_rows.append(st)

        if not trades_df.empty:
            all_st = stats(trades_df, f"{tf} ALL")
            print(f"  {'ALL':>20} {all_st['trades']:>4}  {all_st['win_rate']:>5.1f}%  "
                  f"{all_st['profit_factor']:>5.2f}  {all_st['avg_win_r']:>+6.2f}R  {all_st['avg_loss_r']:>+6.2f}R  "
                  f"${all_st['net_pnl_usd']:>8.0f}  {all_st['max_dd_pct']:>5.1f}%")

    # Signal log
    all_trades_df = pd.concat([t for t in all_trades if not t.empty], ignore_index=True)
    if not all_trades_df.empty:
        all_trades_df = all_trades_df.sort_values("entry_time")
        all_trades_df["session"] = all_trades_df["entry_time"].apply(get_session_label)
        all_trades_df.to_csv(r"D:\Trade\manku_signals.csv", index=False)
        print(f"\n  Signal log -> D:\\Trade\\manku_signals.csv  ({len(all_trades_df)} trades)")

        # S2 5M session breakdown
        s2_5m = all_trades_df[(all_trades_df["setup"] == 2) & (all_trades_df["tf"] == "5M")]
        if not s2_5m.empty and ADD_LONDON_SESSION:
            print(f"\n{'='*60}")
            print("  S2 Zone-Pyramid 5M — SESSION BREAKDOWN")
            print(f"{'='*60}")
            print(f"  {'Session':<10} {'N':>4}  {'Win%':>6}  {'Net P&L':>9}")
            print(f"  {'-'*38}")
            for sess in ["NY", "London", "other"]:
                sub = s2_5m[s2_5m["session"] == sess]
                if len(sub) == 0:
                    continue
                n    = len(sub)
                wins = (sub["pnl_r"] > 0).sum()
                pnl  = sub["pnl_usd"].sum()
                print(f"  {sess:<10} {n:>4}  {wins/n*100:>5.1f}%  ${pnl:>+8.0f}")
            print(f"  {'TOTAL':<10} {len(s2_5m):>4}  {(s2_5m['pnl_r']>0).sum()/len(s2_5m)*100:>5.1f}%  ${s2_5m['pnl_usd'].sum():>+8.0f}")

    # Summary CSV
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(r"D:\Trade\manku_summary.csv", index=False)
    print(f"  Summary   -> D:\\Trade\\manku_summary.csv")

    # Cross-TF winner
    if not summary_df.empty:
        valid = summary_df[summary_df["trades"] > 0].copy()
        if not valid.empty:
            print(f"\n{'='*60}")
            print("  BEST SETUP PER METRIC")
            print(f"{'='*60}")
            best_wr  = valid.loc[valid["win_rate"].idxmax()]
            best_pf  = valid.loc[valid["profit_factor"].idxmax()]
            best_net = valid.loc[valid["net_pnl_usd"].idxmax()]
            print(f"  Best Win Rate   : {best_wr['label']} -> {best_wr['win_rate']}%  ({best_wr['trades']} trades)")
            print(f"  Best Prof Factor: {best_pf['label']} -> {best_pf['profit_factor']}  ({best_pf['trades']} trades)")
            print(f"  Best Net P&L    : {best_net['label']} -> ${best_net['net_pnl_usd']}  ({best_net['trades']} trades)")
            low_dd = valid.loc[valid["max_dd_pct"].idxmin()]
            print(f"  Lowest Max DD   : {low_dd['label']} -> {low_dd['max_dd_pct']}%")

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
