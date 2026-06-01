"""
Gold OHLCV Chart Viewer — TradingView style (Plotly)
Edit only the CONFIG block below — nothing else.
"""

# ─── CONFIG ────────────────────────────────────────────────
CSV_FILE  = "gold_6m.csv"   # source data file
TIMEFRAME = "5m"            # resample to: 1m,5m,15m,30m,1H,2H,4H,1D,1W
                            # (must be >= source data timeframe)
LAST_DAYS = 30              # show last N days on chart (0 = show all)
THEME     = "dark"          # "dark" or "light"
SHOW_VOL  = True            # show volume bars below chart
# ───────────────────────────────────────────────────────────

import subprocess, sys
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "plotly"])
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

import pandas as pd

RESAMPLE_MAP = {
    "1m": "1min",  "5m": "5min",  "15m": "15min", "30m": "30min",
    "1H": "1h",    "2H": "2h",    "4H": "4h",
    "1D": "1D",    "1W": "1W",
}

if TIMEFRAME not in RESAMPLE_MAP:
    print(f"Invalid TIMEFRAME. Choose from: {list(RESAMPLE_MAP.keys())}")
    sys.exit(1)

# ── Load ──────────────────────────────────────────────────
df = pd.read_csv(CSV_FILE, index_col="Datetime", parse_dates=True)
df.index = pd.to_datetime(df.index, utc=True)
df.sort_index(inplace=True)

# ── Resample ──────────────────────────────────────────────
rule = RESAMPLE_MAP[TIMEFRAME]
df = df.resample(rule).agg({
    "Open":  "first",
    "High":  "max",
    "Low":   "min",
    "Close": "last",
    "Volume":"sum",
}).dropna()

# ── Filter last N days ────────────────────────────────────
if LAST_DAYS > 0:
    cutoff = df.index[-1] - pd.Timedelta(days=LAST_DAYS)
    df = df[df.index >= cutoff]

print(f"Charting {len(df)} bars | TF: {TIMEFRAME} | {df.index[0].date()} to {df.index[-1].date()}")

# ── Colors ────────────────────────────────────────────────
if THEME == "dark":
    bg        = "#131722"
    grid      = "#1e222d"
    text      = "#d1d4dc"
    up_color  = "#26a69a"
    dn_color  = "#ef5350"
else:
    bg        = "#ffffff"
    grid      = "#e0e0e0"
    text      = "#131722"
    up_color  = "#26a69a"
    dn_color  = "#ef5350"

# ── Build chart ───────────────────────────────────────────
rows   = 2 if SHOW_VOL else 1
heights= [0.75, 0.25] if SHOW_VOL else [1.0]

fig = make_subplots(
    rows=rows, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.02,
    row_heights=heights,
)

# Candlestick
fig.add_trace(
    go.Candlestick(
        x=df.index,
        open=df["Open"], high=df["High"],
        low=df["Low"],   close=df["Close"],
        increasing_line_color=up_color,
        decreasing_line_color=dn_color,
        increasing_fillcolor=up_color,
        decreasing_fillcolor=dn_color,
        name="XAUUSD",
        line=dict(width=1),
    ),
    row=1, col=1,
)

# Volume
if SHOW_VOL:
    vol_colors = [
        up_color if c >= o else dn_color
        for o, c in zip(df["Open"], df["Close"])
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            marker_color=vol_colors,
            marker_line_width=0,
            opacity=0.7,
            name="Volume",
        ),
        row=2, col=1,
    )

# ── Layout ────────────────────────────────────────────────
axis_style = dict(
    gridcolor=grid,
    gridwidth=1,
    color=text,
    showgrid=True,
    zeroline=False,
)

fig.update_layout(
    title=dict(
        text=f"XAUUSD | {TIMEFRAME}",
        font=dict(color=text, size=16),
    ),
    paper_bgcolor=bg,
    plot_bgcolor=bg,
    font=dict(color=text),
    xaxis_rangeslider_visible=False,
    hovermode="x unified",
    margin=dict(l=60, r=30, t=50, b=30),
    legend=dict(
        bgcolor=bg,
        bordercolor=grid,
        font=dict(color=text),
    ),
)

fig.update_xaxes(
    **axis_style,
    rangebreaks=[
        dict(bounds=["sat", "mon"]),  # remove weekends
    ],
)
fig.update_yaxes(**axis_style)

import os, webbrowser

html_file = "gold_chart.html"
fig.write_html(html_file, include_plotlyjs="cdn")

# Open in Chrome
chrome_paths = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
chrome = next((p for p in chrome_paths if os.path.exists(p)), None)

if chrome:
    webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(chrome))
    webbrowser.get("chrome").open(os.path.abspath(html_file))
    print(f"Chart opened in Chrome -> {html_file}")
else:
    webbrowser.open(os.path.abspath(html_file))
    print(f"Chrome not found, opened in default browser -> {html_file}")
