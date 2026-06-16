"""
Signal Engine  v2  —  Yesterday-Volume-Driven
──────────────────────────────────────────────
Bias and entry zone come exclusively from yesterday's:
    • Volume Profile  (POC / VAH / VAL)
    • CVD             (cumulative volume delta)
    • Absorption zones (large volume / small body candles)

Today's price action only needs to:
    1. Return to the entry zone derived from yesterday
    2. Show ONE of:  rejection candle | CVD shift | absorption signal

Price-action indicators (EMAs, swing points) are used only for
stop-loss placement and TP targeting — NOT for bias.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import numpy as np

from config.settings import (
    MIN_RR, TP1_ATR_MULT, TP2_ATR_MULT,
    MIN_CONFIDENCE,
    ALLOWED_SESSION_HOURS_UTC,
    NEWS_BLACKOUT_MINUTES,
)
from core.indicators import add_atr, get_swing_points
from core.volume_profile import YesterdayContext, YesterdayContextBuilder

logger = logging.getLogger(__name__)


# ─── Signal Dataclass ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    pair:           str
    direction:      str        # LONG | SHORT | NO_TRADE
    confidence:     int
    entry:          float
    stop_loss:      float
    tp1:            float
    tp2:            float
    tp3:            float
    risk_reward:    float
    lot_size:       float
    decision:       str        # EXECUTE | REVIEW MANUALLY | NO TRADE

    # Yesterday context summary
    yest_bias:      str   = ""
    yest_poc:       float = 0.0
    yest_vah:       float = 0.0
    yest_val:       float = 0.0
    yest_cvd_bias:  str   = ""
    cvd_divergence: bool  = False
    entry_zone_high: float = 0.0
    entry_zone_low:  float = 0.0
    absorptions_count: int = 0

    # Today confirmation
    in_entry_zone:  bool  = False
    rejection_candle: bool = False
    cvd_confirmed:  bool  = False
    absorption_confirmed: bool = False

    atr_current:    float = 0.0
    rejection_log:  list  = field(default_factory=list)
    timestamp:      str   = ""


# ─── Session / News Filters ───────────────────────────────────────

def is_valid_session(utc_now: datetime) -> bool:
    return utc_now.hour in ALLOWED_SESSION_HOURS_UTC


def is_news_blackout(utc_now: datetime, news_times_utc: list) -> bool:
    from datetime import timedelta
    window = NEWS_BLACKOUT_MINUTES * 60
    for nt in news_times_utc:
        if abs((utc_now - nt).total_seconds()) <= window:
            return True
    return False


# ─── Lot Size Calculator ──────────────────────────────────────────

def calculate_lot_size(
    balance:     float,
    risk_pct:    float,
    entry:       float,
    stop_loss:   float,
    point_value: float = 10.0,
    pip_size:    float = 0.0001,
) -> float:
    risk_amount  = balance * risk_pct / 100.0
    pips_at_risk = abs(entry - stop_loss) / pip_size
    if pips_at_risk <= 0:
        return 0.01
    return round(max(0.01, risk_amount / (pips_at_risk * point_value)), 2)


# ─── Today Confirmation Checks ────────────────────────────────────

def check_in_entry_zone(price: float, ctx: YesterdayContext) -> bool:
    """Is current price inside yesterday's derived entry zone?"""
    return ctx.entry_zone_low <= price <= ctx.entry_zone_high


def check_rejection_candle(df: pd.DataFrame, bias: str) -> bool:
    """
    Last candle shows rejection of the entry zone:
    - LONG: long lower wick, close in upper 40 % of range
    - SHORT: long upper wick, close in lower 40 % of range
    """
    c = df.iloc[-1]
    candle_range = c["high"] - c["low"]
    if candle_range == 0:
        return False

    body_top    = max(c["open"], c["close"])
    body_bottom = min(c["open"], c["close"])
    upper_wick  = c["high"] - body_top
    lower_wick  = body_bottom - c["low"]

    if bias == "bullish":
        return (
            lower_wick > candle_range * 0.45 and
            c["close"]  > c["low"] + candle_range * 0.60
        )
    else:  # bearish
        return (
            upper_wick > candle_range * 0.45 and
            c["close"]  < c["high"] - candle_range * 0.60
        )


def check_cvd_shift(df: pd.DataFrame, bias: str, lookback: int = 5) -> bool:
    """
    In the last N candles, is volume delta shifting in the bias direction?
    Bullish: last 3 candles have net positive delta (more up volume than down)
    Bearish: last 3 candles have net negative delta
    """
    recent = df.tail(lookback).copy()
    recent["delta"] = np.where(
        recent["close"] > recent["open"],  recent["volume"],
        np.where(recent["close"] < recent["open"], -recent["volume"], 0.0)
    )
    net_delta = recent["delta"].sum()
    if bias == "bullish":
        return net_delta > 0
    else:
        return net_delta < 0


def check_absorption_today(df: pd.DataFrame, ctx: YesterdayContext, bias: str) -> bool:
    """
    Is there absorption near the entry zone in today's recent candles?
    Checks last 3 candles for high-volume / small-body pattern.
    """
    recent      = df.tail(3)
    vol_median  = df["volume"].median()
    for _, row in recent.iterrows():
        candle_range = row["high"] - row["low"]
        if candle_range == 0:
            continue
        body       = abs(row["close"] - row["open"])
        body_ratio = body / candle_range
        high_vol   = row["volume"] >= vol_median * 1.5
        small_body = body_ratio <= 0.35

        if not (high_vol and small_body):
            continue

        # Must be near the entry zone
        candle_mid = (row["high"] + row["low"]) / 2
        in_zone    = ctx.entry_zone_low <= candle_mid <= ctx.entry_zone_high
        if not in_zone:
            continue

        # Side check
        mid = (row["high"] + row["low"]) / 2
        if bias == "bullish" and row["close"] > mid:
            return True   # bid absorption (buyers eating sells near VAL/POC)
        if bias == "bearish" and row["close"] < mid:
            return True   # ask absorption (sellers eating buys near POC/VAH)

    return False


# ─── Confidence Scorer ────────────────────────────────────────────

def compute_confidence(checks: dict) -> int:
    """
    Weights reflecting the new yesterday-volume-first approach.
    Max = 100.
    """
    weights = {
        # Yesterday context (determines bias)
        "yest_vol_bias":    25,   # volume profile bias clear
        "yest_cvd_bias":    20,   # CVD bias agrees
        "cvd_divergence":   10,   # divergence adds conviction to reversal
        "absorption_zone":  10,   # strong absorption zone present

        # Today confirmation (determines entry timing)
        "in_entry_zone":    15,   # price returned to entry zone
        "rejection_candle": 10,   # candle shows rejection
        "cvd_confirmed":     5,   # intraday CVD shifting in bias direction
        "absorption_today":  5,   # absorption candle today near zone

        # Risk management
        "rr_ok":            0,    # binary gate — handled separately
    }
    score = sum(weights[k] for k, v in checks.items() if v and k in weights)
    return min(100, score)


# ─── Core Signal Builder ──────────────────────────────────────────

class SignalEngine:

    def __init__(self, account_balance: float = 10_000.0, risk_pct: float = 1.0):
        self.balance  = account_balance
        self.risk_pct = risk_pct
        self._ctx_builder = YesterdayContextBuilder()

    # ── Public API ─────────────────────────────────────────────────

    def build_yesterday_context(self, df_yesterday: pd.DataFrame) -> YesterdayContext:
        """
        Call once per day with yesterday's intraday candles (M15 or H1).
        Returns a YesterdayContext object to pass into evaluate() all day.
        """
        return self._ctx_builder.build(df_yesterday)

    def evaluate(
        self,
        symbol:      str,
        df_m15:      pd.DataFrame,   # today's live M15 candles
        ctx:         YesterdayContext,
        news_times:  list = None,
        utc_now:     datetime = None,
    ) -> TradeSignal:
        """
        Run signal evaluation using yesterday's context + today's price action.
        Call repeatedly as new M15 candles arrive.
        """
        if utc_now is None:
            utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        if news_times is None:
            news_times = []

        rejection_log = []

        # ── Prepare today's M15 ───────────────────────────────────
        df = self._prepare(df_m15)
        current_price = float(df.iloc[-1]["close"])

        # ── Session Filter ────────────────────────────────────────
        if not is_valid_session(utc_now):
            rejection_log.append("Outside London/NY session")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── News Filter ───────────────────────────────────────────
        if is_news_blackout(utc_now, news_times):
            rejection_log.append("News blackout active")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── STEP 1: Yesterday bias must not be neutral ─────────────
        if ctx.bias == "neutral":
            rejection_log.append(
                f"Yesterday bias neutral — VP and CVD disagreed "
                f"(VP={ctx.profile.bias}, CVD={ctx.cvd.bias})"
            )
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        bias      = ctx.bias
        direction = "LONG" if bias == "bullish" else "SHORT"

        # ── STEP 2: Invalidation check ────────────────────────────
        if ctx.invalidation > 0:
            if bias == "bullish" and current_price < ctx.invalidation:
                rejection_log.append(
                    f"Price {current_price:.5f} broke invalidation "
                    f"{ctx.invalidation:.5f} — bias cancelled"
                )
                return self._no_trade(symbol, rejection_log, utc_now, ctx)
            if bias == "bearish" and current_price > ctx.invalidation:
                rejection_log.append(
                    f"Price {current_price:.5f} broke invalidation "
                    f"{ctx.invalidation:.5f} — bias cancelled"
                )
                return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── STEP 3: Price must be in entry zone ───────────────────
        in_zone = check_in_entry_zone(current_price, ctx)
        if not in_zone:
            rejection_log.append(
                f"Price {current_price:.5f} not in entry zone "
                f"[{ctx.entry_zone_low:.5f} – {ctx.entry_zone_high:.5f}]"
            )

        # ── STEP 4: Today confirmation checks ─────────────────────
        rejection_candle     = check_rejection_candle(df, bias)
        cvd_confirmed        = check_cvd_shift(df, bias)
        absorption_today     = check_absorption_today(df, ctx, bias)

        confirmation_count = sum([rejection_candle, cvd_confirmed, absorption_today])
        if in_zone and confirmation_count == 0:
            rejection_log.append(
                "In zone but no confirmation (need rejection candle, CVD shift, or absorption)"
            )

        # ── STEP 5: Confidence score ──────────────────────────────
        checks = {
            "yest_vol_bias":    ctx.profile.bias == bias,
            "yest_cvd_bias":    ctx.cvd.bias     == bias,
            "cvd_divergence":   ctx.cvd.divergence,
            "absorption_zone":  len([z for z in ctx.absorptions if z.strength > 0.5]) > 0,
            "in_entry_zone":    in_zone,
            "rejection_candle": rejection_candle,
            "cvd_confirmed":    cvd_confirmed,
            "absorption_today": absorption_today,
            "rr_ok":            True,  # resolved below
        }
        confidence = compute_confidence(checks)

        if confidence < MIN_CONFIDENCE:
            rejection_log.append(f"Confidence {confidence} < minimum {MIN_CONFIDENCE}")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── STEP 6: Entry / SL / TP ───────────────────────────────
        atr_val   = float(df.iloc[-1]["atr"])
        entry     = current_price

        if direction == "LONG":
            # SL below the entry zone low (or absorption zone if present)
            sl_anchor  = ctx.entry_zone_low
            stop_loss  = sl_anchor - atr_val * 0.5
            tp1        = entry + atr_val * TP1_ATR_MULT
            tp2        = entry + atr_val * TP2_ATR_MULT
            # TP3: yesterday high then beyond
            tp3        = ctx.high + (ctx.high - ctx.low) * 0.382
        else:  # SHORT
            sl_anchor  = ctx.entry_zone_high
            stop_loss  = sl_anchor + atr_val * 0.5
            tp1        = entry - atr_val * TP1_ATR_MULT
            tp2        = entry - atr_val * TP2_ATR_MULT
            tp3        = ctx.low  - (ctx.high - ctx.low) * 0.382

        risk   = abs(entry - stop_loss)
        reward = abs(tp1   - entry)
        rr     = round(reward / risk, 2) if risk > 0 else 0.0
        checks["rr_ok"] = rr >= MIN_RR
        confidence = compute_confidence(checks)

        if rr < MIN_RR:
            rejection_log.append(f"R:R {rr:.2f} below minimum 1:{MIN_RR}")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        lot_size = calculate_lot_size(self.balance, self.risk_pct, entry, stop_loss)

        # ── STEP 7: Decision ──────────────────────────────────────
        if confidence >= 80:
            decision = "EXECUTE TRADE"
        elif confidence >= 70:
            decision = "REVIEW MANUALLY"
        else:
            decision = "NO TRADE"

        return TradeSignal(
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

            yest_bias         = ctx.bias,
            yest_poc          = round(ctx.profile.poc, 5),
            yest_vah          = round(ctx.profile.vah, 5),
            yest_val          = round(ctx.profile.val, 5),
            yest_cvd_bias     = ctx.cvd.bias,
            cvd_divergence    = ctx.cvd.divergence,
            entry_zone_high   = ctx.entry_zone_high,
            entry_zone_low    = ctx.entry_zone_low,
            absorptions_count = len(ctx.absorptions),

            in_entry_zone         = in_zone,
            rejection_candle      = rejection_candle,
            cvd_confirmed         = cvd_confirmed,
            absorption_confirmed  = absorption_today,

            atr_current   = round(atr_val, 6),
            rejection_log = rejection_log,
            timestamp     = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

    # ── Helpers ────────────────────────────────────────────────────

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_atr(df)
        df = get_swing_points(df)
        return df

    def _no_trade(
        self, symbol: str, rejection_log: list,
        utc_now: datetime, ctx: YesterdayContext
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
            yest_bias     = ctx.bias if ctx else "",
            yest_poc      = round(ctx.profile.poc, 5) if ctx else 0.0,
            yest_vah      = round(ctx.profile.vah, 5) if ctx else 0.0,
            yest_val      = round(ctx.profile.val, 5) if ctx else 0.0,
            rejection_log = rejection_log,
            timestamp     = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )


# ─── Formatter ────────────────────────────────────────────────────

def format_signal(sig: TradeSignal) -> str:
    if sig.direction == "NO_TRADE":
        lines = [
            "=" * 52,
            f"PAIR:      {sig.pair}",
            f"DECISION:  NO TRADE",
            f"TIMESTAMP: {sig.timestamp}",
            f"YESTERDAY: POC={sig.yest_poc}  VAH={sig.yest_vah}  VAL={sig.yest_val}",
            "REASONS:",
        ] + [f"  • {r}" for r in sig.rejection_log]
        lines.append("=" * 52)
        return "\n".join(lines)

    conf_label = (
        "Institutional Grade" if sig.confidence >= 90 else
        "High Quality"        if sig.confidence >= 80 else
        "Moderate"            if sig.confidence >= 70 else
        "Below Threshold"
    )

    tick = "✔" if True else "✘"   # helper

    lines = [
        "=" * 52,
        f"PAIR:              {sig.pair}",
        f"DIRECTION:         {sig.direction}",
        f"CONFIDENCE:        {sig.confidence}/100  [{conf_label}]",
        f"TIMESTAMP:         {sig.timestamp}",
        "-" * 52,
        "YESTERDAY CONTEXT:",
        f"  Bias             {sig.yest_bias.upper()}",
        f"  POC              {sig.yest_poc}",
        f"  VAH              {sig.yest_vah}",
        f"  VAL              {sig.yest_val}",
        f"  CVD Bias         {sig.yest_cvd_bias.upper()}",
        f"  CVD Divergence   {'YES ⚡' if sig.cvd_divergence else 'No'}",
        f"  Absorption Zones {sig.absorptions_count}",
        "-" * 52,
        "TODAY CONFIRMATION:",
        f"  In Entry Zone    {'✔' if sig.in_entry_zone        else '✘'}  [{sig.entry_zone_low} – {sig.entry_zone_high}]",
        f"  Rejection Candle {'✔' if sig.rejection_candle     else '✘'}",
        f"  CVD Shift        {'✔' if sig.cvd_confirmed        else '✘'}",
        f"  Absorption Today {'✔' if sig.absorption_confirmed else '✘'}",
        "-" * 52,
        f"ENTRY:             {sig.entry}",
        f"STOP LOSS:         {sig.stop_loss}",
        f"TP1:               {sig.tp1}",
        f"TP2:               {sig.tp2}",
        f"TP3:               {sig.tp3}",
        f"RISK REWARD:       1:{sig.risk_reward}",
        f"LOT SIZE:          {sig.lot_size}",
        f"ATR:               {sig.atr_current}",
        "-" * 52,
        f"DECISION:          {sig.decision}",
        "=" * 52,
    ]
    if sig.rejection_log:
        lines += ["NOTES:"] + [f"  • {r}" for r in sig.rejection_log]

    return "\n".join(lines)