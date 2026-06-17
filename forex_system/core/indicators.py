"""
Technical Indicators
────────────────────
Pure-pandas/numpy implementations — no TA-Lib dependency required.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from config.settings import EMA_FAST, EMA_SLOW, ATR_PERIOD, ATR_MA_PERIOD, VOLUME_MA_PERIOD, SWING_LOOKBACK


# ── EMA ────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def add_emas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df[f"ema{EMA_FAST}"] = ema(df["close"], EMA_FAST)
    df[f"ema{EMA_SLOW}"] = ema(df["close"], EMA_SLOW)
    return df


# ── ATR ────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def add_atr(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["atr"]     = atr(df, ATR_PERIOD)
    df["atr_ma"]  = df["atr"].rolling(ATR_MA_PERIOD).mean()
    return df


# ── Volume ─────────────────────────────────────────────────────────

def add_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vol_ma"]    = df["volume"].rolling(VOLUME_MA_PERIOD).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]
    return df


# ── Swing Highs / Lows ─────────────────────────────────────────────

def swing_highs(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    """Returns True where price is a swing high (local max over ±lookback bars)."""
    highs = df["high"]
    left  = highs.rolling(lookback + 1).max()
    right = highs[::-1].rolling(lookback + 1).max()[::-1]
    return (highs == left) & (highs == right)


def swing_lows(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.Series:
    """Returns True where price is a swing low (local min over ±lookback bars)."""
    lows  = df["low"]
    left  = lows.rolling(lookback + 1).min()
    right = lows[::-1].rolling(lookback + 1).min()[::-1]
    return (lows == left) & (lows == right)


def get_swing_points(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> pd.DataFrame:
    """Returns DataFrame with swing_high and swing_low columns."""
    df = df.copy()
    df["swing_high"] = swing_highs(df, lookback)
    df["swing_low"]  = swing_lows(df, lookback)
    return df


# ── Market Structure ───────────────────────────────────────────────

def classify_market_structure(df: pd.DataFrame) -> str:
    """
    Identify HH/HL (bullish) or LH/LL (bearish) from last N swing points.
    Returns: 'bullish' | 'bearish' | 'neutral'
    """
    sh_idx = df.index[df["swing_high"]].tolist()
    sl_idx = df.index[df["swing_low"]].tolist()

    if len(sh_idx) < 2 or len(sl_idx) < 2:
        return "neutral"

    # Last two swing highs & lows
    last_sh  = df.loc[sh_idx[-1], "high"]
    prev_sh  = df.loc[sh_idx[-2], "high"]
    last_sl  = df.loc[sl_idx[-1], "low"]
    prev_sl  = df.loc[sl_idx[-2], "low"]

    hh = last_sh > prev_sh
    hl = last_sl > prev_sl
    lh = last_sh < prev_sh
    ll = last_sl < prev_sl

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"


# ── Trend (EMA-based) ──────────────────────────────────────────────

def classify_trend(df: pd.DataFrame) -> str:
    """
    Bullish: EMA20 > EMA50 AND close > EMA50
    Bearish: EMA20 < EMA50 AND close < EMA50
    """
    last = df.iloc[-1]
    f, s, c = last[f"ema{EMA_FAST}"], last[f"ema{EMA_SLOW}"], last["close"]
    if f > s and c > s:
        return "bullish"
    if f < s and c < s:
        return "bearish"
    return "neutral"


# ── Liquidity Sweep Detection ──────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame, swing_lookback: int = SWING_LOOKBACK) -> dict:
    """
    Look at the last few candles for a wick-rejection after sweeping a swing point.
    Returns dict with keys: detected (bool), direction, sweep_price, rejection_strength, candle_size.
    """
    result = {"detected": False, "direction": None,
              "sweep_price": None, "rejection_strength": 0.0, "candle_size": 0.0}

    if len(df) < swing_lookback + 5:
        return result

    recent   = df.iloc[-(swing_lookback + 5):]
    last     = df.iloc[-1]
    prev_bar = df.iloc[-2]

    sh_prices = recent["high"][recent["swing_high"]].values
    sl_prices = recent["low"][recent["swing_low"]].values

    candle_size = last["high"] - last["low"]
    if candle_size == 0:
        return result

    # ── Bearish sweep (price swept above swing high then closed back below) ──
    if len(sh_prices) > 0:
        prev_swing_high = sh_prices[-1]
        if (last["high"] > prev_swing_high and
                last["close"] < prev_swing_high):
            upper_wick = last["high"] - max(last["open"], last["close"])
            rej_strength = upper_wick / candle_size
            result.update({
                "detected":           True,
                "direction":          "bearish",
                "sweep_price":        prev_swing_high,
                "rejection_strength": round(rej_strength, 3),
                "candle_size":        round(candle_size, 6),
            })
            return result

    # ── Bullish sweep (price swept below swing low then closed back above) ──
    if len(sl_prices) > 0:
        prev_swing_low = sl_prices[-1]
        if (last["low"] < prev_swing_low and
                last["close"] > prev_swing_low):
            lower_wick = min(last["open"], last["close"]) - last["low"]
            rej_strength = lower_wick / candle_size
            result.update({
                "detected":           True,
                "direction":          "bullish",
                "sweep_price":        prev_swing_low,
                "rejection_strength": round(rej_strength, 3),
                "candle_size":        round(candle_size, 6),
            })
            return result

    return result


# ── Breakout Confirmation ──────────────────────────────────────────

def detect_breakout(df: pd.DataFrame) -> dict:
    """
    Bullish:  close above consolidation range AND above prev swing high.
    Bearish:  close below consolidation range AND below prev swing low.
    Uses last 10-bar range as consolidation reference.
    """
    result = {"confirmed": False, "direction": None}
    if len(df) < 15:
        return result

    consolidation = df.iloc[-11:-1]
    range_high    = consolidation["high"].max()
    range_low     = consolidation["low"].min()
    last_close    = df.iloc[-1]["close"]

    sh_prices = df["high"][df["swing_high"]].values
    sl_prices = df["low"][df["swing_low"]].values

    if last_close > range_high and len(sh_prices) >= 2:
        if last_close > sh_prices[-2]:
            result = {"confirmed": True, "direction": "bullish"}
    elif last_close < range_low and len(sl_prices) >= 2:
        if last_close < sl_prices[-2]:
            result = {"confirmed": True, "direction": "bearish"}

    return result


# ── ATR Expansion ─────────────────────────────────────────────────

def is_atr_expanding(df: pd.DataFrame) -> bool:
    """True if current ATR > previous ATR (expansion beginning)."""
    if len(df) < 3:
        return False
    return df["atr"].iloc[-1] > df["atr"].iloc[-2]


def is_atr_compressed(df: pd.DataFrame) -> bool:
    """True if current ATR < its 20-period average (compression / energy buildup)."""
    last = df.iloc[-1]
    if pd.isna(last["atr_ma"]):
        return False
    return last["atr"] < last["atr_ma"]


# ── Three Strikes Extras ───────────────────────────────────────────

def add_emas(df: pd.DataFrame, fast: int = EMA_FAST, slow: int = EMA_SLOW) -> pd.DataFrame:
    """Adds 'ema_fast' and 'ema_slow' columns using pandas ewm(span=N, adjust=False)."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    return df


def classify_trend_ema(df: pd.DataFrame) -> str:
    """
    Returns 'bullish' if close > ema_fast > ema_slow,
    'bearish' if close < ema_fast < ema_slow,
    else 'neutral'.
    """
    if len(df) == 0:
        return "neutral"
    last = df.iloc[-1]
    c = last["close"]
    f = last["ema_fast"]
    s = last["ema_slow"]
    if c > f and f > s:
        return "bullish"
    elif c < f and f < s:
        return "bearish"
    return "neutral"
