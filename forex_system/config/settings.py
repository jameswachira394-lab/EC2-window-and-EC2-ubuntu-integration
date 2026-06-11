"""
Institutional Forex Trading System — Configuration
"""

# ─── MT5 Connection ────────────────────────────────────────────────
MT5_CONFIG = {
    "login":    10401216,          # ← your MT5 account number
    "password": "erBo0B{",   # ← your MT5 password
    "server":   "FBS-Demo", # ← your broker's server name
    "timeout":  10000,
    "portable": False,
}

# ─── Tradeable Pairs ───────────────────────────────────────────────
SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY",
    "USDCHF", "AUDUSD", "NZDUSD",
    "USDCAD", "GBPJPY", "EURJPY",
]

# ─── Timeframes (MT5 constants mapped by string) ───────────────────
TIMEFRAMES = {
    "M15": "M15",
    "H1":  "H1",
    "H4":  "H4",
}

# ─── EMA Settings ─────────────────────────────────────────────────
EMA_FAST = 20
EMA_SLOW = 50

# ─── ATR ──────────────────────────────────────────────────────────
ATR_PERIOD          = 14
ATR_MA_PERIOD       = 20   # period for ATR average (compression check)

# ─── Volume ───────────────────────────────────────────────────────
VOLUME_MA_PERIOD    = 20
VOLUME_RATIO_MIN    = 1.5

# ─── Risk Management ──────────────────────────────────────────────
RISK_PER_TRADE_PCT  = 1.0   # % of account balance
MAX_DAILY_LOSS_PCT  = 3.0
MAX_WEEKLY_LOSS_PCT = 6.0
MAX_CONSEC_LOSSES   = 3

# ─── Take Profit Multipliers ──────────────────────────────────────
TP1_ATR_MULT = 2.0
TP2_ATR_MULT = 3.0
MIN_RR       = 2.0   # minimum risk-reward ratio

# ─── Candle history to fetch per TF ───────────────────────────────
CANDLES_HISTORY = 300

# ─── Session Windows (UTC) ────────────────────────────────────────
SESSIONS = {
    "London":          (7,  16),
    "NewYork":         (12, 21),
    "LondonNYOverlap": (12, 16),
}
ALLOWED_SESSION_HOURS_UTC = list(range(7, 21))   # 07:00–21:00 UTC

# ─── News blackout (minutes) ──────────────────────────────────────
NEWS_BLACKOUT_MINUTES = 30

# ─── Confidence threshold ─────────────────────────────────────────
MIN_CONFIDENCE = 70

# ─── Swing detection lookback ─────────────────────────────────────
SWING_LOOKBACK = 10   # bars each side for swing high/low detection

# ─── Logging ──────────────────────────────────────────────────────
LOG_DIR        = "logs"
SIGNAL_LOG_CSV = "signals/signal_log.csv"
