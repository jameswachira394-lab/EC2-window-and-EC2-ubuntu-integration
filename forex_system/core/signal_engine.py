"""
Signal Engine  v3  —  Yesterday-Volume-Driven  (Soft-Gate Edition)
──────────────────────────────────────────────────────────────────
Bias and entry zone come exclusively from yesterday's:
    • Volume Profile  (POC / VAH / VAL)
    • CVD             (cumulative volume delta)
    • Absorption zones (large volume / small body candles)

Today's price action only needs to:
    1. Return to (or be near) the entry zone derived from yesterday
    2. Show ONE of:  rejection candle | CVD shift | absorption signal

Key changes from v2:
    • No single condition hard-blocks a trade (except both VP and CVD neutral,
      or price breaking the invalidation level)
    • Neutral bias resolved by CVD tiebreak before giving up
    • Entry zone check returns a proximity score (0–1) not a binary
    • Extended POC zone added for strong-trend days
    • Confidence weights redistributed; lower MIN_CONFIDENCE threshold
    • Price-action indicators (EMAs, swing points) still used only for
      stop-loss placement and TP targeting — NOT for bias
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import (
    ALLOWED_SESSION_HOURS_UTC,
    MIN_RR,
    NEWS_BLACKOUT_MINUTES,
    TP1_ATR_MULT,
    TP2_ATR_MULT,
)
from core.indicators import add_atr, get_swing_points
from core.context_builder import YesterdayContext, YesterdayContextBuilder

logger = logging.getLogger(__name__)

# ─── Thresholds (override in config.settings if preferred) ────────

EXECUTE_THRESHOLD = 75   # confidence >= this → EXECUTE TRADE
REVIEW_THRESHOLD  = 60   # confidence >= this → REVIEW MANUALLY
MIN_CONFIDENCE    = 55   # below this → NO TRADE regardless of RR


# ─── Signal Dataclass ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    pair:            str
    direction:       str        # LONG | SHORT | NO_TRADE
    confidence:      int
    entry:           float
    stop_loss:       float
    tp1:             float
    tp2:             float
    tp3:             float
    risk_reward:     float
    lot_size:        float
    decision:        str        # EXECUTE | REVIEW MANUALLY | NO TRADE

    # Yesterday context
    yest_bias:           str   = ""
    yest_poc:            float = 0.0
    yest_vah:            float = 0.0
    yest_val:            float = 0.0
    yest_cvd_bias:       str   = ""
    cvd_divergence:      bool  = False
    entry_zone_high:     float = 0.0
    entry_zone_low:      float = 0.0
    absorptions_count:   int   = 0

    # Today confirmation
    in_entry_zone:        bool  = False
    zone_proximity:       float = 0.0   # 0–1; 1 = fully inside zone
    used_extended_zone:   bool  = False
    rejection_candle:     bool  = False
    cvd_confirmed:        bool  = False
    absorption_confirmed: bool  = False

    atr_current:     float = 0.0
    rejection_log:   list  = field(default_factory=list)
    timestamp:       str   = ""


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


# ─── Zone Proximity Check ─────────────────────────────────────────

def check_zone_proximity(
    price: float,
    ctx: YesterdayContext,
    atr_val: float,
) -> Tuple[bool, float, bool]:
    """
    Returns (in_primary_zone, proximity_score 0–1, used_extended_zone).

    Primary zone   : ctx.entry_zone_low  – ctx.entry_zone_high
    Extended zone  : POC ± 0.5 ATR  (used on strong-trend days where price
                     rarely retraces all the way to VAL/VAH)

    proximity_score:
        1.0  = fully inside the primary zone
        0.5  = inside the extended zone but outside primary
        0–1  = gradient as price moves up to 1 zone-width away from primary
    """
    # --- primary zone ---
    if ctx.entry_zone_low <= price <= ctx.entry_zone_high:
        return True, 1.0, False

    zone_size = max(ctx.entry_zone_high - ctx.entry_zone_low, atr_val * 0.1)

    # Distance outside primary zone as fraction of zone size
    if price < ctx.entry_zone_low:
        gap = ctx.entry_zone_low - price
    else:
        gap = price - ctx.entry_zone_high

    primary_proximity = max(0.0, 1.0 - gap / zone_size)

    # --- extended zone (strong-trend days only) ---
    # Condition: VP and CVD both agree (no divergence) → trend continuation
    strong_trend = (
        ctx.profile.bias == ctx.cvd.bias
        and ctx.profile.bias != "neutral"
        and not ctx.cvd.divergence
    )
    if strong_trend and atr_val > 0:
        ext_low  = ctx.profile.poc - atr_val * 0.5
        ext_high = ctx.profile.poc + atr_val * 0.5
        if ext_low <= price <= ext_high:
            # In extended zone; score it at 0.5 (less preferred than primary)
            return False, 0.5, True

    return False, primary_proximity, False


# ─── Today Confirmation Checks ────────────────────────────────────

def check_rejection_candle(df: pd.DataFrame, bias: str) -> bool:
    """
    Last candle shows rejection of the entry zone.
    LONG: long lower wick, close in upper 40% of range.
    SHORT: long upper wick, close in lower 40% of range.
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
            lower_wick > candle_range * 0.45
            and c["close"] > c["low"] + candle_range * 0.60
        )
    else:
        return (
            upper_wick > candle_range * 0.45
            and c["close"] < c["high"] - candle_range * 0.60
        )


def check_cvd_shift(df: pd.DataFrame, bias: str, lookback: int = 5) -> bool:
    """
    In the last N candles, is volume delta shifting in the bias direction?
    Uses approximate delta: up-close candles = positive, down-close = negative.
    """
    recent = df.tail(lookback).copy()
    recent["delta"] = np.where(
        recent["close"] > recent["open"],  recent["volume"],
        np.where(recent["close"] < recent["open"], -recent["volume"], 0.0),
    )
    net_delta = recent["delta"].sum()
    return net_delta > 0 if bias == "bullish" else net_delta < 0


def check_absorption_today(
    df: pd.DataFrame, ctx: YesterdayContext, bias: str
) -> bool:
    """
    Absorption candle in last 3 candles: high volume, small body, near entry zone.
    """
    recent     = df.tail(3)
    vol_median = df["volume"].median()

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

        candle_mid = (row["high"] + row["low"]) / 2
        in_zone    = ctx.entry_zone_low <= candle_mid <= ctx.entry_zone_high
        if not in_zone:
            continue

        mid = candle_mid
        if bias == "bullish" and row["close"] > mid:
            return True
        if bias == "bearish" and row["close"] < mid:
            return True

    return False


# ─── Confidence Scorer ────────────────────────────────────────────

def compute_confidence(checks: dict) -> int:
    """
    Soft scoring — every check contributes points; nothing hard-blocks.

    'in_entry_zone' and 'zone_proximity' are handled together:
        - fully in zone         → 20 pts
        - extended zone         → 10 pts
        - gradient (0–1 score)  → up to 15 pts

    Max theoretical = 100.
    """
    weights = {
        # Yesterday context
        "yest_vol_bias":    20,   # VP bias is clear and agrees with direction
        "yest_cvd_bias":    15,   # CVD bias agrees
        "cvd_divergence":    8,   # divergence adds conviction to reversal
        "absorption_zone":   7,   # strong absorption zone from yesterday

        # Today location + confirmation
        "rejection_candle": 15,   # candle shows rejection
        "cvd_confirmed":    10,   # intraday CVD shifting in bias direction
        "absorption_today":  5,   # absorption candle today near zone

        # RR gate (resolved separately; keep weight 0 here)
        "rr_ok":             0,
    }

    score = sum(weights[k] for k, v in checks.items() if v and k in weights)

    # Zone proximity contributes up to 20 pts (replaces binary in_entry_zone)
    proximity = float(checks.get("zone_proximity", 0.0))
    score += round(proximity * 20)

    return min(100, score)


# ─── Core Signal Builder ──────────────────────────────────────────

class SignalEngine:

    def __init__(self, account_balance: float = 10_000.0, risk_pct: float = 1.0):
        self.balance      = account_balance
        self.risk_pct     = risk_pct
        self._ctx_builder = YesterdayContextBuilder()

    # ── Public API ─────────────────────────────────────────────────

    def build_yesterday_context(self, df_yesterday: pd.DataFrame) -> YesterdayContext:
        """
        Call once per day with yesterday's intraday candles (M15 or H1).
        Returns a YesterdayContext to pass into evaluate() all day.
        """
        return self._ctx_builder.build(df_yesterday)

    def evaluate(
        self,
        symbol:     str,
        df_m15:     pd.DataFrame,
        ctx:        YesterdayContext,
        news_times: list = None,
        utc_now:    datetime = None,
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
        df            = self._prepare(df_m15)
        current_price = float(df.iloc[-1]["close"])
        atr_val       = float(df.iloc[-1]["atr"])

        # ── Hard gates (session / news / total neutrality) ─────────

        if not is_valid_session(utc_now):
            rejection_log.append("Outside London/NY session")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        if is_news_blackout(utc_now, news_times):
            rejection_log.append("News blackout active")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── Resolve bias (soft: CVD tiebreak on neutral VP) ────────
        bias = ctx.bias

        if bias == "neutral":
            # Attempt tiebreak via CVD
            if ctx.cvd.bias in ("bullish", "bearish"):
                bias = ctx.cvd.bias
                rejection_log.append(
                    f"VP neutral — CVD tiebreak used ({bias}); confidence penalised"
                )
                logger.debug("Bias resolved by CVD tiebreak: %s", bias)
            else:
                # Both VP and CVD are neutral — nothing to trade
                rejection_log.append(
                    "Both VP and CVD neutral — no directional bias available"
                )
                return self._no_trade(symbol, rejection_log, utc_now, ctx)

        direction = "LONG" if bias == "bullish" else "SHORT"

        # ── Invalidation check (still a hard gate) ─────────────────
        if ctx.invalidation > 0:
            broke = (
                (bias == "bullish" and current_price < ctx.invalidation)
                or (bias == "bearish" and current_price > ctx.invalidation)
            )
            if broke:
                rejection_log.append(
                    f"Price {current_price:.5f} broke invalidation "
                    f"{ctx.invalidation:.5f} — bias cancelled"
                )
                return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── Zone proximity (soft) ───────────────────────────────────
        in_zone, proximity, used_ext = check_zone_proximity(
            current_price, ctx, atr_val
        )

        if not in_zone:
            if used_ext:
                rejection_log.append(
                    f"Price in extended POC zone (trend continuation); "
                    f"primary zone [{ctx.entry_zone_low:.5f}–{ctx.entry_zone_high:.5f}]"
                )
            elif proximity < 0.20:
                rejection_log.append(
                    f"Price {current_price:.5f} too far from entry zone "
                    f"[{ctx.entry_zone_low:.5f}–{ctx.entry_zone_high:.5f}] "
                    f"(proximity={proximity:.2f})"
                )

        # ── Today confirmation checks ───────────────────────────────
        rejection_candle  = check_rejection_candle(df, bias)
        cvd_confirmed     = check_cvd_shift(df, bias)
        absorption_today  = check_absorption_today(df, ctx, bias)

        confirmations = sum([rejection_candle, cvd_confirmed, absorption_today])
        if confirmations == 0:
            rejection_log.append(
                "No today-confirmation (rejection candle / CVD shift / absorption)"
            )

        # ── Confidence score ────────────────────────────────────────
        # Note: yest_vol_bias is True only when VP bias matches the resolved bias
        # AND we didn't have to use the CVD tiebreak (i.e. ctx.bias was not neutral).
        vp_agrees   = ctx.profile.bias == bias and ctx.bias != "neutral"
        cvd_agrees  = ctx.cvd.bias     == bias

        checks = {
            "yest_vol_bias":    vp_agrees,
            "yest_cvd_bias":    cvd_agrees,
            "cvd_divergence":   ctx.cvd.divergence,
            "absorption_zone":  any(z.strength > 0.5 for z in ctx.absorptions),
            "zone_proximity":   proximity,          # float, handled specially
            "rejection_candle": rejection_candle,
            "cvd_confirmed":    cvd_confirmed,
            "absorption_today": absorption_today,
            "rr_ok":            True,               # resolved below
        }

        confidence = compute_confidence(checks)

        if confidence < MIN_CONFIDENCE:
            rejection_log.append(
                f"Confidence {confidence} < minimum {MIN_CONFIDENCE}"
            )
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        # ── Entry / SL / TP ────────────────────────────────────────
        entry = current_price

        if direction == "LONG":
            sl_anchor = ctx.entry_zone_low
            stop_loss = sl_anchor - atr_val * 0.5
            tp1       = entry + atr_val * TP1_ATR_MULT
            tp2       = entry + atr_val * TP2_ATR_MULT
            tp3       = ctx.high + (ctx.high - ctx.low) * 0.382
        else:
            sl_anchor = ctx.entry_zone_high
            stop_loss = sl_anchor + atr_val * 0.5
            tp1       = entry - atr_val * TP1_ATR_MULT
            tp2       = entry - atr_val * TP2_ATR_MULT
            tp3       = ctx.low  - (ctx.high - ctx.low) * 0.382

        risk   = abs(entry - stop_loss)
        reward = abs(tp1   - entry)
        rr     = round(reward / risk, 2) if risk > 0 else 0.0

        checks["rr_ok"] = rr >= MIN_RR
        confidence = compute_confidence(checks)   # recompute (rr_ok weight is 0, no change)

        if rr < MIN_RR:
            rejection_log.append(f"R:R {rr:.2f} below minimum 1:{MIN_RR}")
            return self._no_trade(symbol, rejection_log, utc_now, ctx)

        lot_size = calculate_lot_size(
            self.balance, self.risk_pct, entry, stop_loss
        )

        # ── Decision ───────────────────────────────────────────────
        if confidence >= EXECUTE_THRESHOLD:
            decision = "EXECUTE TRADE"
        elif confidence >= REVIEW_THRESHOLD:
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

            yest_bias           = ctx.bias,
            yest_poc            = round(ctx.profile.poc, 5),
            yest_vah            = round(ctx.profile.vah, 5),
            yest_val            = round(ctx.profile.val, 5),
            yest_cvd_bias       = ctx.cvd.bias,
            cvd_divergence      = ctx.cvd.divergence,
            entry_zone_high     = ctx.entry_zone_high,
            entry_zone_low      = ctx.entry_zone_low,
            absorptions_count   = len(ctx.absorptions),

            in_entry_zone        = in_zone,
            zone_proximity       = round(proximity, 3),
            used_extended_zone   = used_ext,
            rejection_candle     = rejection_candle,
            cvd_confirmed        = cvd_confirmed,
            absorption_confirmed = absorption_today,

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
        self,
        symbol:       str,
        rejection_log: list,
        utc_now:      datetime,
        ctx:          Optional[YesterdayContext],
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
            yest_bias     = ctx.bias              if ctx else "",
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
            "=" * 56,
            f"PAIR:      {sig.pair}",
            f"DECISION:  NO TRADE",
            f"TIMESTAMP: {sig.timestamp}",
            f"YESTERDAY: POC={sig.yest_poc}  VAH={sig.yest_vah}  VAL={sig.yest_val}",
            "REASONS:",
        ] + [f"  • {r}" for r in sig.rejection_log]
        lines.append("=" * 56)
        return "\n".join(lines)

    conf_label = (
        "Institutional Grade" if sig.confidence >= 90 else
        "High Quality"        if sig.confidence >= 80 else
        "Good"                if sig.confidence >= EXECUTE_THRESHOLD else
        "Moderate"            if sig.confidence >= REVIEW_THRESHOLD else
        "Below Threshold"
    )

    proximity_bar = "█" * int(sig.zone_proximity * 10) + "░" * (10 - int(sig.zone_proximity * 10))
    zone_label    = "PRIMARY" if sig.in_entry_zone else ("EXTENDED" if sig.used_extended_zone else "OUTSIDE")

    lines = [
        "=" * 56,
        f"PAIR:              {sig.pair}",
        f"DIRECTION:         {sig.direction}",
        f"CONFIDENCE:        {sig.confidence}/100  [{conf_label}]",
        f"TIMESTAMP:         {sig.timestamp}",
        "-" * 56,
        "YESTERDAY CONTEXT:",
        f"  Bias             {sig.yest_bias.upper()}",
        f"  POC              {sig.yest_poc}",
        f"  VAH              {sig.yest_vah}",
        f"  VAL              {sig.yest_val}",
        f"  CVD Bias         {sig.yest_cvd_bias.upper()}",
        f"  CVD Divergence   {'YES ⚡' if sig.cvd_divergence else 'No'}",
        f"  Absorption Zones {sig.absorptions_count}",
        "-" * 56,
        "TODAY CONFIRMATION:",
        f"  Zone Location    {zone_label}  [{sig.entry_zone_low} – {sig.entry_zone_high}]",
        f"  Zone Proximity   [{proximity_bar}]  {sig.zone_proximity:.0%}",
        f"  Rejection Candle {'✔' if sig.rejection_candle     else '✘'}",
        f"  CVD Shift        {'✔' if sig.cvd_confirmed        else '✘'}",
        f"  Absorption Today {'✔' if sig.absorption_confirmed else '✘'}",
        "-" * 56,
        f"ENTRY:             {sig.entry}",
        f"STOP LOSS:         {sig.stop_loss}",
        f"TP1:               {sig.tp1}",
        f"TP2:               {sig.tp2}",
        f"TP3:               {sig.tp3}",
        f"RISK REWARD:       1:{sig.risk_reward}",
        f"LOT SIZE:          {sig.lot_size}",
        f"ATR:               {sig.atr_current}",
        "-" * 56,
        f"DECISION:          {sig.decision}",
        "=" * 56,
    ]

    if sig.rejection_log:
        lines += ["NOTES:"] + [f"  • {r}" for r in sig.rejection_log]

    return "\n".join(lines)