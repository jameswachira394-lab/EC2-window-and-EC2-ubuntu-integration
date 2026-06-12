"""
MT5 Execution Server  (Windows EC2)
────────────────────────────────────
FastAPI server that receives trade orders from the Ubuntu trading engine
via HTTP and executes them directly on MetaTrader5.

Endpoints:
    POST /buy          — place a BUY market order
    POST /sell         — place a SELL market order
    GET  /positions    — list open positions
    GET  /health       — server health check
    GET  /docs         — Swagger UI (automatic)

Run:
    python -m uvicorn main:app --host 0.0.0.0 --port 8000

Credentials (set as environment variables or in a .env file):
    MT5_LOGIN      = 10401216
    MT5_PASSWORD   = your_password
    MT5_SERVER     = FBS-Demo
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional

import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mt5_connector import connect, disconnect, get_filling_mode

# ── Logging ───────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("MT5Executor")

# ── Lifespan (replaces deprecated @app.on_event) ─────────────────

_mt5_ready = False   # global flag — False until MT5 connects


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to MT5 on startup, disconnect on shutdown."""
    global _mt5_ready
    try:
        connect()
        _mt5_ready = True
        logger.info("MT5 Execution Server ready.")
    except Exception as exc:
        logger.warning("MT5 not connected at startup: %s", exc)
        logger.warning("Server is running but orders will fail until MT5 is available.")
    yield
    # ── Shutdown ──
    disconnect()
    logger.info("MT5 Execution Server stopped.")


app = FastAPI(
    title="MT5 Execution Server",
    description=(
        "Executes Forex trades on MetaTrader5 on behalf of the Ubuntu trading engine.\n\n"
        "Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER environment variables before starting."
    ),
    version="1.1.0",
    lifespan=lifespan,
)


# ── Pydantic Models ───────────────────────────────────────────────

class Order(BaseModel):
    symbol:  str
    volume:  float
    entry:   Optional[float] = None    # ignored for market orders (price auto-set)
    sl:      Optional[float] = None
    tp1:     Optional[float] = None
    tp2:     Optional[float] = None    # reserved for future partial-close support
    tp3:     Optional[float] = None    # reserved for future partial-close support
    comment: Optional[str]  = "InstitutionalBot"


# ── Internal helpers ──────────────────────────────────────────────

def _guard_mt5():
    """Raise 503 if MT5 is not connected."""
    if mt5.account_info() is None:
        raise HTTPException(
            status_code=503,
            detail="MT5 is not connected. Check that MetaTrader5 is running and credentials are correct.",
        )


def _ensure_symbol(symbol: str):
    """
    Make sure the symbol is selected/visible in Market Watch.
    MT5 will not return tick data for a symbol that is not selected.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(status_code=400, detail=f"Symbol '{symbol}' not found in MT5.")
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            raise HTTPException(
                status_code=400,
                detail=f"Could not add '{symbol}' to Market Watch.",
            )


def _send_order(
    symbol:     str,
    volume:     float,
    order_type: int,
    sl:         Optional[float],
    tp1:        Optional[float],
    comment:    Optional[str],
) -> dict:
    """
    Build and send a market order to MT5.
    Automatically detects the correct filling mode for the symbol/broker.
    """
    _guard_mt5()
    _ensure_symbol(symbol)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot get live tick for '{symbol}'. Is the market open?",
        )

    price        = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
    filling_mode = get_filling_mode(symbol)

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       float(volume),
        "type":         order_type,
        "price":        price,
        "deviation":    20,
        "magic":        202400,
        "comment":      (comment or "InstitutionalBot")[:31],   # MT5 max comment length
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    if sl and sl > 0:
        request["sl"] = float(sl)
    if tp1 and tp1 > 0:
        request["tp"] = float(tp1)

    logger.info(
        "Sending order: %s %s %.2f lots @ %.5f  SL=%s  TP=%s  fill=%s",
        "BUY" if order_type == mt5.ORDER_TYPE_BUY else "SELL",
        symbol, volume, price, sl, tp1, filling_mode,
    )

    result = mt5.order_send(request)

    if result is None:
        last_err = mt5.last_error()
        raise HTTPException(
            status_code=500,
            detail=f"MT5 order_send returned None. Last error: {last_err}",
        )

    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if success:
        logger.info(
            "Order FILLED | ticket=%s symbol=%s volume=%.2f price=%.5f",
            result.order, symbol, result.volume, result.price,
        )
    else:
        logger.error(
            "Order FAILED | retcode=%s comment=%s",
            result.retcode, result.comment,
        )

    return {
        "status":  "FILLED" if success else "FAILED",
        "retcode": result.retcode,
        "order":   result.order,
        "volume":  result.volume,
        "price":   result.price,
        "bid":     tick.bid,
        "ask":     tick.ask,
        "comment": result.comment,
    }


# ── Endpoints ─────────────────────────────────────────────────────

@app.get("/health", summary="Health check")
def health():
    """
    Returns server status and MT5 connection details.
    Use this to verify the Windows server is reachable before trading.
    """
    info = mt5.account_info()
    if info is None:
        return {
            "status":        "degraded",
            "mt5_connected": False,
            "balance":       None,
            "message":       "MT5 not connected — check that MetaTrader5 is open and logged in.",
        }
    return {
        "status":        "ok",
        "mt5_connected": True,
        "account":       info.login,
        "balance":       info.balance,
        "equity":        info.equity,
        "currency":      info.currency,
        "server":        info.server,
        "leverage":      info.leverage,
    }


@app.post("/buy", summary="Place BUY market order")
def buy(order: Order):
    """
    Place a BUY (LONG) market order on MetaTrader5.

    - `symbol`  — e.g. "EURUSD"
    - `volume`  — lot size, e.g. 0.01
    - `sl`      — stop loss price (optional)
    - `tp1`     — take profit price (optional)
    """
    return _send_order(
        symbol     = order.symbol.upper(),
        volume     = order.volume,
        order_type = mt5.ORDER_TYPE_BUY,
        sl         = order.sl,
        tp1        = order.tp1,
        comment    = order.comment,
    )


@app.post("/sell", summary="Place SELL market order")
def sell(order: Order):
    """
    Place a SELL (SHORT) market order on MetaTrader5.

    - `symbol`  — e.g. "EURUSD"
    - `volume`  — lot size, e.g. 0.01
    - `sl`      — stop loss price (optional)
    - `tp1`     — take profit price (optional)
    """
    return _send_order(
        symbol     = order.symbol.upper(),
        volume     = order.volume,
        order_type = mt5.ORDER_TYPE_SELL,
        sl         = order.sl,
        tp1        = order.tp1,
        comment    = order.comment,
    )


@app.get("/ohlcv", summary="Fetch historical candles")
def ohlcv(symbol: str, timeframe: str, count: int = 300):
    """
    Fetch OHLCV historical data.
    `timeframe` should be a string like "M15", "H1", "H4", "D1".
    """
    _guard_mt5()
    _ensure_symbol(symbol.upper())
    
    mapping = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }
    tf_const = mapping.get(timeframe.upper(), mt5.TIMEFRAME_H1)
    
    rates = mt5.copy_rates_from_pos(symbol.upper(), tf_const, 0, count)
    if rates is None or len(rates) == 0:
        return []
    
    # Convert numpy recarray to list of dicts
    result = []
    for r in rates:
        result.append({
            "time": r['time'],
            "open": r['open'],
            "high": r['high'],
            "low": r['low'],
            "close": r['close'],
            "tick_volume": r['tick_volume'],
            "spread": r['spread'],
            "real_volume": r['real_volume']
        })
    return result


@app.get("/tick", summary="Fetch current tick")
def tick(symbol: str):
    """
    Fetch current bid/ask tick for a symbol.
    """
    _guard_mt5()
    _ensure_symbol(symbol.upper())
    
    t = mt5.symbol_info_tick(symbol.upper())
    if t is None:
        raise HTTPException(status_code=400, detail=f"Tick data unavailable for {symbol}")
        
    return {
        "time": t.time,
        "bid": t.bid,
        "ask": t.ask,
        "last": t.last,
        "volume": t.volume
    }


@app.get("/symbol_info", summary="Fetch symbol properties")
def symbol_info(symbol: str):
    """
    Fetch static/dynamic properties for a symbol.
    """
    _guard_mt5()
    info = mt5.symbol_info(symbol.upper())
    if info is None:
        raise HTTPException(status_code=400, detail=f"Symbol not found: {symbol}")
        
    return {
        "point": info.point,
        "digits": info.digits,
        "trade_contract_size": info.trade_contract_size,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "spread": info.spread,
        "visible": info.visible
    }



@app.get("/positions", summary="List open positions")
def positions(symbol: Optional[str] = None):
    """
    Return all currently open positions.
    Pass `?symbol=EURUSD` to filter by symbol.
    """
    _guard_mt5()

    pos = mt5.positions_get(symbol=symbol.upper()) if symbol else mt5.positions_get()
    if pos is None:
        return {"positions": [], "count": 0}

    return {
        "positions": [
            {
                "ticket":       p.ticket,
                "symbol":       p.symbol,
                "volume":       p.volume,
                "type":         "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                "open_price":   p.price_open,
                "current_price":p.price_current,
                "sl":           p.sl,
                "tp":           p.tp,
                "profit":       p.profit,
                "swap":         p.swap,
                "comment":      p.comment,
                "magic":        p.magic,
            }
            for p in pos
        ],
        "count": len(pos),
    }
