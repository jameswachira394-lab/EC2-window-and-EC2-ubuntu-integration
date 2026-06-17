"""
Three Strikes Reversal Strategy
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config.settings import (
    MIN_RR,
    TP1_ATR_MULT,
    TP2_ATR_MULT,
    MIN_CONFIDENCE,
    ALLOWED_SESSION_HOURS_UTC,
    NEWS_BLACKOUT_MINUTES,
)
from core.indicators import (
    add_atr,
    get_swing_points,
    add_emas,
    classify_trend_ema,
)

logger = logging.getLogger(__name__)

@dataclass
class TradeSignal:
    pair:            str
    direction:       str        # LONG | SHORT | NO_TRADE
    confidence:      int
    entry:           float
    stop_loss:       float
    tp1:             float
    tp2:             float = 0.0
    tp3:             float = 0.0
    risk_reward:     float
    lot_size:        float
    decision:        str        # EXECUTE TRADE | REVIEW MANUALLY | NO TRADE
    
    # Three strikes extras
    swing_1:           float = 0.0
    swing_2:           float = 0.0
    swing_3:           float = 0.0
    trendline_slope:   float = 0.0
    trendline_r2:      float = 0.0
    third_touch_price: float = 0.0
    
    rejection_log:   list  = field(default_factory=list)
    timestamp:       str   = ""


def fit_trendline(points: list[tuple[int, float]]) -> tuple[float, float, float]:
    """
    Fits a linear trendline through the given (x, y) points using numpy.polyfit.
    Returns (slope, intercept, r_squared).
    """
    if len(points) < 2:
        return 0.0, 0.0, 0.0
    x = np.array([p[0] for p in points])
    y = np.array([p[1] for p in points])
    
    # deg=1 for linear fit
    slope, intercept = np.polyfit(x, y, 1)
    
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 1.0
    
    return slope, intercept, r_squared

def find_third_touch(df: pd.DataFrame, trendline_slope: float, trendline_intercept: float, atr: float, mode: str) -> tuple[bool, float]:
    """
    Checks if the current price touches the projected trendline within 1x ATR.
    mode = 'high' | 'low'
    Returns (touched: bool, projected_price: float)
    """
    if df.empty:
        return False, 0.0
    
    current_idx = len(df) - 1
    projected_price = trendline_slope * current_idx + trendline_intercept
    
    # Use close price to check distance to projected trendline
    current_price = df.iloc[-1]['close']
    touched = abs(current_price - projected_price) <= atr
    return touched, projected_price


class SignalEngine:
    def __init__(self, account_balance: float = 10_000.0, risk_pct: float = 1.0, use_stop_order: bool = False):
        self.balance = account_balance
        self.risk_pct = risk_pct
        self.use_stop_order = use_stop_order

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = add_atr(df)
        df = get_swing_points(df)
        df = add_emas(df)
        # Calculate volume rolling mean for volume checking
        df['vol_ma'] = df['volume'].rolling(20).mean()
        return df

    def _no_trade(self, symbol: str, rejection_log: list, utc_now: datetime) -> TradeSignal:
        return TradeSignal(
            pair=symbol,
            direction="NO_TRADE",
            confidence=0,
            entry=0.0,
            stop_loss=0.0,
            tp1=0.0,
            tp2=0.0,
            tp3=0.0,
            risk_reward=0.0,
            lot_size=0.0,
            decision="NO TRADE",
            rejection_log=rejection_log,
            timestamp=utc_now.strftime("%Y-%m-%d %H:%M:%S UTC") if utc_now else ""
        )

    def evaluate(self, symbol: str, df_m15: pd.DataFrame, df_h1: pd.DataFrame, df_h4: pd.DataFrame, news_times: list = None, utc_now: datetime = None) -> TradeSignal:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        if news_times is None:
            news_times = []
            
        rejection_log = []

        # --- GATES ---
        if utc_now.hour not in ALLOWED_SESSION_HOURS_UTC:
            rejection_log.append("Outside allowed session hours")
            return self._no_trade(symbol, rejection_log, utc_now)
            
        # News blackout
        news_window = NEWS_BLACKOUT_MINUTES * 60
        for nt in news_times:
            if abs((utc_now - nt).total_seconds()) <= news_window:
                rejection_log.append("News blackout active")
                return self._no_trade(symbol, rejection_log, utc_now)

        # Prep M15
        df_m15 = self._prepare(df_m15)
        df_h1 = add_emas(df_h1)
        df_h4 = add_emas(df_h4)
        
        h1_trend = classify_trend_ema(df_h1)
        h4_trend = classify_trend_ema(df_h4)
        
        current_idx = len(df_m15) - 1
        current_bar = df_m15.iloc[-1]
        current_close = current_bar['close']
        atr_val = current_bar['atr']
        
        # Identify Swings
        sh_indices = np.where(df_m15['swing_high'])[0]
        sl_indices = np.where(df_m15['swing_low'])[0]
        
        # Variables to populate
        direction = "NO_TRADE"
        swing_1 = 0.0
        swing_2 = 0.0
        swing_3 = current_close
        tp2 = 0.0
        tp3 = 0.0
        trendline_slope = 0.0
        trendline_intercept = 0.0
        trendline_r2 = 0.0
        projected = 0.0
        
        # Determine direction based on strictly descending lows or ascending highs
        # BUY Setup
        if len(sl_indices) >= 2:
            idx1, idx2 = sl_indices[-2], sl_indices[-1]
            p1, p2 = df_m15['low'].iloc[idx1], df_m15['low'].iloc[idx2]
            if p2 < p1: # Strictly descending
                slope, intercept, r2 = fit_trendline([(idx1, p1), (idx2, p2)])
                touched, proj = find_third_touch(df_m15, slope, intercept, atr_val, mode='low')
                if touched:
                    direction = "LONG"
                    swing_1, swing_2 = p1, p2
                    trendline_slope, trendline_intercept, trendline_r2 = slope, intercept, r2
                    projected = proj

        # SELL Setup
        if direction == "NO_TRADE" and len(sh_indices) >= 2:
            idx1, idx2 = sh_indices[-2], sh_indices[-1]
            p1, p2 = df_m15['high'].iloc[idx1], df_m15['high'].iloc[idx2]
            if p2 > p1: # Strictly ascending
                slope, intercept, r2 = fit_trendline([(idx1, p1), (idx2, p2)])
                touched, proj = find_third_touch(df_m15, slope, intercept, atr_val, mode='high')
                if touched:
                    direction = "SHORT"
                    swing_1, swing_2 = p1, p2
                    trendline_slope, trendline_intercept, trendline_r2 = slope, intercept, r2
                    projected = proj

        if direction == "NO_TRADE":
            rejection_log.append("No valid Three Strikes setup (missing 3rd touch or swings)")
            return self._no_trade(symbol, rejection_log, utc_now)

        # Calculate TP / SL
        entry = current_close
        pip_size = 0.01 if 'JPY' in symbol else 0.0001
        
        if direction == "LONG":
            if self.use_stop_order:
                stop_loss = current_bar['low'] - (10 * pip_size)
            else:
                stop_loss = swing_2 - (15 * pip_size)
            
            # Nearest swing high above entry
            sh_prices = df_m15['high'].iloc[sh_indices].values
            above_entry = [p for p in sh_prices if p > entry]
            tp1 = above_entry[-1] if above_entry else entry + atr_val * TP1_ATR_MULT
            
        else: # SHORT
            if self.use_stop_order:
                stop_loss = current_bar['high'] + (10 * pip_size)
            else:
                stop_loss = swing_2 + (15 * pip_size)
                
            # Nearest swing low below entry
            sl_prices = df_m15['low'].iloc[sl_indices].values
            below_entry = [p for p in sl_prices if p < entry]
            tp1 = below_entry[-1] if below_entry else entry - atr_val * TP1_ATR_MULT

        risk = abs(entry - stop_loss)
        reward = abs(tp1 - entry)
        rr = round(reward / risk, 2) if risk > 0 else 0.0
        
        if rr < MIN_RR:
            rejection_log.append(f"R:R {rr:.2f} below minimum 1:{MIN_RR}")
            return self._no_trade(symbol, rejection_log, utc_now)

        # SCORING
        score = 0
        checks = {}
        
        # 1. H4 trend aligned
        if (direction == "LONG" and h4_trend == "bearish") or (direction == "SHORT" and h4_trend == "bullish"):
            # Wait, prompt says: "H4 and H1 EMAs must both be bearish - confirms we are in a downtrend". For BUY.
            # So if direction is LONG, we are in a downtrend, so trend should be BEARISH!
            score += 20
            checks['h4_aligned'] = True
        else:
            checks['h4_aligned'] = False
            
        # 2. H1 trend aligned
        if (direction == "LONG" and h1_trend == "bearish") or (direction == "SHORT" and h1_trend == "bullish"):
            score += 15
            checks['h1_aligned'] = True
        else:
            checks['h1_aligned'] = False
            
        # 3. Trendline R2 >= 0.85
        if trendline_r2 >= 0.85:
            score += 20
            checks['r2_high'] = True
        else:
            checks['r2_high'] = False
            
        # 4. Third touch within 0.5x ATR
        if abs(current_close - projected) <= (0.5 * atr_val):
            score += 15
            checks['tight_touch'] = True
        else:
            checks['tight_touch'] = False
            
        # 5. Volume at 3rd touch >= 1.2x rolling mean
        if current_bar['volume'] >= 1.2 * current_bar['vol_ma']:
            score += 10
            checks['high_vol'] = True
        else:
            checks['high_vol'] = False
            
        # 6. ATR expanding
        if current_bar['atr'] > df_m15.iloc[-2]['atr']:
            score += 10
            checks['atr_expanding'] = True
        else:
            checks['atr_expanding'] = False
            
        # 7. Swing points strictly aligned & Candle Distance penalty
        aligned = False
        dist_ok = True
        if direction == "LONG":
            if swing_1 > swing_2 > current_close:
                aligned = True
            if (idx2 - idx1 < 3) or (current_idx - idx2 < 3):
                dist_ok = False
        else:
            if swing_1 < swing_2 < current_close:
                aligned = True
            if (idx2 - idx1 < 3) or (current_idx - idx2 < 3):
                dist_ok = False
                
        if aligned:
            score += 10
        if not dist_ok:
            score -= 10
            
        checks['strictly_aligned'] = aligned
        checks['dist_ok'] = dist_ok
        
        score = min(100, max(0, score)) # Cap at 100
        
        if score >= 75:
            decision = "EXECUTE TRADE"
        elif score >= 55:
            decision = "REVIEW MANUALLY"
        else:
            decision = "NO TRADE"

        # Lot size
        risk_amount = self.balance * self.risk_pct / 100.0
        pips_at_risk = risk / pip_size
        lot_size = max(0.01, round(risk_amount / (pips_at_risk * 10.0), 2)) if pips_at_risk > 0 else 0.01

        # We will pack checks into rejection_log for format_signal to display ✔/✘
        for k, v in checks.items():
            if v:
                rejection_log.append(f"✔ {k}")
            else:
                rejection_log.append(f"✘ {k}")

        return TradeSignal(
            pair=symbol,
            direction=direction,
            confidence=score,
            entry=round(entry, 5),
            stop_loss=round(stop_loss, 5),
            tp1=round(tp1, 5),
            tp2=round(tp2, 5),
            tp3=round(tp3, 5),
            risk_reward=rr,
            lot_size=lot_size,
            decision=decision,
            swing_1=round(swing_1, 5),
            swing_2=round(swing_2, 5),
            swing_3=round(swing_3, 5),
            trendline_slope=trendline_slope,
            trendline_r2=trendline_r2,
            third_touch_price=round(projected, 5),
            rejection_log=rejection_log,
            timestamp=utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")
        )

def format_signal(sig: TradeSignal) -> str:
    lines = [
        "=" * 56,
        f"PAIR:              {sig.pair}",
        f"DIRECTION:         {sig.direction}",
        f"CONFIDENCE:        {sig.confidence}/100",
        f"TIMESTAMP:         {sig.timestamp}",
        "-" * 56,
        "THREE STRIKES SETUP:",
        f"  Swing 1 Price:   {sig.swing_1}",
        f"  Swing 2 Price:   {sig.swing_2}",
        f"  Swing 3 Price:   {sig.swing_3}",
        f"  Projected Touch: {sig.third_touch_price}",
        f"  Trendline Slope: {sig.trendline_slope:.6f}",
        f"  Trendline R²:    {sig.trendline_r2:.4f}",
        "-" * 56,
        "SCORING CRITERIA:"
    ]
    
    # Extract criteria from rejection_log
    reasons = []
    for log in sig.rejection_log:
        if log.startswith("✔") or log.startswith("✘"):
            lines.append(f"  {log}")
        else:
            reasons.append(log)
            
    lines.extend([
        "-" * 56,
        f"ENTRY:             {sig.entry}",
        f"STOP LOSS:         {sig.stop_loss}",
        f"TP1:               {sig.tp1}",
        f"TP2:               {sig.tp2}",
        f"TP3:               {sig.tp3}",
        f"RISK REWARD:       1:{sig.risk_reward}",
        f"LOT SIZE:          {sig.lot_size}",
        "-" * 56,
        f"DECISION:          {sig.decision}",
        "=" * 56,
    ])
    
    if reasons:
        lines += ["NOTES:"] + [f"  • {r}" for r in reasons]
        
    return "\n".join(lines)