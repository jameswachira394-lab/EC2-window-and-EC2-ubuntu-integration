"""
MT5 Execution Server  (Windows EC2 only)
────────────────────────────────────────
FastAPI server that runs on the Windows machine with MetaTrader5 installed.
Exposes REST endpoints so the Ubuntu bot can:
  • fetch OHLCV data
  • place / close orders
  • query open positions
  • check account balance

Install & run (Windows CMD / PowerShell):
    pip install fastapi uvicorn MetaTrader5 pandas numpy requests
    set MT5_LOGIN=12345678
    set MT5_PASSWORD=YourPassword
    set MT5_SERVER=YourBroker-Live
    python mt5_server.py

The server listens on 0.0.0.0:8000 by default.
Open Windows Firewall inbound rule for TCP 8000 so the Ubuntu EC2 can reach it.

FIX APPLIED:
    All OHLCV data is serialised with df.to_dict(orient="records") then
    each value is cast through _clean() which converts numpy scalar types
    (int64, float64, etc.) to native Python ints/floats before FastAPI
    touches them.  This eliminates the:
        ValueError: [TypeError("'numpy.int64' object is not iterable"), ...]
    error seen in uvicorn logs.
"""

from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    import MetaTrader5 as mt5
except ImportError:
    raise SystemExit("MetaTrader5 package not found. Run: pip install MetaTrader5")

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("MT5Server")

# ── Config from environment ────────────────────────────────────────
MT5_LOGIN    = int(os.environ.get("MT5_LOGIN",    "0"))
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER   = os.environ.get("MT5_SERVER",   "")
HOST         = os.environ.get("HOST", "0.0.0.0")
PORT         = int(os.environ.get("PORT", "8000"))

# ── Timeframe map ──────────────────────────────────────────────────
TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

# ── FastAPI app ────────────────────────────────────────────────────
app = FastAPI(title="MT5 Execution Server", version="1.1.0")


# ─── Numpy serialization fix ──────────────────────────────────────

def _clean(value):
    """
    Recursively convert numpy scalar types to native Python types.

    This is the core fix for:
        ValueError: [TypeError("'numpy.int64' object is not iterable"), ...]

    FastAPI's jsonable_encoder does not know how to handle numpy scalars
    returned inside plain dicts/lists. Converting them here — before FastAPI
    touches the data — resolves the 500 errors on all /ohlcv responses.
    """
    if isinstance(value, (np.integer,)):          # np.int8/16/32/64 → int
        return int(value)
    if isinstance(value, (np.floating,)):         # np.float16/32/64 → float
        return float(value)
    if isinstance(value, (np.bool_,)):            # np.bool_ → bool
        return bool(value)
    if isinstance(value, (np.ndarray,)):          # arrays → list (recursive)
        return [_clean(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None                               # JSON has no NaN/Inf
    return value


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """
    Convert a DataFrame to a list of clean Python dicts safe for JSON.
    Resets index so the DatetimeIndex becomes a 'time' column (as ISO string).
    """
    df = df.reset_index()
    # Ensure the time column is a plain ISO string
    if "time" in df.columns:
        df["time"] = df["time"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    raw_records = df.to_dict(orient="records")
    return [_clean(row) for row in raw_records]


# ─── MT5 connection lifecycle ─────────────────────────────────────

@app.on_event("startup")
def startup():
    if not mt5.initialize(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    ):
        logger.error("MT5 init failed: %s", mt5.last_error())
        raise SystemExit("Cannot connect to MetaTrader5")
    info = mt5.account_info()
    logger.info(
        "MT5 connected | account=%s | balance=%.2f %s",
        info.login, info.balance, info.currency,
    )


@app.on_event("shutdown")
def shutdown():
    mt5.shutdown()
    logger.info("MT5 disconnected.")


# ─── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/account")
def account():
    info = mt5.account_info()
    if info is None:
        raise HTTPException(500, "Could not fetch account info")
    return {
        "login":    int(info.login),
        "balance":  float(info.balance),
        "equity":   float(info.equity),
        "margin":   float(info.margin),
        "currency": str(info.currency),
        "leverage": int(info.leverage),
    }


@app.get("/ohlcv")
def ohlcv(
    symbol:    str = Query(...),
    timeframe: str = Query("H1"),
    count:     int = Query(300),
):
    """
    Fetch OHLCV + tick_volume from MT5 and return as JSON.

    ── THE FIX ──
    MT5's copy_rates_from_pos returns a numpy structured array.
    Converting it to a DataFrame and then to dicts via _df_to_records()
    ensures every value is a native Python type before FastAPI serialises
    it — eliminating the numpy.int64 TypeError that caused 500 errors.
    """
    tf = TF_MAP.get(timeframe.upper())
    if tf is None:
        raise HTTPException(400, f"Unknown timeframe: {timeframe}")

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        raise HTTPException(404, f"No data for {symbol} {timeframe}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    df.set_index("time", inplace=True)

    # ← CRITICAL: _df_to_records converts all numpy types to Python natives
    records = _df_to_records(df)

    return JSONResponse(content={"symbol": symbol, "timeframe": timeframe, "data": records})


@app.get("/positions")
def positions(symbol: Optional[str] = Query(None)):
    raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if raw is None:
        return {"positions": []}
    result = []
    for p in raw:
        result.append(_clean({
            "ticket":     p.ticket,
            "symbol":     p.symbol,
            "type":       p.type,
            "volume":     p.volume,
            "price_open": p.price_open,
            "sl":         p.sl,
            "tp":         p.tp,
            "profit":     p.profit,
            "comment":    p.comment,
        }))
    return {"positions": result}


@app.post("/positions/{ticket}/close")
def close_position(ticket: int):
    pos_list = mt5.positions_get(ticket=ticket)
    if not pos_list:
        raise HTTPException(404, f"Position {ticket} not found")
    pos = pos_list[0]
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       pos.symbol,
        "volume":       float(pos.volume),
        "type":         order_type,
        "position":     ticket,
        "price":        float(price),
        "deviation":    20,
        "magic":        202400,
        "comment":      "Close by bot",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    return {
        "success": result.retcode == mt5.TRADE_RETCODE_DONE,
        "retcode": int(result.retcode),
        "comment": str(result.comment),
    }


# ─── Order placement ──────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol:    str
    direction: str     # "BUY" | "SELL"
    lot_size:  float
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    tp3:       float
    comment:   str = "InstitutionalBot"


@app.post("/order")
def place_order(req: OrderRequest):
    order_type = mt5.ORDER_TYPE_BUY if req.direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       req.symbol,
        "volume":       float(req.lot_size),
        "type":         order_type,
        "price":        float(req.entry),
        "sl":           float(req.sl),
        "tp":           float(req.tp1),   # primary TP; TP2/TP3 managed separately
        "deviation":    20,
        "magic":        202400,
        "comment":      req.comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(
            status_code=400,
            detail={
                "status":  "FAILED",
                "retcode": int(result.retcode),
                "comment": str(result.comment),
            },
        )

    logger.info(
        "Order filled | %s %s %.2f lots @ %.5f | ticket=%d",
        req.direction, req.symbol, req.lot_size, result.price, result.order,
    )

    return {
        "status":    "FILLED",
        "ticket":    int(result.order),
        "symbol":    req.symbol,
        "direction": req.direction,
        "volume":    float(result.volume),
        "entry":     float(result.price),
        "sl":        float(req.sl),
        "tp1":       float(req.tp1),
        "tp2":       float(req.tp2),
        "tp3":       float(req.tp3),
    }


# ─── Entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("mt5_server:app", host=HOST, port=PORT, reload=False)
