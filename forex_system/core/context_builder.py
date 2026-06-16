"""
Volume Profile & Order Book Analysis
─────────────────────────────────────
Builds yesterday's context from MT5 tick_volume OHLCV data:
  • Volume Profile  → POC, VAH, VAL, Value Area
  • CVD             → Cumulative Volume Delta (buying vs selling pressure)
  • Absorption      → detects where large volume didn't move price (walls)

All inputs are standard pandas DataFrames with columns:
    time, open, high, low, close, volume   (tick_volume from MT5)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np


# ─── Output Structures ────────────────────────────────────────────

@dataclass
class VolumeProfile:
    poc:        float          # Point of Control  — highest volume price
    vah:        float          # Value Area High   — top of 70 % volume zone
    val:        float          # Value Area Low    — bottom of 70 % volume zone
    total_vol:  float          # total tick volume for the session
    bias:       str            # "bullish" | "bearish" | "neutral"
    # Full profile for optional inspection
    profile:    pd.DataFrame   = field(default_factory=pd.DataFrame)


@dataclass
class CVDResult:
    cvd_final:  float          # cumulative delta at session end
    cvd_high:   float          # intra-session high delta
    cvd_low:    float          # intra-session low delta
    bias:       str            # "bullish" | "bearish" | "neutral"
    divergence: bool           # price made new high/low but CVD didn't confirm


@dataclass
class AbsorptionZone:
    price_high: float
    price_low:  float
    volume:     float
    side:       str            # "bid_absorption" (buyers absorbing sells) | "ask_absorption"
    strength:   float          # 0.0 – 1.0


@dataclass
class YesterdayContext:
    """
    Single object passed into SignalEngine.
    Encodes everything derived from yesterday's session.
    """
    profile:          VolumeProfile
    cvd:              CVDResult
    absorptions:      list[AbsorptionZone]

    # Pre-computed key levels for today
    bias:             str        # final combined bias
    entry_zone_high:  float      # top of today's preferred entry zone
    entry_zone_low:   float      # bottom of today's preferred entry zone
    invalidation:     float      # level that kills the bias

    # Descriptive flags
    high:             float      # yesterday high
    low:              float      # yesterday low
    close:            float      # yesterday close


# ─── Volume Profile Builder ───────────────────────────────────────

class VolumeProfileBuilder:
    """
    Constructs a price-based volume profile from OHLCV candles.
    Distributes each candle's tick_volume uniformly across its
    high-low range (TPO approximation — best achievable from OHLCV).
    """

    def __init__(self, num_bins: int = 100, value_area_pct: float = 0.70):
        self.num_bins       = num_bins
        self.value_area_pct = value_area_pct

    def build(self, df: pd.DataFrame) -> VolumeProfile:
        lo  = df["low"].min()
        hi  = df["high"].max()
        tot = df["volume"].sum()

        bins  = np.linspace(lo, hi, self.num_bins + 1)
        volpr = np.zeros(self.num_bins)

        for _, row in df.iterrows():
            # which bins does this candle span?
            mask = (bins[:-1] >= row["low"]) & (bins[1:] <= row["high"])
            n    = mask.sum()
            if n == 0:
                # candle spans less than one bin — add to nearest bin
                mid_bin = np.searchsorted(bins, (row["low"] + row["high"]) / 2) - 1
                mid_bin = max(0, min(mid_bin, self.num_bins - 1))
                volpr[mid_bin] += row["volume"]
            else:
                volpr[mask] += row["volume"] / n

        bin_mids = (bins[:-1] + bins[1:]) / 2
        profile  = pd.DataFrame({"price": bin_mids, "volume": volpr})

        poc_idx = profile["volume"].idxmax()
        poc     = float(profile.loc[poc_idx, "price"])

        # Value Area: expand from POC until 70 % of volume is captured
        vah, val = self._value_area(profile, poc_idx, tot)

        # Bias: close relative to POC and value area
        last_close = float(df["close"].iloc[-1])
        if last_close > vah:
            bias = "bullish"
        elif last_close < val:
            bias = "bearish"
        elif last_close > poc:
            bias = "bullish"
        elif last_close < poc:
            bias = "bearish"
        else:
            bias = "neutral"

        return VolumeProfile(
            poc=poc, vah=vah, val=val,
            total_vol=float(tot), bias=bias, profile=profile
        )

    def _value_area(
        self, profile: pd.DataFrame, poc_idx: int, total_vol: float
    ) -> tuple[float, float]:
        target   = total_vol * self.value_area_pct
        captured = profile.loc[poc_idx, "volume"]
        upper    = poc_idx
        lower    = poc_idx

        while captured < target:
            next_up  = upper + 1
            next_dn  = lower - 1
            vol_up   = profile.loc[next_up, "volume"]  if next_up  < len(profile) else 0
            vol_dn   = profile.loc[next_dn, "volume"]  if next_dn  >= 0           else 0

            if vol_up == 0 and vol_dn == 0:
                break
            if vol_up >= vol_dn:
                upper     = next_up
                captured += vol_up
            else:
                lower     = next_dn
                captured += vol_dn

        vah = float(profile.loc[upper, "price"])
        val = float(profile.loc[lower, "price"])
        return vah, val


# ─── CVD Calculator ───────────────────────────────────────────────

class CVDCalculator:
    """
    Approximates Cumulative Volume Delta from OHLCV.

    Delta per candle heuristic (no tick data required):
        bullish candle (close > open) → +volume  (buyers dominated)
        bearish candle (close < open) → -volume  (sellers dominated)
        doji                          → 0

    CVD divergence: price reached a new session extreme but CVD did not.
    """

    def calculate(self, df: pd.DataFrame) -> CVDResult:
        df = df.copy()
        df["delta"] = np.where(
            df["close"] > df["open"],  df["volume"],
            np.where(
                df["close"] < df["open"], -df["volume"],
                0.0
            )
        )
        df["cvd"] = df["delta"].cumsum()

        cvd_final = float(df["cvd"].iloc[-1])
        cvd_high  = float(df["cvd"].max())
        cvd_low   = float(df["cvd"].min())

        # Bias from final CVD vs session midpoint
        cvd_mid = (cvd_high + cvd_low) / 2
        if cvd_final > cvd_mid * 1.1:
            bias = "bullish"
        elif cvd_final < cvd_mid * 0.9:
            bias = "bearish"
        else:
            bias = "neutral"

        # Divergence detection
        price_made_new_high = df["high"].iloc[-1] >= df["high"].max()
        cvd_made_new_high   = cvd_final >= cvd_high * 0.98
        price_made_new_low  = df["low"].iloc[-1]  <= df["low"].min()
        cvd_made_new_low    = cvd_final <= cvd_low  * 0.98

        divergence = (
            (price_made_new_high and not cvd_made_new_high) or
            (price_made_new_low  and not cvd_made_new_low)
        )

        return CVDResult(
            cvd_final=cvd_final, cvd_high=cvd_high, cvd_low=cvd_low,
            bias=bias, divergence=divergence
        )


# ─── Absorption Detector ──────────────────────────────────────────

class AbsorptionDetector:
    """
    Finds candles where large volume produced small price movement.
    These are institutional absorption zones — the market absorbing
    aggressive orders without moving price, signalling a likely reversal.

    threshold_vol_pct  : candle must be in top N% volume for the session
    threshold_body_pct : body/range ratio must be below this (small body = absorption)
    """

    def __init__(
        self,
        threshold_vol_pct:  float = 0.80,
        threshold_body_pct: float = 0.30,
    ):
        self.threshold_vol_pct  = threshold_vol_pct
        self.threshold_body_pct = threshold_body_pct

    def detect(self, df: pd.DataFrame) -> list[AbsorptionZone]:
        df   = df.copy()
        vol_cutoff  = df["volume"].quantile(self.threshold_vol_pct)
        df["range"] = df["high"] - df["low"]
        df["body"]  = (df["close"] - df["open"]).abs()
        df["body_ratio"] = np.where(df["range"] > 0, df["body"] / df["range"], 1.0)

        high_vol = df["volume"]     >= vol_cutoff
        small_body = df["body_ratio"] <= self.threshold_body_pct
        absorption_candles = df[high_vol & small_body]

        zones = []
        vol_max = df["volume"].max()

        for _, row in absorption_candles.iterrows():
            # Determine side: where did price close relative to range midpoint?
            mid = (row["high"] + row["low"]) / 2
            if row["close"] > mid:
                # closed upper half → sellers tried to push down, buyers absorbed
                side = "bid_absorption"
            else:
                # closed lower half → buyers tried to push up, sellers absorbed
                side = "ask_absorption"

            strength = float(row["volume"] / vol_max)
            zones.append(AbsorptionZone(
                price_high=float(row["high"]),
                price_low =float(row["low"]),
                volume    =float(row["volume"]),
                side      =side,
                strength  =strength,
            ))

        return sorted(zones, key=lambda z: z.strength, reverse=True)


# ─── Context Builder (main entry point) ───────────────────────────

class YesterdayContextBuilder:
    """
    Call build(df_yesterday) with yesterday's D1 or intraday candles.
    Returns a YesterdayContext used by SignalEngine to set bias and entry zone.
    """

    def __init__(self):
        self.vp_builder    = VolumeProfileBuilder()
        self.cvd_calc      = CVDCalculator()
        self.abs_detector  = AbsorptionDetector()

    def build(self, df_yesterday: pd.DataFrame) -> YesterdayContext:
        """
        df_yesterday : OHLCV DataFrame for yesterday's full session (any TF).
                       Must have columns: open, high, low, close, volume.
        """
        profile     = self.vp_builder.build(df_yesterday)
        cvd         = self.cvd_calc.calculate(df_yesterday)
        absorptions = self.abs_detector.detect(df_yesterday)

        yest_high  = float(df_yesterday["high"].max())
        yest_low   = float(df_yesterday["low"].min())
        yest_close = float(df_yesterday["close"].iloc[-1])

        # ── Combined Bias ─────────────────────────────────────────
        # Vote: volume profile bias + CVD bias + CVD divergence reversal
        votes_bull = sum([
            profile.bias == "bullish",
            cvd.bias     == "bullish",
            cvd.divergence and yest_close < profile.poc,  # bearish divergence → reversal up
        ])
        votes_bear = sum([
            profile.bias == "bearish",
            cvd.bias     == "bearish",
            cvd.divergence and yest_close > profile.poc,  # bullish divergence → reversal down
        ])

        if votes_bull > votes_bear:
            bias = "bullish"
        elif votes_bear > votes_bull:
            bias = "bearish"
        else:
            bias = "neutral"

        # ── Entry Zone ────────────────────────────────────────────
        # Longs: retrace to VAL–POC zone (institutional buy zone)
        # Shorts: retrace to POC–VAH zone (institutional sell zone)
        if bias == "bullish":
            entry_zone_high  = profile.poc
            entry_zone_low   = profile.val
            invalidation     = yest_low - (yest_high - yest_low) * 0.10
        elif bias == "bearish":
            entry_zone_high  = profile.vah
            entry_zone_low   = profile.poc
            invalidation     = yest_high + (yest_high - yest_low) * 0.10
        else:
            # Neutral: tighten to tight band around POC
            spread           = (profile.vah - profile.val) * 0.25
            entry_zone_high  = profile.poc + spread
            entry_zone_low   = profile.poc - spread
            invalidation     = 0.0

        # Refine entry zone with absorption zones if strong ones exist
        strong_absorptions = [z for z in absorptions if z.strength > 0.65]
        for zone in strong_absorptions[:2]:
            zone_mid = (zone.price_high + zone.price_low) / 2
            if bias == "bullish" and zone.side == "bid_absorption":
                # Strong buying absorption below POC → tighten entry to that zone
                entry_zone_low  = max(entry_zone_low,  zone.price_low)
                entry_zone_high = max(entry_zone_high, zone.price_high)
            elif bias == "bearish" and zone.side == "ask_absorption":
                entry_zone_high = min(entry_zone_high, zone.price_high)
                entry_zone_low  = min(entry_zone_low,  zone.price_low)

        return YesterdayContext(
            profile         = profile,
            cvd             = cvd,
            absorptions     = absorptions,
            bias            = bias,
            entry_zone_high = round(entry_zone_high, 5),
            entry_zone_low  = round(entry_zone_low,  5),
            invalidation    = round(invalidation,     5),
            high            = yest_high,
            low             = yest_low,
            close           = yest_close,
        )
