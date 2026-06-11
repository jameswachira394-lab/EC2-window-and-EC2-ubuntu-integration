"""
MT5 Connector
─────────────
Wraps MetaTrader5 Python API for data fetching and order execution.
Requires: pip install MetaTrader5
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("[WARNING] MetaTrader5 package not installed. Running in SIMULATION mode.")

from config.settings import MT5_CONFIG, CANDLES_HISTORY

logger = logging.getLogger(__name__)

# Map string timeframe names → MT5 constants
_TF_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  60,
    "H4":  240,
    "D1":  1440,
}

def _tf_const(tf_str: str):
    """Return MT5 TIMEFRAME constant from string."""
    if not MT5_AVAILABLE:
        return _TF_MAP.get(tf_str, 60)
    mapping = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }
    return mapping.get(tf_str, mt5.TIMEFRAME_H1)


class MT5Connector:
    """
    Handles connection, data retrieval, and order management via MT5.
    """

    def __init__(self):
        self.connected = False
        self._sim_mode = not MT5_AVAILABLE

    # ── Connection ─────────────────────────────────────────────────

    def connect(self) -> bool:
        if self._sim_mode:
            logger.warning("MT5 not installed — simulation mode active.")
            self.connected = True
            return True

        if not mt5.initialize(
            login=MT5_CONFIG["login"],
            password=MT5_CONFIG["password"],
            server=MT5_CONFIG["server"],
            timeout=MT5_CONFIG["timeout"],
            portable=MT5_CONFIG["portable"],
        ):
            logger.error("MT5 initialization failed: %s", mt5.last_error())
            return False

        info = mt5.account_info()
        if info is None:
            logger.error("Could not retrieve account info.")
            mt5.shutdown()
            return False

        logger.info(
            "MT5 Connected | Account: %s | Balance: %.2f %s",
            info.login, info.balance, info.currency,
        )
        self.connected = True
        return True

    def disconnect(self):
        if not self._sim_mode and MT5_AVAILABLE:
            mt5.shutdown()
        self.connected = False
        logger.info("MT5 disconnected.")

    # ── Account Info ───────────────────────────────────────────────

    def get_account_balance(self) -> float:
        if self._sim_mode:
            return 10_000.0
        info = mt5.account_info()
        return info.balance if info else 0.0

    def get_account_equity(self) -> float:
        if self._sim_mode:
            return 10_000.0
        info = mt5.account_info()
        return info.equity if info else 0.0

    # ── OHLCV Data ─────────────────────────────────────────────────

    def get_ohlcv(self, symbol: str, timeframe: str, count: int = CANDLES_HISTORY) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV + tick volume from MT5.
        Returns DataFrame with columns: time, open, high, low, close, tick_volume.
        """
        if self._sim_mode:
            return self._sim_ohlcv(symbol, timeframe, count)

        rates = mt5.copy_rates_from_pos(symbol, _tf_const(timeframe), 0, count)
        if rates is None or len(rates) == 0:
            logger.warning("No data for %s %s", symbol, timeframe)
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df.set_index("time", inplace=True)
        return df

    def _sim_ohlcv(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Generate synthetic OHLCV for simulation / testing."""
        import numpy as np
        np.random.seed(abs(hash(symbol + timeframe)) % (2**31))
        close = 1.1000 + np.cumsum(np.random.randn(count) * 0.0005)
        high   = close + np.abs(np.random.randn(count) * 0.0003)
        low    = close - np.abs(np.random.randn(count) * 0.0003)
        open_  = close + np.random.randn(count) * 0.0002
        volume = np.random.randint(500, 3000, count).astype(float)
        idx    = pd.date_range(end=datetime.utcnow(), periods=count, freq="1h")
        df = pd.DataFrame({
            "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        }, index=idx)
        return df

    # ── Symbol Info ────────────────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        if self._sim_mode:
            return {"point": 0.00001, "digits": 5, "trade_contract_size": 100_000}
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return {
            "point":               info.point,
            "digits":              info.digits,
            "trade_contract_size": info.trade_contract_size,
            "volume_min":          info.volume_min,
            "volume_max":          info.volume_max,
            "volume_step":         info.volume_step,
            "spread":              info.spread,
        }

    def get_tick(self, symbol: str) -> Optional[dict]:
        if self._sim_mode:
            return {"bid": 1.10000, "ask": 1.10002, "time": datetime.utcnow()}
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": tick.bid, "ask": tick.ask,
                "time": datetime.utcfromtimestamp(tick.time)}

    # ── Order Execution ────────────────────────────────────────────

    def place_order(
        self,
        symbol:    str,
        direction: str,         # "BUY" | "SELL"
        lot_size:  float,
        entry:     float,
        sl:        float,
        tp1:       float,
        tp2:       float,
        tp3:       float,
        comment:   str = "InstitutionalBot",
    ) -> dict:
        """
        Places a market order on MT5.
        In sim mode prints the order and returns a fake result.
        """
        if self._sim_mode:
            result = {
                "status":    "SIMULATED",
                "symbol":    symbol,
                "direction": direction,
                "lot_size":  lot_size,
                "entry":     entry,
                "sl":        sl,
                "tp1":       tp1,
                "tp2":       tp2,
                "tp3":       tp3,
            }
            logger.info("[SIM ORDER] %s", result)
            return result

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        # Primary order (full lot, first TP)
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot_size,
            "type":         order_type,
            "price":        entry,
            "sl":           sl,
            "tp":           tp1,
            "deviation":    20,
            "magic":        202400,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("Order failed: %s — %s", result.retcode, result.comment)
            return {"status": "FAILED", "retcode": result.retcode, "comment": result.comment}

        logger.info(
            "Order placed | %s %s %s lots @ %.5f SL:%.5f TP1:%.5f",
            direction, symbol, lot_size, entry, sl, tp1,
        )
        return {
            "status":  "FILLED",
            "ticket":  result.order,
            "symbol":  symbol,
            "direction": direction,
            "volume":  lot_size,
            "entry":   result.price,
        }

    def get_open_positions(self, symbol: Optional[str] = None) -> list:
        if self._sim_mode:
            return []
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if positions is None:
            return []
        return list(positions)

    def close_position(self, ticket: int) -> bool:
        if self._sim_mode:
            logger.info("[SIM] Closing position ticket %d", ticket)
            return True
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False
        pos = position[0]
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         order_type,
            "position":     ticket,
            "price":        price,
            "deviation":    20,
            "magic":        202400,
            "comment":      "Close by bot",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
