"""
Signal Logger
─────────────
Persists every evaluated signal to a CSV file for back-analysis.
"""

from __future__ import annotations
import csv
import logging
import os
from dataclasses import asdict
from pathlib import Path

from core.signal_engine import TradeSignal
from config.settings import SIGNAL_LOG_CSV

logger = logging.getLogger(__name__)

FIELDNAMES = [
    "timestamp", "pair", "direction", "confidence", "decision",
    "entry", "stop_loss", "tp1", "tp2", "tp3", "risk_reward", "lot_size",
    "trend_h4", "trend_h1", "trend_m15", "structure",
    "volume_ratio", "atr_current", "atr_expanding", "atr_compressed",
    "sweep_detected", "sweep_direction", "sweep_rejection",
    "breakout_confirmed", "breakout_direction",
    "rejection_reasons",
]


class SignalLogger:

    def __init__(self, csv_path: str = SIGNAL_LOG_CSV):
        self.path = Path(csv_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_header()

    def log(self, signal: TradeSignal):
        row = {
            "timestamp":          signal.timestamp,
            "pair":               signal.pair,
            "direction":          signal.direction,
            "confidence":         signal.confidence,
            "decision":           signal.decision,
            "entry":              signal.entry,
            "stop_loss":          signal.stop_loss,
            "tp1":                signal.tp1,
            "tp2":                signal.tp2,
            "tp3":                signal.tp3,
            "risk_reward":        signal.risk_reward,
            "lot_size":           signal.lot_size,
            "trend_h4":           signal.trend_h4,
            "trend_h1":           signal.trend_h1,
            "trend_m15":          signal.trend_m15,
            "structure":          signal.structure,
            "volume_ratio":       signal.volume_ratio,
            "atr_current":        signal.atr_current,
            "atr_expanding":      signal.atr_expanding,
            "atr_compressed":     signal.atr_compressed,
            "sweep_detected":     signal.sweep.get("detected", False),
            "sweep_direction":    signal.sweep.get("direction", ""),
            "sweep_rejection":    signal.sweep.get("rejection_strength", 0.0),
            "breakout_confirmed": signal.breakout.get("confirmed", False),
            "breakout_direction": signal.breakout.get("direction", ""),
            "rejection_reasons":  " | ".join(signal.rejection_log),
        }
        with open(self.path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)

    def _write_header(self):
        with open(self.path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
