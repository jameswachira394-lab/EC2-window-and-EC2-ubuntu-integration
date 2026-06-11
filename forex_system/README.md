# Institutional Forex Trading System

A fully automated, multi-step trading engine built in Python with a MetaTrader 5 connector.
Implements all 13 steps from the Institutional Forex Trading System specification.

---

## Project Structure

```
forex_system/
├── main.py                    ← Bot entry point
├── backtest.py                ← Historical backtester
├── requirements.txt
├── config/
│   ├── settings.py            ← All tunable parameters
│   └── news_events.csv        ← High-impact news (you populate this)
├── connectors/
│   └── mt5_connector.py       ← MT5 connection, data & order execution
├── core/
│   ├── indicators.py          ← EMA, ATR, swings, structure, sweeps
│   ├── signal_engine.py       ← Steps 1–13 signal logic + confidence score
│   └── risk_manager.py        ← Daily/weekly loss limits, consecutive losses
├── utils/
│   ├── signal_logger.py       ← CSV signal log
│   └── news_filter.py         ← News blackout loader
├── logs/                      ← Auto-created: bot log, risk state JSON
└── signals/                   ← Auto-created: signal_log.csv
```

---

## Step-by-Step Implementation Map

| Step | Description | File |
|------|-------------|------|
| 1 | Higher Timeframe Bias (EMA20/50) | `core/indicators.py` → `classify_trend()` |
| 2 | Market Structure (HH/HL, LH/LL) | `core/indicators.py` → `classify_market_structure()` |
| 3 | Liquidity Sweep Detection | `core/indicators.py` → `detect_liquidity_sweep()` |
| 4 | Volatility Compression (ATR) | `core/indicators.py` → `is_atr_compressed()` |
| 5 | Volume Ratio Confirmation | `core/indicators.py` → `add_volume_ratio()` |
| 6 | Breakout Confirmation | `core/indicators.py` → `detect_breakout()` |
| 7 | Order Flow (optional stub) | `core/signal_engine.py` Step 7 comment |
| 8 | Entry / SL / TP Logic | `core/signal_engine.py` → `SignalEngine.evaluate()` |
| 9 | Risk Management (1%/3%/6%) | `core/risk_manager.py` → `RiskManager` |
| 10 | Session Filter | `core/signal_engine.py` → `is_valid_session()` |
| 11 | News Blackout Filter | `utils/news_filter.py` + `signal_engine.py` |
| 12 | Confidence Score (0–100) | `core/signal_engine.py` → `compute_confidence()` |
| 13 | Output Format | `core/signal_engine.py` → `format_signal()` |

---

## Setup

### 1. Prerequisites

- **Windows** PC or VM with **MetaTrader 5** installed and logged into your broker account
- **Python 3.10+**

### 2. Install dependencies

```bash
pip install -r requirements.txt
pip install MetaTrader5          # Windows only
```

### 3. Configure your MT5 credentials

Edit `config/settings.py`:

```python
MT5_CONFIG = {
    "login":    12345678,          # your account number
    "password": "YOUR_PASSWORD",
    "server":   "YourBroker-Live",
}
```

### 4. (Optional) Add news events

Populate `config/news_events.csv` with columns:
```
datetime_utc,currency,impact,event
2024-12-06 13:30:00,USD,HIGH,Non-Farm Payrolls
2024-12-11 13:30:00,USD,HIGH,CPI m/m
```
Only rows with `impact=HIGH` trigger the 30-minute blackout.

---

## Running the Bot

```bash
# Live scanning (all symbols, every 60s)
python main.py

# Dry run — generate signals but NEVER place orders
python main.py --dry-run

# Single scan, then exit
python main.py --scan-once

# Single symbol only
python main.py --symbol EURUSD

# Custom scan interval (seconds)
python main.py --interval 300

# Dry run, one symbol, one scan
python main.py --dry-run --symbol GBPUSD --scan-once
```

---

## Backtesting

```bash
# Runs on synthetic data in simulation mode (no MT5 needed)
python backtest.py --symbol EURUSD --balance 10000

# Adjust step (evaluate every N M15 candles — larger = faster)
python backtest.py --step 1    # every candle
python backtest.py --step 4    # every hour (default)
```

Backtest results saved to `logs/backtest_results.csv`.

---

## Signal Output Format

```
====================================================
PAIR:             EURUSD
DIRECTION:        LONG
CONFIDENCE:       92/100  [Institutional Grade]
TIMESTAMP:        2024-12-06 14:30:00 UTC
----------------------------------------------------
TREND:
  H4              BULLISH
  H1              BULLISH
  M15             BULLISH
MARKET STRUCTURE: BULLISH
LIQUIDITY EVENT:  Previous Low Swept (rej=62.50%)
VOLUME RATIO:     1.8x Average
ATR:              0.000820  (EXPANDING / COMPRESSED)
----------------------------------------------------
ENTRY:            1.14520
STOP LOSS:        1.14390
TAKE PROFIT 1:    1.14780
TAKE PROFIT 2:    1.14950
TAKE PROFIT 3:    1.15200
RISK REWARD:      1:2.7
LOT SIZE:         0.08
----------------------------------------------------
DECISION:         EXECUTE TRADE
====================================================
```

---

## Key Parameters (config/settings.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RISK_PER_TRADE_PCT` | 1.0% | Risk per trade |
| `MAX_DAILY_LOSS_PCT` | 3.0% | Daily loss limit |
| `MAX_WEEKLY_LOSS_PCT` | 6.0% | Weekly loss limit |
| `MAX_CONSEC_LOSSES` | 3 | Consecutive losses before pause |
| `VOLUME_RATIO_MIN` | 1.5 | Minimum tick volume ratio |
| `MIN_RR` | 2.0 | Minimum risk-reward |
| `MIN_CONFIDENCE` | 70 | Minimum confidence score |
| `NEWS_BLACKOUT_MINUTES` | 30 | Minutes before/after news |
| `EMA_FAST / EMA_SLOW` | 20 / 50 | Trend EMA periods |
| `ATR_PERIOD` | 14 | ATR period |

---

## Simulation Mode

If `MetaTrader5` is not installed, the system automatically runs in **simulation mode**:
- Generates synthetic OHLCV data
- All signal logic runs normally
- Orders are logged but not sent

This lets you test and develop on any OS.

---

## Disclaimers

- **This is an automated trading system. Use at your own risk.**
- Always test thoroughly in a **demo account** before going live.
- Past performance in backtests does not guarantee future results.
- The author is not responsible for financial losses.
