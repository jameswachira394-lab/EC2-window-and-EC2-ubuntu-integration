"""
Institutional Forex Trading Bot
────────────────────────────────
Main orchestrator — fetches market data from MT5Connector (simulation on
Ubuntu), generates signals, enforces risk rules, and delegates ALL order
execution to the Windows MT5 Execution Server via HTTP (ExecutionClient).

Run:
    python main.py                  # live / sim mode
    python main.py --scan-once      # single scan then exit
    python main.py --symbol EURUSD  # scan one pair only

Environment variable required on Ubuntu EC2:
    MT5_SERVER_URL=http://<windows-ec2-ip>:8000
"""

from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    SYMBOLS, TIMEFRAMES, RISK_PER_TRADE_PCT, LOG_DIR,
)
from connectors.mt5_connector import MT5Connector
from connectors.execution_client import ExecutionClient
from core.signal_engine import SignalEngine, format_signal
from core.risk_manager import RiskManager
from utils.signal_logger import SignalLogger
from utils.news_filter import load_news_events

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{LOG_DIR}/trading_bot.log"),
    ],
)
logger = logging.getLogger("TradingBot")


class TradingBot:

    def __init__(
        self,
        scan_interval: int  = 60,
        dry_run:       bool = False,
        symbols:       list = None,
    ):
        self.scan_interval = scan_interval
        self.dry_run       = dry_run
        self.symbols       = symbols or SYMBOLS

        self.mt5      = MT5Connector()        # data layer (sim on Ubuntu)
        self.executor = ExecutionClient()     # execution → Windows MT5 server
        self.logger   = SignalLogger()

        self.engine   = None
        self.risk_mgr = None

    def start(self, scan_once: bool = False):
        logger.info("=" * 60)
        logger.info("Institutional Forex Trading Bot Starting")
        logger.info("Dry-run: %s | Symbols: %s", self.dry_run, self.symbols)
        logger.info("=" * 60)

        if not self.mt5.connect():
            logger.error("Failed to connect to MT5. Exiting.")
            sys.exit(1)

        balance = self.mt5.get_account_balance()
        logger.info("Account balance: %.2f", balance)

        self.engine   = SignalEngine(account_balance=balance, risk_pct=RISK_PER_TRADE_PCT)
        self.risk_mgr = RiskManager(initial_balance=balance)

        try:
            if scan_once:
                self._scan_all()
            else:
                self._run_loop()
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
        finally:
            self.mt5.disconnect()
            logger.info("Bot stopped.")

    def _run_loop(self):
        logger.info("Entering main scan loop (interval=%ds). Ctrl+C to stop.", self.scan_interval)
        while True:
            self._scan_all()
            logger.info("Scan complete. Sleeping %ds...", self.scan_interval)
            time.sleep(self.scan_interval)

    def _scan_all(self):
        utc_now     = datetime.now(timezone.utc).replace(tzinfo=None)
        news_events = load_news_events()
        balance     = self.mt5.get_account_balance()

        self.engine.balance = balance

        ok, reason = self.risk_mgr.can_trade(balance)
        if not ok:
            logger.warning("TRADING HALTED: %s", reason)
            return

        for symbol in self.symbols:
            try:
                self._process_symbol(symbol, utc_now, news_events)
            except Exception as e:
                logger.exception("Error processing %s: %s", symbol, e)

    def _process_symbol(self, symbol, utc_now, news_events):
        df_m15 = self.mt5.get_ohlcv(symbol, "M15")
        df_h1  = self.mt5.get_ohlcv(symbol, "H1")
        df_h4  = self.mt5.get_ohlcv(symbol, "H4")

        if df_m15 is None or df_m15.empty or df_h1 is None or df_h1.empty or df_h4 is None or df_h4.empty:
            logger.warning("Skipping %s — missing data.", symbol)
            return

        signal = self.engine.evaluate(
            symbol     = symbol,
            df_m15     = df_m15,
            df_h1      = df_h1,
            df_h4      = df_h4,
            news_times = news_events,
            utc_now    = utc_now,
        )

        self.logger.log(signal)
        print("\n" + format_signal(signal))

        if signal.decision == "EXECUTE TRADE" and signal.direction != "NO_TRADE":
            self._execute_signal(signal)

    def _execute_signal(self, signal):
        if self.dry_run:
            logger.info("[DRY RUN] Would execute: %s %s conf=%d",
                        signal.pair, signal.direction, signal.confidence)
            return

        # Check existing position via Windows MT5 server
        existing = self.executor.get_open_positions(symbol=signal.pair)
        if existing:
            logger.info("Skipping %s — position already open.", signal.pair)
            return

        direction_str = "BUY" if signal.direction == "LONG" else "SELL"

        result = self.executor.place_order(
            symbol    = signal.pair,
            direction = direction_str,
            lot_size  = signal.lot_size,
            entry     = signal.entry,
            sl        = signal.stop_loss,
            tp1       = signal.tp1,
            tp2       = signal.tp2,
            tp3       = signal.tp3,
            comment   = f"InstitutionalBot conf={signal.confidence}",
        )

        logger.info("Order result: %s", result)


def main():
    parser = argparse.ArgumentParser(description="Institutional Forex Trading Bot")
    parser.add_argument("--scan-once", action="store_true")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--symbol",    type=str, default=None)
    parser.add_argument("--interval",  type=int, default=60)
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else None

    bot = TradingBot(
        scan_interval = args.interval,
        dry_run       = args.dry_run,
        symbols       = symbols,
    )
    bot.start(scan_once=args.scan_once)


if __name__ == "__main__":
    main()