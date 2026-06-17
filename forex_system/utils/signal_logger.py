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
    "swing_1", "swing_2", "swing_3", "trendline_slope", "trendline_r2",
    "third_touch_price", "rejection_reasons",
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
            "swing_1":            signal.swing_1,
            "swing_2":            signal.swing_2,
            "swing_3":            signal.swing_3,
            "trendline_slope":    signal.trendline_slope,
            "trendline_r2":       signal.trendline_r2,
            "third_touch_price":  signal.third_touch_price,
            "rejection_reasons":  " | ".join(signal.rejection_log),
        }
        with open(self.path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)

    def _write_header(self):
        with open(self.path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
