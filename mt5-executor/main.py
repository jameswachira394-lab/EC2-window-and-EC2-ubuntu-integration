from fastapi import FastAPI
from pydantic import BaseModel
import MetaTrader5 as mt5

app = FastAPI()

class Order(BaseModel):
    symbol: str
    volume: float

@app.post("/buy")
def buy(order: Order):

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": order.symbol,
        "volume": order.volume,
        "type": mt5.ORDER_TYPE_BUY,
    }

    result = mt5.order_send(request)

    return {
        "retcode": result.retcode
    }
