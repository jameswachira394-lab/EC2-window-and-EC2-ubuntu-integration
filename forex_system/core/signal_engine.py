"""
Signal Engine
─────────────
Implements Steps 1–13 of the Institutional Forex Trading System.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from config.settings import (
    EMA_FAST, EMA_SLOW,
    VOLUME_RATIO_MIN,
    MIN_RR, TP1_ATR_MULT, TP2_ATR_MULT,
    MIN_CONFIDENCE,
    ALLOWED_SESSION_HOURS_UTC,
    NEWS_BLACKOUT_MINUTES,
)
from core.indicators import (
    add_emas, add_atr, add_volume_ratio, get_swing_points,
    classify_trend, classify_market_structure,
    detect_liquidity_sweep, detect_breakout,
    is_atr_expanding, is_atr_compressed,
)

logger = logging.getLogger(__name__)


# ─── Signal Dataclass ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    pair:          str
    direction:     str       # LONG | SHORT | NO_TRADE
    confidence:    int
    entry:         float
    stop_loss:     float
    tp1:           float
    tp2:           float
    tp3:           float
    risk_reward:   float
    lot_size:      float
    decision:      str       # EXECUTE | REVIEW | NO_TRADE

    # Diagnostics
    trend_h4:      str = "neutral"
    trend_h1:      str = "neutral"
    trend_m15:     str = "neutral"
    structure:     str = "neutral"
    sweep:         dict = field(default_factory=dict)
    volume_ratio:  float = 0.0
    atr_current:   float = 0.0
    atr_expanding: bool = False
    atr_compressed:bool = False
    breakout:      dict = field(default_factory=dict)
    rejection_log: list = field(default_factory=list)
    timestamp:     str  = ""


# ─── Session / News Filters ───────────────────────────────────────

def is_valid_session(utc_now: datetime) -> bool:
    return utc_now.hour in ALLOWED_SESSION_HOURS_UTC


def is_news_blackout(utc_now: datetime, news_times_utc: list[datetime]) -> bool:
    from datetime import timedelta
    window = NEWS_BLACKOUT_MINUTES * 60  # seconds
    for nt in news_times_utc:
        delta = abs((utc_now - nt).total_seconds())
        if delta <= window:
            return True
    return False


# ─── Lot Size Calculator ──────────────────────────────────────────

def calculate_lot_size(
    balance:       float,
    risk_pct:      float,
    entry:         float,
    stop_loss:     float,
    point_value:   float = 10.0,   # USD per pip per standard lot (default EURUSD)
    pip_size:      float = 0.0001,
) -> float:
    """
    Risk-based lot sizing.
    risk_amount  = balance × risk_pct / 100
    pips_at_risk = |entry − stop_loss| / pip_size
    lot_size     = risk_amount / (pips_at_risk × point_value)
    """
    risk_amount  = balance * risk_pct / 100.0
    pips_at_risk = abs(entry - stop_loss) / pip_size
    if pips_at_risk <= 0:
        return 0.01
    lot = risk_amount / (pips_at_risk * point_value)
    return round(max(0.01, lot), 2)


# ─── Confidence Scorer ────────────────────────────────────────────

def compute_confidence(checks: dict) -> int:
    """
    Weighted scoring across all signal components.
    Max = 100.
    """
    weights = {
        "h4_trend":          20,
        "h1_trend":          15,
        "structure":         15,
        "sweep":             15,
        "volume":            12,
        "breakout":          10,
        "atr_compressed":     5,
        "atr_expanding":      5,
        "rr_min":             3,
    }
    score = sum(weights[k] for k, v in checks.items() if v)
    return min(100, score)


# ─── Core Signal Builder ──────────────────────────────────────────

class SignalEngine:

    def __init__(self, account_balance: float = 10_000.0, risk_pct: float = 1.0):
        self.balance  = account_balance
        self.risk_pct = risk_pct

    # ── Public API ─────────────────────────────────────────────────

    def evaluate(
        self,
        symbol:    str,
        df_h4:     pd.DataFrame,
        df_h1:     pd.DataFrame,
        df_m15:    pd.DataFrame,
        news_times: list = None,
        utc_now:    datetime = None,
    ) -> TradeSignal:
        """
        Run all 13 steps and return a TradeSignal.
        """
        if utc_now is None:
            utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        if news_times is None:
            news_times = []

        rejection_log = []

        # ── Prepare data ─────────────────────────────────────────
        df_h4  = self._prepare(df_h4)
        df_h1  = self._prepare(df_h1)
        df_m15 = self._prepare(df_m15)

        # ── STEP 10: Session Filter ───────────────────────────────
        if not is_valid_session(utc_now):
            rejection_log.append("Outside allowed sessions (London/NY)")
            return self._no_trade(symbol, rejection_log, utc_now)

        # ── STEP 11: News Filter ──────────────────────────────────
        if is_news_blackout(utc_now, news_times):
            rejection_log.append("News blackout active")
            return self._no_trade(symbol, rejection_log, utc_now)

        # ── STEP 1: Higher Timeframe Bias ─────────────────────────
        trend_h4  = classify_trend(df_h4)
        trend_h1  = classify_trend(df_h1)
        trend_m15 = classify_trend(df_m15)

        if trend_h4 == "neutral" or trend_h1 == "neutral":
            rejection_log.append(f"Trend conflict H4={trend_h4} H1={trend_h1}")
            return self._no_trade(symbol, rejection_log, utc_now,
                                  trend_h4=trend_h4, trend_h1=trend_h1)

        if trend_h4 != trend_h1:
            rejection_log.append(f"H4/H1 trend mismatch: H4={trend_h4} H1={trend_h1}")
            return self._no_trade(symbol, rejection_log, utc_now,
                                  trend_h4=trend_h4, trend_h1=trend_h1)

        bias = trend_h4  # bullish | bearish

        # ── STEP 2: Market Structure ──────────────────────────────
        structure = classify_market_structure(df_h1)
        structure_ok = (structure == bias)
        if not structure_ok:
            rejection_log.append(f"Structure ({structure}) conflicts with bias ({bias})")
            return self._no_trade(symbol, rejection_log, utc_now,
                                  trend_h4=trend_h4, trend_h1=trend_h1, structure=structure)

        # ── STEP 3: Liquidity Sweep ───────────────────────────────
        sweep = detect_liquidity_sweep(df_m15)
        sweep_ok = (sweep["detected"] and
                    sweep["direction"] == bias and
                    sweep["rejection_strength"] > 0.3)
        if not sweep_ok:
            rejection_log.append("No valid liquidity sweep on M15")

        # ── STEP 4: Volatility Compression ───────────────────────
        atr_compressed = is_atr_compressed(df_m15)
        if not atr_compressed:
            rejection_log.append("ATR not compressed (no energy buildup)")

        # ── STEP 5: Volume Confirmation ───────────────────────────
        last_m15   = df_m15.iloc[-1]
        vol_ratio  = last_m15.get("vol_ratio", 0.0)
        volume_ok  = vol_ratio >= VOLUME_RATIO_MIN
        if not volume_ok:
            rejection_log.append(f"Volume ratio {vol_ratio:.2f} < {VOLUME_RATIO_MIN}")

        # ── STEP 6: Breakout Confirmation ─────────────────────────
        breakout   = detect_breakout(df_m15)
        breakout_ok = (breakout["confirmed"] and breakout["direction"] == bias)
        if not breakout_ok:
            rejection_log.append("Breakout not confirmed on M15")

        # ── ATR State ─────────────────────────────────────────────
        atr_expanding = is_atr_expanding(df_m15)
        if not atr_expanding:
            rejection_log.append("ATR not yet expanding")

        # ── STEP 12: Confidence Score ─────────────────────────────
        checks = {
            "h4_trend":       trend_h4 == bias,
            "h1_trend":       trend_h1 == bias,
            "structure":      structure_ok,
            "sweep":          sweep_ok,
            "volume":         volume_ok,
            "breakout":       breakout_ok,
            "atr_compressed": atr_compressed,
            "atr_expanding":  atr_expanding,
            "rr_min":         True,  # resolved below
        }
        confidence = compute_confidence(checks)

        if confidence < MIN_CONFIDENCE:
            rejection_log.append(f"Confidence {confidence} below minimum {MIN_CONFIDENCE}")
            return self._no_trade(symbol, rejection_log, utc_now,
                                  trend_h4=trend_h4, trend_h1=trend_h1, structure=structure)

        # ── STEP 8: Entry / SL / TP ───────────────────────────────
        atr_val   = last_m15["atr"]
        tick      = df_m15.iloc[-1]
        direction = "LONG" if bias == "bullish" else "SHORT"

        if direction == "LONG":
            entry     = tick["close"]
            stop_loss = sweep["sweep_price"] - atr_val * 0.2 if sweep_ok else entry - atr_val * 1.5
            tp1       = entry + atr_val * TP1_ATR_MULT
            tp2       = entry + atr_val * TP2_ATR_MULT
            # TP3: find nearest resistance (previous swing high)
            sh_vals   = df_h1["high"][df_h1["swing_high"]].values
            tp3       = sh_vals[-1] if len(sh_vals) > 0 else tp2 * 1.005
        else:  # SHORT
            entry     = tick["close"]
            stop_loss = sweep["sweep_price"] + atr_val * 0.2 if sweep_ok else entry + atr_val * 1.5
            tp1       = entry - atr_val * TP1_ATR_MULT
            tp2       = entry - atr_val * TP2_ATR_MULT
            sl_vals   = df_h1["low"][df_h1["swing_low"]].values
            tp3       = sl_vals[-1] if len(sl_vals) > 0 else tp2 * 0.995

        risk   = abs(entry - stop_loss)
        reward = abs(tp1   - entry)
        rr     = round(reward / risk, 2) if risk > 0 else 0.0
        checks["rr_min"] = rr >= MIN_RR
        confidence = compute_confidence(checks)

        if rr < MIN_RR:
            rejection_log.append(f"R:R {rr:.2f} below minimum 1:{MIN_RR}")
            return self._no_trade(symbol, rejection_log, utc_now,
                                  trend_h4=trend_h4, trend_h1=trend_h1, structure=structure)

        lot_size = calculate_lot_size(self.balance, self.risk_pct, entry, stop_loss)

        # ── STEP 13: Decision ─────────────────────────────────────
        if confidence >= 90:
            decision = "EXECUTE TRADE"
        elif confidence >= 80:
            decision = "EXECUTE TRADE"
        elif confidence >= 70:
            decision = "REVIEW MANUALLY"
        else:
            decision = "NO TRADE"

        signal = TradeSignal(
            pair          = symbol,
            direction     = direction,
            confidence    = confidence,
            entry         = round(entry,     5),
            stop_loss     = round(stop_loss, 5),
            tp1           = round(tp1,       5),
            tp2           = round(tp2,       5),
            tp3           = round(tp3,       5),
            risk_reward   = rr,
            lot_size      = lot_size,
            decision      = decision,
            trend_h4      = trend_h4,
            trend_h1      = trend_h1,
            trend_m15     = trend_m15,
            structure     = structure,
            sweep         = sweep,
            volume_ratio  = round(float(vol_ratio), 2),
            atr_current   = round(float(atr_val), 6),
            atr_expanding = atr_expanding,
            atr_compressed= atr_compressed,
            breakout      = breakout,
            rejection_log = rejection_log,
            timestamp     = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

        logger.info("Signal generated: %s %s conf=%d decision=%s",
                    symbol, direction, confidence, decision)
        return signal

    # ── Helpers ────────────────────────────────────────────────────

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_emas(df)
        df = add_atr(df)
        df = add_volume_ratio(df)
        df = get_swing_points(df)
        return df

    def _no_trade(
        self, symbol: str, rejection_log: list, utc_now: datetime, **kwargs
    ) -> TradeSignal:
        return TradeSignal(
            pair          = symbol,
            direction     = "NO_TRADE",
            confidence    = 0,
            entry         = 0.0,
            stop_loss     = 0.0,
            tp1           = 0.0,
            tp2           = 0.0,
            tp3           = 0.0,
            risk_reward   = 0.0,
            lot_size      = 0.0,
            decision      = "NO TRADE",
            rejection_log = rejection_log,
            timestamp     = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            **kwargs,
        )


# ─── Formatter ────────────────────────────────────────────────────

def format_signal(sig: TradeSignal) -> str:
    """Render signal in the Step 13 output format."""
    if sig.direction == "NO_TRADE":
        lines = [
            f"PAIR: {sig.pair}",
            f"DIRECTION: NO TRADE",
            f"TIMESTAMP: {sig.timestamp}",
            "REASONS:",
        ] + [f"  • {r}" for r in sig.rejection_log]
        return "\n".join(lines)

    conf_label = (
        "Institutional Grade" if sig.confidence >= 90 else
        "High Quality"        if sig.confidence >= 80 else
        "Moderate Quality"    if sig.confidence >= 70 else
        "Below Threshold"
    )

    sweep_info = (
        f"Previous {'Low' if sig.direction == 'LONG' else 'High'} Swept "
        f"(rej={sig.sweep.get('rejection_strength', 0):.2%})"
        if sig.sweep.get("detected") else "Not Detected"
    )

    lines = [
        "=" * 52,
        f"PAIR:             {sig.pair}",
        f"DIRECTION:        {sig.direction}",
        f"CONFIDENCE:       {sig.confidence}/100  [{conf_label}]",
        f"TIMESTAMP:        {sig.timestamp}",
        "-" * 52,
        f"TREND:",
        f"  H4              {sig.trend_h4.upper()}",
        f"  H1              {sig.trend_h1.upper()}",
        f"  M15             {sig.trend_m15.upper()}",
        f"MARKET STRUCTURE: {sig.structure.upper()}",
        f"LIQUIDITY EVENT:  {sweep_info}",
        f"VOLUME RATIO:     {sig.volume_ratio}x Average",
        f"ATR:              {sig.atr_current:.6f}  "
        f"({'EXPANDING' if sig.atr_expanding else 'FLAT'} / "
        f"{'COMPRESSED' if sig.atr_compressed else 'NORMAL'})",
        "-" * 52,
        f"ENTRY:            {sig.entry:.5f}",
        f"STOP LOSS:        {sig.stop_loss:.5f}",
        f"TAKE PROFIT 1:    {sig.tp1:.5f}",
        f"TAKE PROFIT 2:    {sig.tp2:.5f}",
        f"TAKE PROFIT 3:    {sig.tp3:.5f}",
        f"RISK REWARD:      1:{sig.risk_reward}",
        f"LOT SIZE:         {sig.lot_size}",
        "-" * 52,
        f"DECISION:         {sig.decision}",
        "=" * 52,
    ]
    if sig.rejection_log:
        lines += ["NOTES:"] + [f"  • {r}" for r in sig.rejection_log]

    return "\n".join(lines)
