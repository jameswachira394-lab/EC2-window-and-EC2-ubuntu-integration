"""
Backtester
──────────
Replay historical OHLCV data through the signal engine and
produce a performance report.

Usage:
    python backtest.py --symbol EURUSD --start 2024-01-01 --end 2024-12-31
    python backtest.py --csv data/EURUSD_M15.csv --timeframe M15
"""

from __future__ import annotations
import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import RISK_PER_TRADE_PCT
from core.signal_engine import SignalEngine, format_signal
from core.indicators import add_emas, add_atr, add_volume_ratio, get_swing_points

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("Backtester")


# ─── Simple walk-forward backtester ──────────────────────────────

class Backtester:

    def __init__(self, balance: float = 10_000.0, risk_pct: float = RISK_PER_TRADE_PCT):
        self.initial_balance = balance
        self.balance         = balance
        self.risk_pct        = risk_pct
        self.engine          = SignalEngine(balance, risk_pct)
        self.trades          = []

    def run_on_dataframe(
        self,
        df_m15: pd.DataFrame,
        symbol: str = "TEST",
        step:   int = 1,    # evaluate every N rows
    ):
        """
        Walk forward through df_m15.
        step: evaluate every `step` M15 candles (speeds up large datasets).
        """
        warmup = 60   # candles needed before meaningful indicators
        indices = range(warmup, len(df_m15), step)

        print(f"\nBacktest: {symbol} | {len(df_m15)} M15 candles | "
              f"{len(indices)} evaluations\n")

        for i in indices:
            ts = df_m15.index[i]
            m15_slice = df_m15.iloc[:i + 1]

            # Context for yesterday
            dates = pd.Series(m15_slice.index.date).unique()
            if len(dates) < 2:
                continue

            prev_date = dates[-2]
            df_yesterday = m15_slice[m15_slice.index.date == prev_date]

            if df_yesterday.empty:
                continue

            try:
                ctx = self.engine.build_yesterday_context(df_yesterday)
                sig = self.engine.evaluate(
                    symbol    = symbol,
                    df_m15    = m15_slice.tail(300),
                    ctx       = ctx,
                    utc_now   = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    news_times= [],
                )
            except Exception as e:
                continue

            if sig.direction == "NO_TRADE":
                continue

            # Simulate trade outcome using future M15 candles
            outcome = self._simulate_trade(sig, df_m15, i)
            if outcome:
                self.trades.append(outcome)
                self._update_balance(outcome["pnl_pct"])
                print(f"[{ts}] {sig.direction} | conf={sig.confidence} | "
                      f"result={outcome['result']} | "
                      f"pnl={outcome['pnl_pct']:+.2f}%")

        self._print_report(symbol)

    def _simulate_trade(self, sig, df_m15: pd.DataFrame, entry_idx: int) -> dict | None:
        """
        Walk forward candle by candle from entry to see if SL or TP1 is hit first.
        """
        future = df_m15.iloc[entry_idx + 1: entry_idx + 101]  # max 100 candles forward

        for _, candle in future.iterrows():
            if sig.direction == "LONG":
                if candle["low"]  <= sig.stop_loss:
                    pnl = -(sig.risk_pct if hasattr(sig, "risk_pct") else self.risk_pct)
                    return self._trade_record(sig, "SL_HIT", pnl)
                if candle["high"] >= sig.tp1:
                    rr  = sig.risk_reward
                    pnl = self.risk_pct * rr
                    return self._trade_record(sig, "TP1_HIT", pnl)
            else:  # SHORT
                if candle["high"] >= sig.stop_loss:
                    pnl = -self.risk_pct
                    return self._trade_record(sig, "SL_HIT", pnl)
                if candle["low"]  <= sig.tp1:
                    pnl = self.risk_pct * sig.risk_reward
                    return self._trade_record(sig, "TP1_HIT", pnl)

        return self._trade_record(sig, "TIMEOUT", 0.0)

    def _trade_record(self, sig, result: str, pnl_pct: float) -> dict:
        return {
            "timestamp":  sig.timestamp,
            "pair":       sig.pair,
            "direction":  sig.direction,
            "confidence": sig.confidence,
            "entry":      sig.entry,
            "sl":         sig.stop_loss,
            "tp1":        sig.tp1,
            "rr":         sig.risk_reward,
            "result":     result,
            "pnl_pct":    round(pnl_pct, 3),
        }

    def _update_balance(self, pnl_pct: float):
        self.balance *= (1 + pnl_pct / 100)

    def _print_report(self, symbol: str):
        if not self.trades:
            print("\nNo trades taken.")
            return

        df = pd.DataFrame(self.trades)
        wins        = df[df["result"] == "TP1_HIT"]
        losses      = df[df["result"] == "SL_HIT"]
        win_rate    = len(wins) / len(df) * 100 if len(df) else 0
        total_pnl   = df["pnl_pct"].sum()
        avg_win     = wins["pnl_pct"].mean()  if len(wins)   else 0
        avg_loss    = losses["pnl_pct"].mean() if len(losses) else 0
        profit_factor = (wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum())
                         if losses["pnl_pct"].sum() != 0 else float("inf"))

        # Drawdown
        cumulative = (1 + df["pnl_pct"] / 100).cumprod()
        roll_max   = cumulative.cummax()
        drawdown   = (cumulative - roll_max) / roll_max * 100
        max_dd     = drawdown.min()

        print("\n" + "=" * 55)
        print(f"  BACKTEST REPORT — {symbol}")
        print("=" * 55)
        print(f"  Total Trades:     {len(df)}")
        print(f"  Wins (TP1):       {len(wins)}")
        print(f"  Losses (SL):      {len(losses)}")
        print(f"  Timeouts:         {len(df) - len(wins) - len(losses)}")
        print(f"  Win Rate:         {win_rate:.1f}%")
        print(f"  Total PnL:        {total_pnl:+.2f}%")
        print(f"  Avg Win:          {avg_win:+.2f}%")
        print(f"  Avg Loss:         {avg_loss:+.2f}%")
        print(f"  Profit Factor:    {profit_factor:.2f}")
        print(f"  Max Drawdown:     {max_dd:.2f}%")
        print(f"  Final Balance:    ${self.balance:,.2f}  "
              f"(started ${self.initial_balance:,.2f})")
        print("=" * 55)

        # Save to CSV
        out = Path("logs/backtest_results.csv")
        df.to_csv(out, index=False)
        print(f"\n  Detailed results saved to {out}")


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Institutional Forex Backtester")
    parser.add_argument("--symbol",    default="EURUSD")
    parser.add_argument("--balance",   type=float, default=10_000.0)
    parser.add_argument("--step",      type=int,   default=4,
                        help="Evaluate every N M15 candles (default 4 = 1h)")
    args = parser.parse_args()

    # In simulation mode we generate synthetic data
    from connectors.mt5_connector import MT5Connector
    mt5 = MT5Connector()
    mt5.connect()

    print(f"Fetching data for {args.symbol}...")
    df_m15 = mt5.get_ohlcv(args.symbol, "M15", count=2000)

    bt = Backtester(balance=args.balance)
    bt.run_on_dataframe(df_m15, symbol=args.symbol, step=args.step)


if __name__ == "__main__":
    main()
