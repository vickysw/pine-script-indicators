"""
Manku S2 - Zone Pyramid v2 - Backtest
Faithful Python translation of Manku-S2-ZonePyramid_v2.pine
Three modes: Normal | 5M Lock | MTF 15M+5M
"""

import pandas as pd
import numpy as np
import pytz
from pathlib import Path

IST = pytz.timezone("Asia/Kolkata")

# --- CONFIG  (mirrors Pine defaults exactly) -----------------------------------
CFG = {
    # 0. Signal Modes
    "SHOW_NORMAL":  True,
    "SHOW_5M":      True,   # always active - backtest runs on 5M data
    "SHOW_MTF":     True,
    "MTF_WINDOW":   6,      # bars (5M bars) after 15M signal
    "SESS_NORMAL":  "NY only",      # NY only / NY+London / All sessions
    "SESS_5M":      "NY+London",
    "SESS_MTF":     "NY+London",

    # 1. Bias
    "HTF_LEN":      50,
    "REQUIRE_HTF":  True,

    # 2. Zone Quality
    "ZONE_TOUCHES_MIN": 2,
    "ZONE_INVALID_ATR": 1.5,
    "S2_MIN_IMPULSE":   1.5,
    "S2_MAX_AGE":       288,
    "S2_KEY_STEP":      50.0,
    "S2_KEY_RANGE":     15.0,

    # 2b. Zone Quality (optimized from filter analysis)
    "S2_FRESH_ONLY":    False,   # False: used zones also valid (+37 trades, PF 1.30->1.70)

    # 3. Filters (optimized from filter analysis 2026-05-28)
    "SKIP_OPEN_30M": False,  # False: NY open 30min signals actually better (WR 45% vs 37%)
    "SL_SKIP_MIN":   5.0,    # was 3.0 — shifted dead zone to 5-8pt range
    "SL_SKIP_MAX":   8.0,    # was 6.0 — net/month +$187, same max DD 6.9%

    # 4. Trade
    "ACCOUNT_SIZE": 5000.0,
    "RISK_PCT":     1.5,
    "TRAIL_BE_RR":  1.0,   # trail SL to entry at 1R
    "T1_RR":        1.5,   # partial close half position
    "T2_RR":        2.0,   # final close
}

# --- DATA FILES ----------------------------------------------------------------
# After running fetch_gold.py with MONTHS=24 -> use gold_24m.csv
FILE_5M  = r"D:\Trade\tvHistoryData\gold_6m.csv"     # swap to gold_24m.csv after fetch
FILE_15M = r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 15.csv"
FILE_1H  = r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 60-1y.csv"

MINTICK = 0.01
BUF     = MINTICK * 5   # mirrors Pine: syminfo.mintick * 5
MAX_ZONES = 3


# --- DATA LOADING --------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    if "time" in df.columns:
        df["ts"] = pd.to_datetime(df["time"].astype(np.int64), unit="s", utc=True)
    elif "datetime" in df.columns:
        df["ts"] = pd.to_datetime(df["datetime"], utc=True)
    else:
        raise ValueError(f"Unknown timestamp column in {path}")
    df = df.set_index("ts").sort_index()
    cols = [c for c in ["open", "high", "low", "close"] if c in df.columns]
    return df[cols].astype(float)


# --- INDICATORS ----------------------------------------------------------------

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def wilder_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([
        df.high - df.low,
        (df.high - df.close.shift(1)).abs(),
        (df.low  - df.close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    alpha  = 1.0 / n
    result = np.full(len(tr), np.nan)
    if n - 1 < len(tr):
        result[n - 1] = tr.iloc[:n].mean()
        for i in range(n, len(tr)):
            result[i] = alpha * tr.iloc[i] + (1 - alpha) * result[i - 1]
    return pd.Series(result, index=df.index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP resetting each UTC day - simple loop avoids pandas 2.x groupby bool bug."""
    hlc3 = (df.high + df.low + df.close) / 3.0
    vals = np.empty(len(df))
    cum_s = 0.0; cum_n = 0; prev_day = None
    for i, (ts, v) in enumerate(zip(df.index, hlc3.values)):
        day = ts.date()
        if day != prev_day:
            cum_s = 0.0; cum_n = 0; prev_day = day
        cum_s += v; cum_n += 1
        vals[i] = cum_s / cum_n
    return pd.Series(vals, index=df.index)


def htf_ema50(df_5m: pd.DataFrame, df_1h: pd.DataFrame) -> pd.Series:
    """1H EMA(50) forward-filled to 5M bars.
    reindex alone fails when 1H timestamps don't exactly match 5M timestamps -
    merge_asof fills every 5M bar with the last available 1H EMA value.
    """
    h1_ema = _ema(df_1h.close, CFG["HTF_LEN"]).rename("htf_ma").reset_index()
    df_5m_reset = df_5m[[]].reset_index()   # just the timestamp column
    merged = pd.merge_asof(df_5m_reset.sort_values("ts"),
                           h1_ema.sort_values("ts"),
                           on="ts", direction="backward")
    merged = merged.set_index("ts")
    result = merged["htf_ma"].reindex(df_5m.index)
    return result


# --- SESSION HELPERS -----------------------------------------------------------

def _ist_mins(ts: pd.Timestamp) -> int:
    t = ts.tz_convert(IST)
    return t.hour * 60 + t.minute


def in_ny(ts):     return 18 * 60 + 30 <= _ist_mins(ts) < 23 * 60 + 30
def in_london(ts): return 13 * 60 + 30 <= _ist_mins(ts) < 21 * 60


def in_session(ts, sess: str) -> bool:
    if sess == "All sessions": return True
    if sess == "NY+London":    return in_ny(ts) or in_london(ts)
    return in_ny(ts)  # "NY only"


def past_open(ts) -> bool:
    if not CFG["SKIP_OPEN_30M"]: return True
    m = _ist_mins(ts)
    return not (18 * 60 + 30 <= m < 18 * 60 + 60)  # block 18:30-18:59 IST


def sl_ok(sl_dist: float) -> bool:
    lo, hi = CFG["SL_SKIP_MIN"], CFG["SL_SKIP_MAX"]
    return hi <= 0.0 or not (lo <= sl_dist <= hi)


def s2_key_ok(price: float) -> bool:
    step = CFG["S2_KEY_STEP"]
    if step <= 0.0: return True
    return abs(price - round(price / step) * step) <= CFG["S2_KEY_RANGE"]


# --- MAIN SIGNAL DETECTION LOOP ------------------------------------------------

def detect_signals(df5: pd.DataFrame, df15: pd.DataFrame, df1h: pd.DataFrame):
    """
    Simulates Pine bar-by-bar execution.
    Returns DataFrame with all signals + SL prices for each of 3 modes.
    """
    # Prepare indicators
    df5 = df5.copy()
    df5["atr14"]  = wilder_atr(df5)
    df5["atr14e"] = _ema(df5["atr14"], 14)
    df5["ema9"]   = _ema(df5.close, 9)
    df5["ema15"]  = _ema(df5.close, 15)
    df5["vwap"]   = session_vwap(df5)
    df5["htf_ma"] = htf_ema50(df5, df1h)
    # fillna(False): htf_ma NaN at start -> pandas 2.x nullable bool -> crashes
    df5["htf_bull"] = (df5.close > df5["htf_ma"]).fillna(False).astype(bool)
    df5["htf_bear"] = (df5.close < df5["htf_ma"]).fillna(False).astype(bool)

    # 15M indicators for MTF gate
    df15 = df15.copy()
    df15["ema9"] = _ema(df15.close, 9)
    df15["vwap"] = session_vwap(df15)

    # 15M lookup: ts -> row  (ts = bar open time)
    m15_idx = {ts: i for i, ts in enumerate(df15.index)}
    m15_o = df15.open.values
    m15_h = df15.high.values
    m15_l = df15.low.values
    m15_c = df15.close.values
    m15_e9  = df15["ema9"].values
    m15_vwap = df15["vwap"].values
    m15_ts   = df15.index

    n = len(df5)
    o = df5.open.values;  h = df5.high.values
    l = df5.low.values;   c = df5.close.values
    atr14  = df5["atr14"].values;  atr14e = df5["atr14e"].values
    ema9   = df5["ema9"].values;   ema15  = df5["ema15"].values
    vwap   = df5["vwap"].values
    htf_bull = df5["htf_bull"].values
    htf_bear = df5["htf_bear"].values

    # Zone state
    bear_zt, bear_zb, bear_zc, bear_ze = [], [], [], []
    bull_zt, bull_zb, bull_zc, bull_ze = [], [], [], []

    # Touch counter state
    bear_ref = np.nan;  bear_touches = 0;  prev_in_sell = False
    bull_ref = np.nan;  bull_touches = 0;  prev_in_buy  = False

    # MTF gate (ta.barssince equivalent - counts 5M bars)
    bars_since_sell15 = 9999
    bars_since_buy15  = 9999

    # Output: list of signal dicts
    signals = []

    for i in range(n):
        ts  = df5.index[i]
        a14  = atr14[i]  if not np.isnan(atr14[i])  else 1.0
        a14e = atr14e[i] if not np.isnan(atr14e[i]) else 1.0

        # -- MTF 15M Gate ----------------------------------------------------
        # Pine: htf15_close1 = request.security("15", close[1]) - last completed 15M bar
        # For 5M bar at time T, the last completed 15M bar = floor(T, 15min) - 15min
        htf15_sell_fired = False
        htf15_buy_fired  = False

        if CFG["SHOW_MTF"]:
            m15_floor = ts.floor("15min")
            bar1_ts   = m15_floor - pd.Timedelta(minutes=15)
            bar2_ts   = m15_floor - pd.Timedelta(minutes=30)
            idx1 = m15_idx.get(bar1_ts)
            idx2 = m15_idx.get(bar2_ts)

            if idx1 is not None:
                o1, h1v, l1, c1 = m15_o[idx1], m15_h[idx1], m15_l[idx1], m15_c[idx1]
                e9_15  = m15_e9[idx1]
                vw_15  = m15_vwap[idx1]

                cur15_idx = m15_idx.get(m15_floor)
                cur15_c   = m15_c[cur15_idx] if cur15_idx is not None else c1

                rng1  = max(h1v - l1, MINTICK)
                body1 = abs(c1 - o1)
                bp1   = body1 / rng1

                # FIXED be1: bar-1 close < bar-2 low (was: close < low same bar = always False)
                l2_val = m15_l[idx2] if idx2 is not None else np.nan
                h2_val = m15_h[idx2] if idx2 is not None else np.nan
                be1 = c1 < o1 and not np.isnan(l2_val) and c1 < l2_val
                sr1 = c1 < o1 and bp1 > 0.55

                be2 = sr2 = False
                if idx2 is not None:
                    o2, h2v, l2v, c2 = m15_o[idx2], m15_h[idx2], m15_l[idx2], m15_c[idx2]
                    body2 = abs(c2 - o2)
                    # FIXED rng2: use bar-2 own range (was: bar-1 range)
                    rng2  = max(h2v - l2v, MINTICK)
                    bp2   = body2 / rng2
                    # FIXED be2: bar-2 close < bar-3 low (was: bar-1 low)
                    idx3  = m15_idx.get(m15_floor - pd.Timedelta(minutes=45))
                    l3_val = m15_l[idx3] if idx3 is not None else np.nan
                    be2 = c2 < o2 and not np.isnan(l3_val) and c2 < l3_val
                    sr2 = c2 < o2 and bp2 > 0.55

                conf_sell = e9_15 > cur15_c or vw_15 > cur15_c
                conf_buy  = e9_15 < cur15_c or vw_15 < cur15_c

                # FIXED: include be1 in sell gate
                htf15_sell_fired = conf_sell and (be1 or sr1 or be2 or sr2)
                # FIXED buy gate: bar-1 close > bar-2 high (was: close > high same bar = always False)
                buy_be1 = c1 > o1 and not np.isnan(h2_val) and c1 > h2_val
                htf15_buy_fired  = conf_buy and (buy_be1 or (c1 > o1 and bp1 > 0.55))

        # barssince update (mirrors Pine ta.barssince)
        if htf15_sell_fired:
            bars_since_sell15 = 0
        elif bars_since_sell15 < 9999:
            bars_since_sell15 += 1

        if htf15_buy_fired:
            bars_since_buy15 = 0
        elif bars_since_buy15 < 9999:
            bars_since_buy15 += 1

        mtf_gate_sell = bars_since_sell15 <= CFG["MTF_WINDOW"]
        mtf_gate_buy  = bars_since_buy15  <= CFG["MTF_WINDOW"]

        # -- Zone Creation ----------------------------------------------------
        if i >= 4:
            bear_mom = c[i]<c[i-1] and c[i-1]<c[i-2] and c[i-2]<c[i-3] and c[i-3]<c[i-4]
            bull_mom = c[i]>c[i-1] and c[i-1]>c[i-2] and c[i-2]>c[i-3] and c[i-3]>c[i-4]

            if bear_mom and htf_bear[i]:
                zt = h[i-4]; drop = c[i-4] - c[i]
                if drop >= a14e * CFG["S2_MIN_IMPULSE"] and s2_key_ok(zt):
                    if len(bear_zt) >= MAX_ZONES:
                        bear_zt.pop(0); bear_zb.pop(0); bear_zc.pop(0); bear_ze.pop(0)
                    bear_zt.append(zt); bear_zb.append(l[i-4])
                    bear_zc.append(i);  bear_ze.append(False)

            if bull_mom and htf_bull[i]:
                zb = l[i-4]; rise = c[i] - c[i-4]
                if rise >= a14e * CFG["S2_MIN_IMPULSE"] and s2_key_ok(zb):
                    if len(bull_zt) >= MAX_ZONES:
                        bull_zt.pop(0); bull_zb.pop(0); bull_zc.pop(0); bull_ze.pop(0)
                    bull_zt.append(h[i-4]); bull_zb.append(zb)
                    bull_zc.append(i);      bull_ze.append(False)

        # -- Zone Invalidation ------------------------------------------------
        inv_buf = a14 * CFG["ZONE_INVALID_ATR"]
        for zi in range(len(bear_zt) - 1, -1, -1):
            if c[i] > bear_zt[zi] + inv_buf or (i - bear_zc[zi]) > CFG["S2_MAX_AGE"]:
                bear_zt.pop(zi); bear_zb.pop(zi); bear_zc.pop(zi); bear_ze.pop(zi)
        for zi in range(len(bull_zt) - 1, -1, -1):
            if c[i] < bull_zb[zi] - inv_buf or (i - bull_zc[zi]) > CFG["S2_MAX_AGE"]:
                bull_zt.pop(zi); bull_zb.pop(zi); bull_zc.pop(zi); bull_ze.pop(zi)

        # -- In-Zone Detection ------------------------------------------------
        in_sell = False; sz_top = np.nan; sz_idx = -1; sell_fresh = False
        for zi in range(len(bear_zt)):
            if h[i] >= bear_zb[zi] and l[i] <= bear_zt[zi]:
                in_sell = True; sz_top = bear_zt[zi]; sz_idx = zi
                sell_fresh = not bear_ze[zi]; break

        in_buy = False; bz_bot = np.nan; bz_idx = -1; buy_fresh = False
        for zi in range(len(bull_zt)):
            if h[i] >= bull_zb[zi] and l[i] <= bull_zt[zi]:
                in_buy = True; bz_bot = bull_zb[zi]; bz_idx = zi
                buy_fresh = not bull_ze[zi]; break

        # -- Touch Counters ---------------------------------------------------
        # Pine uses sz_top for bear_ref, bz_bot for bull_ref
        if in_sell:
            if np.isnan(bear_ref) or sz_top != bear_ref:
                bear_touches = 1; bear_ref = sz_top
            elif not prev_in_sell:
                bear_touches += 1
        else:
            if not np.isnan(bear_ref) and bear_ref not in bear_zt:
                bear_ref = np.nan; bear_touches = 0

        if in_buy:
            if np.isnan(bull_ref) or bz_bot != bull_ref:
                bull_touches = 1; bull_ref = bz_bot
            elif not prev_in_buy:
                bull_touches += 1
        else:
            if not np.isnan(bull_ref) and bull_ref not in bull_zb:
                bull_ref = np.nan; bull_touches = 0

        prev_in_sell = in_sell
        prev_in_buy  = in_buy

        # -- Candle Patterns --------------------------------------------------
        strong_red  = c[i] < o[i] and (o[i] - c[i]) > a14 * 0.5
        strong_grn  = c[i] > o[i] and (c[i] - o[i]) > a14 * 0.5
        bear_engulf = i > 0 and c[i] < o[i] and c[i] < l[i-1] and o[i] >= c[i-1]
        bull_engulf = i > 0 and c[i] > o[i] and c[i] > h[i-1] and o[i] <= c[i-1]

        # -- Confluence (EMA9 + EMA15 OR VWAP) -------------------------------
        e9  = ema9[i];  e15v = ema15[i];  vw = vwap[i]
        conf_sell = (e9 > c[i] and e15v > c[i]) or vw > c[i]
        conf_buy  = (e9 < c[i] and e15v < c[i]) or vw < c[i]

        # -- Filters ---------------------------------------------------------
        po = past_open(ts)
        sell_sl_d = (sz_top + BUF - c[i]) if not np.isnan(sz_top) else 0.0
        buy_sl_d  = (c[i] - bz_bot + BUF) if not np.isnan(bz_bot) else 0.0

        # -- Base Signal ------------------------------------------------------
        base_sell = (in_sell
                     and (not CFG["REQUIRE_HTF"] or htf_bear[i])
                     and bear_touches >= CFG["ZONE_TOUCHES_MIN"]
                     and (bear_engulf or strong_red)
                     and conf_sell
                     and (not CFG["S2_FRESH_ONLY"] or sell_fresh))

        base_buy  = (in_buy
                     and (not CFG["REQUIRE_HTF"] or htf_bull[i])
                     and bull_touches >= CFG["ZONE_TOUCHES_MIN"]
                     and (bull_engulf or strong_grn)
                     and conf_buy
                     and (not CFG["S2_FRESH_ONLY"] or buy_fresh))

        # -- Three Layers -----------------------------------------------------
        s_n_sell = (CFG["SHOW_NORMAL"] and base_sell
                    and in_session(ts, CFG["SESS_NORMAL"]) and po and sl_ok(sell_sl_d))
        s_n_buy  = (CFG["SHOW_NORMAL"] and base_buy
                    and in_session(ts, CFG["SESS_NORMAL"]) and po and sl_ok(buy_sl_d))

        s_5_sell = (CFG["SHOW_5M"] and base_sell
                    and in_session(ts, CFG["SESS_5M"]) and po and sl_ok(sell_sl_d))
        s_5_buy  = (CFG["SHOW_5M"] and base_buy
                    and in_session(ts, CFG["SESS_5M"]) and po and sl_ok(buy_sl_d))

        s_m_sell = (CFG["SHOW_MTF"] and base_sell
                    and in_session(ts, CFG["SESS_MTF"]) and po and sl_ok(sell_sl_d)
                    and mtf_gate_sell)
        s_m_buy  = (CFG["SHOW_MTF"] and base_buy
                    and in_session(ts, CFG["SESS_MTF"]) and po and sl_ok(buy_sl_d)
                    and mtf_gate_buy)

        # Mark zone entered (only Normal layer marks - same as Pine)
        if s_n_sell and sz_idx >= 0: bear_ze[sz_idx] = True
        if s_n_buy  and bz_idx >= 0: bull_ze[bz_idx] = True

        # Collect signals (bar index for trade sim)
        sl_sell = sz_top + BUF if not np.isnan(sz_top) else np.nan
        sl_buy  = bz_bot - BUF if not np.isnan(bz_bot) else np.nan

        for mode, fired, sl_price, direction in [
            ("Normal", s_n_sell, sl_sell, "sell"),
            ("Normal", s_n_buy,  sl_buy,  "buy"),
            ("5M",     s_5_sell, sl_sell, "sell"),
            ("5M",     s_5_buy,  sl_buy,  "buy"),
            ("MTF",    s_m_sell, sl_sell, "sell"),
            ("MTF",    s_m_buy,  sl_buy,  "buy"),
        ]:
            if fired and not np.isnan(sl_price):
                signals.append({
                    "bar_i":     i,
                    "ts":        ts,
                    "layer":     mode,
                    "direction": direction,
                    "entry":     c[i],
                    "sl":        sl_price,
                })

    return pd.DataFrame(signals), df5


# --- TRADE SIMULATION ----------------------------------------------------------

def simulate(sig_df: pd.DataFrame, df5: pd.DataFrame, mode: str) -> pd.DataFrame:
    """
    Walk bar-by-bar, enter at signal close, manage SL/TP.
    Trade management (mirrors Pine v2 trade plan):
      1R  -> trail SL to entry (BE)
      1.5R -> close 50% (T1)
      2R  -> close remaining 50% (T2)
    """
    sigs = sig_df[sig_df["layer"] == mode].copy()
    if sigs.empty:
        return pd.DataFrame()

    h = df5.high.values
    l = df5.low.values
    c = df5.close.values
    n = len(df5)

    risk_usd   = CFG["ACCOUNT_SIZE"] * CFG["RISK_PCT"] / 100.0
    trail_rr   = CFG["TRAIL_BE_RR"]
    t1_rr      = CFG["T1_RR"]
    t2_rr      = CFG["T2_RR"]

    # Index signals by bar
    sig_map = {}
    for _, row in sigs.iterrows():
        bi = int(row["bar_i"])
        if bi not in sig_map:
            sig_map[bi] = []
        sig_map[bi].append(row)

    trades = []
    in_trade   = False
    entry_i    = 0;  entry_p = 0.0
    sl         = 0.0;  init_sl = 0.0
    direction  = 0     # +1 long  -1 short
    t1_done    = False

    for i in range(n):
        if in_trade:
            r_dist = abs(entry_p - init_sl)
            if r_dist <= 0:
                in_trade = False; continue
            move = (c[i] - entry_p) * direction
            r_mul = move / r_dist

            # Trail SL to BE at 1R
            if r_mul >= trail_rr and sl != entry_p:
                sl = entry_p

            # T1: close half at 1.5R
            if not t1_done:
                t1_hit = (direction ==  1 and h[i] >= entry_p + r_dist * t1_rr) or \
                         (direction == -1 and l[i] <= entry_p - r_dist * t1_rr)
                if t1_hit:
                    t1_price = entry_p + direction * r_dist * t1_rr
                    t1_pnl   = risk_usd * t1_rr * 0.5   # half position at T1
                    # sl already trailed, but track T1 happened
                    t1_done  = True
                    # Don't close trade yet - let remaining half run to T2

            # SL hit
            sl_hit = (direction ==  1 and l[i] <= sl) or \
                     (direction == -1 and h[i] >= sl)
            # T2 hit
            t2_hit = (direction ==  1 and h[i] >= entry_p + r_dist * t2_rr) or \
                     (direction == -1 and l[i] <= entry_p - r_dist * t2_rr)

            if t2_hit:
                t2_price = entry_p + direction * r_dist * t2_rr
                if t1_done:
                    total_pnl = risk_usd * t1_rr * 0.5 + risk_usd * t2_rr * 0.5
                else:
                    total_pnl = risk_usd * t2_rr
                trades.append(_trade_row(sigs, entry_i, i, df5, direction,
                                         entry_p, init_sl, t2_price, total_pnl,
                                         "T2", t1_done))
                in_trade = False; t1_done = False
                continue

            if sl_hit:
                exit_p = sl
                if sl == entry_p:  # BE hit
                    pnl = risk_usd * t1_rr * 0.5 if t1_done else 0.0
                    reason = "BE"
                else:
                    pnl = -risk_usd + (risk_usd * t1_rr * 0.5 if t1_done else 0.0)
                    reason = "SL"
                trades.append(_trade_row(sigs, entry_i, i, df5, direction,
                                         entry_p, init_sl, exit_p, pnl,
                                         reason, t1_done))
                in_trade = False; t1_done = False
                continue

        # Enter new trade if flat
        if not in_trade and i in sig_map:
            row = sig_map[i][0]   # first signal on this bar
            entry_p   = row["entry"]
            init_sl   = row["sl"]
            direction = 1 if row["direction"] == "buy" else -1
            sl        = init_sl
            entry_i   = i
            t1_done   = False
            in_trade  = True

    return pd.DataFrame(trades)


def _trade_row(sigs, entry_i, exit_i, df5, direction,
               entry_p, init_sl, exit_p, pnl, reason, t1_done):
    return {
        "entry_time":  df5.index[entry_i],
        "exit_time":   df5.index[exit_i],
        "direction":   "LONG" if direction == 1 else "SHORT",
        "entry":       round(entry_p, 2),
        "sl":          round(init_sl, 2),
        "exit":        round(exit_p, 2),
        "pnl_usd":     round(pnl, 2),
        "outcome":     reason,          # T2 / SL / BE
        "t1_hit":      t1_done,
    }


# --- STATS ---------------------------------------------------------------------

def stats(trades_df: pd.DataFrame, label: str) -> None:
    if trades_df.empty:
        print(f"  {label:<12}: no trades"); return
    n      = len(trades_df)
    wins   = (trades_df["pnl_usd"] > 0).sum()
    losses = (trades_df["pnl_usd"] < 0).sum()
    be     = (trades_df["pnl_usd"] == 0).sum()
    wr     = wins / n * 100
    not_loss = (wins + be) / n * 100
    net    = trades_df["pnl_usd"].sum()
    gw     = trades_df[trades_df["pnl_usd"] > 0]["pnl_usd"].sum()
    gl     = abs(trades_df[trades_df["pnl_usd"] < 0]["pnl_usd"].sum())
    pf     = gw / gl if gl > 0 else float("inf")
    # Max drawdown
    eq  = np.concatenate([[CFG["ACCOUNT_SIZE"]], CFG["ACCOUNT_SIZE"] + trades_df["pnl_usd"].cumsum().values])
    peak = np.maximum.accumulate(eq)
    dd   = (peak - eq) / peak * 100
    print(f"\n  {'-'*55}")
    print(f"  {label}")
    print(f"  {'-'*55}")
    print(f"  Trades   : {n}  (WIN:{wins}  LOSS:{losses}  BE:{be})")
    print(f"  Win Rate : {wr:.1f}%   |  Not-Loss: {not_loss:.1f}%  (WIN+BE)")
    print(f"  Net P&L  : ${net:+.2f}   |  Profit Factor: {pf:.2f}")
    print(f"  Max DD   : {dd.max():.1f}%")
    print(f"  Final Eq : ${CFG['ACCOUNT_SIZE'] + net:.2f}")
    # Breakdown by outcome
    for outcome in ["T2", "BE", "SL"]:
        sub = trades_df[trades_df["outcome"] == outcome]
        if len(sub) > 0:
            print(f"  {outcome:<4}: {len(sub):>3} trades  avg P&L: ${sub['pnl_usd'].mean():+.2f}")


# --- TIMEFRAME COMPARISON RUNNER -----------------------------------------------

TF_FILES = {
    "5M":  r"D:\Trade\tvHistoryData\gold_24m.csv",
    "15M": r"D:\Trade\tvHistoryData\gold_24m_15m.csv",
    "30M": r"D:\Trade\tvHistoryData\OANDA_XAUUSD, 30.csv",
}
FILE_15M_GATE = r"D:\Trade\tvHistoryData\gold_24m_15m.csv"   # MTF gate always 15M
FILE_1H       = r"D:\Trade\tvHistoryData\gold_24m_1h.csv"


def run_tf(tf_label: str, df_tf: pd.DataFrame, df15: pd.DataFrame, df1h: pd.DataFrame) -> dict:
    """Run Normal mode on any timeframe. Returns summary dict."""
    print(f"\n  [{tf_label}] {len(df_tf):,} bars  "
          f"{df_tf.index[0].date()} -> {df_tf.index[-1].date()}")

    # For non-5M TFs: disable 5M-lock + MTF (those need 5M chart)
    orig_5m  = CFG["SHOW_5M"];  orig_mtf = CFG["SHOW_MTF"]
    if tf_label != "5M":
        CFG["SHOW_5M"] = False; CFG["SHOW_MTF"] = False

    sig_df, df_prep = detect_signals(df_tf, df15, df1h)
    n_normal = len(sig_df[sig_df["layer"] == "Normal"])
    n_mtf    = len(sig_df[sig_df["layer"] == "MTF"])
    print(f"  [{tf_label}] signals: Normal={n_normal}  MTF={n_mtf}")

    trades = simulate(sig_df, df_prep, "Normal")

    CFG["SHOW_5M"] = orig_5m; CFG["SHOW_MTF"] = orig_mtf   # restore

    if trades.empty:
        print(f"  [{tf_label}] no trades")
        return {"tf": tf_label, "trades": 0}

    n      = len(trades)
    wins   = (trades["pnl_usd"] > 0).sum()
    losses = (trades["pnl_usd"] < 0).sum()
    be     = (trades["pnl_usd"] == 0).sum()
    net    = trades["pnl_usd"].sum()
    gw     = trades[trades["pnl_usd"] > 0]["pnl_usd"].sum()
    gl     = abs(trades[trades["pnl_usd"] < 0]["pnl_usd"].sum())
    pf     = round(gw / gl, 2) if gl > 0 else 999.0
    eq     = np.concatenate([[CFG["ACCOUNT_SIZE"]],
                              CFG["ACCOUNT_SIZE"] + trades["pnl_usd"].cumsum().values])
    peak   = np.maximum.accumulate(eq)
    max_dd = round(((peak - eq) / peak * 100).max(), 1)
    not_loss_pct = round((wins + be) / n * 100, 1)
    win_pct      = round(wins / n * 100, 1)

    trades["tf"] = tf_label
    trades.to_csv(fr"D:\Trade\s2_v2_trades_{tf_label}.csv", index=False)

    return {"tf": tf_label, "trades": n, "wins": int(wins),
            "losses": int(losses), "be": int(be),
            "win_pct": win_pct, "not_loss_pct": not_loss_pct,
            "net_pnl": round(net, 2), "pf": pf, "max_dd_pct": max_dd}


# --- MAIN ----------------------------------------------------------------------

def main():
    print("\n" + "=" * 57)
    print("  Manku S2 - Zone Pyramid v2 - TF Comparison")
    print("=" * 57)

    df1h = load_csv(FILE_1H)
    df15_gate = load_csv(FILE_15M_GATE)

    results = []
    all_trades_dfs = []

    for tf_label, path in TF_FILES.items():
        if not Path(path).exists():
            print(f"  SKIP {tf_label}: file missing ({path})")
            continue
        df_tf = load_csv(path)

        # Clip all 3 to common range
        start = max(df_tf.index[0], df15_gate.index[0], df1h.index[0])
        end   = min(df_tf.index[-1], df15_gate.index[-1], df1h.index[-1])
        df_c  = df_tf[start:end]
        d15_c = df15_gate[start:end]
        d1h_c = df1h[start:end]

        row = run_tf(tf_label, df_c, d15_c, d1h_c)
        results.append(row)

    # ---- Summary table ----
    print("\n\n" + "=" * 75)
    print("  TIMEFRAME COMPARISON  (Normal mode - NY only session)")
    print("=" * 75)
    print(f"  {'TF':<6} {'Trades':>7} {'WIN':>5} {'LOSS':>5} {'BE':>4} "
          f"{'Win%':>6} {'NotLoss%':>9} {'Net$':>8} {'PF':>5} {'MaxDD%':>7}")
    print("  " + "-" * 63)
    for r in results:
        if r.get("trades", 0) == 0:
            print(f"  {r['tf']:<6}  no trades")
            continue
        flag = " <-- BEST" if r["not_loss_pct"] == max(x.get("not_loss_pct",0) for x in results) else ""
        print(f"  {r['tf']:<6} {r['trades']:>7} {r['wins']:>5} {r['losses']:>5} {r['be']:>4} "
              f"  {r['win_pct']:>5.1f}%   {r['not_loss_pct']:>6.1f}%  "
              f"${r['net_pnl']:>7.0f}  {r['pf']:>4.2f}   {r['max_dd_pct']:>5.1f}%{flag}")
    print("=" * 75)
    print("  Trade CSVs: D:\\Trade\\s2_v2_trades_5M.csv / _15M.csv / _30M.csv")
    print("\n  Done.\n")


if __name__ == "__main__":
    main()
