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
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import MetaTrader5 as mt5
from mt5_connector import connect

app = FastAPI(
    title="MT5 Execution Server",
    description="Executes Forex trades on MetaTrader5 on behalf of the Ubuntu trading engine.",
    version="1.0.0",
)

# ── Initialize MT5 on startup ─────────────────────────────────────

@app.on_event("startup")
def startup():
    try:
        connect()
        print("[MT5] Connected successfully.")
    except Exception as e:
        print(f"[MT5] WARNING: Could not connect — {e}")
        print("[MT5] Server will start but orders will fail until MT5 is running.")


# ── Pydantic Models ───────────────────────────────────────────────

class Order(BaseModel):
    symbol:  str
    volume:  float
    entry:   Optional[float] = None   # ignored for market orders (price auto)
    sl:      Optional[float] = None
    tp1:     Optional[float] = None
    tp2:     Optional[float] = None   # reserved for future use
    tp3:     Optional[float] = None   # reserved for future use
    comment: Optional[str]  = "InstitutionalBot"


# ── Internal helper ───────────────────────────────────────────────

def _send_order(symbol: str, volume: float, order_type, sl, tp1, comment) -> dict:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise HTTPException(status_code=400, detail=f"Cannot get tick for {symbol}")

    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       volume,
        "type":         order_type,
        "price":        price,
        "deviation":    20,
        "magic":        202400,
        "comment":      comment or "InstitutionalBot",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    if sl:
        request["sl"] = sl
    if tp1:
        request["tp"] = tp1

    result = mt5.order_send(request)

    if result is None:
        raise HTTPException(status_code=500, detail="MT5 returned None — is MT5 running?")

    return {
        "status":   "FILLED" if result.retcode == mt5.TRADE_RETCODE_DONE else "FAILED",
        "retcode":  result.retcode,
        "order":    result.order,
        "volume":   result.volume,
        "price":    result.price,
        "comment":  result.comment,
    }


# ── Endpoints ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Server health check."""
    info = mt5.account_info()
    return {
        "status":  "ok",
        "mt5_connected": info is not None,
        "balance": info.balance if info else None,
    }


@app.post("/buy")
def buy(order: Order):
    """Place a BUY market order."""
    return _send_order(
        symbol     = order.symbol,
        volume     = order.volume,
        order_type = mt5.ORDER_TYPE_BUY,
        sl         = order.sl,
        tp1        = order.tp1,
        comment    = order.comment,
    )


@app.post("/sell")
def sell(order: Order):
    """Place a SELL market order."""
    return _send_order(
        symbol     = order.symbol,
        volume     = order.volume,
        order_type = mt5.ORDER_TYPE_SELL,
        sl         = order.sl,
        tp1        = order.tp1,
        comment    = order.comment,
    )


@app.get("/positions")
def positions(symbol: Optional[str] = None):
    """Return currently open positions."""
    pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if pos is None:
        return {"positions": []}

    return {
        "positions": [
            {
                "ticket":  p.ticket,
                "symbol":  p.symbol,
                "volume":  p.volume,
                "type":    "BUY" if p.type == 0 else "SELL",
                "profit":  p.profit,
                "sl":      p.sl,
                "tp":      p.tp,
            }
            for p in pos
        ]
    }
