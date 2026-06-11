"""
MT5 Connector — Windows EC2
────────────────────────────
Initialises and logs into MetaTrader5 using credentials loaded from
environment variables (or a .env file).

Environment variables (set in Windows EC2 or a .env file):
    MT5_LOGIN      — integer account number
    MT5_PASSWORD   — account password
    MT5_SERVER     — broker server name (e.g. "FBS-Demo")
    MT5_TIMEOUT    — optional, default 10000 ms
    MT5_PATH       — optional, path to terminal64.exe if not auto-detected
"""

import os
import logging
import MetaTrader5 as mt5

# Load .env file if present (python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # dotenv optional — env vars can be set directly on the OS

logger = logging.getLogger(__name__)


def connect() -> bool:
    """
    Initialise MT5 and log in.
    Reads credentials from environment variables.
    Raises Exception on failure so the caller can handle it.
    """
    login    = os.environ.get("MT5_LOGIN")
    password = os.environ.get("MT5_PASSWORD")
    server   = os.environ.get("MT5_SERVER")
    timeout  = int(os.environ.get("MT5_TIMEOUT", "10000"))
    path     = os.environ.get("MT5_PATH", None)   # e.g. "C:\\Program Files\\MetaTrader 5\\terminal64.exe"

    if not login or not password or not server:
        raise Exception(
            "Missing MT5 credentials. Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER "
            "as environment variables or in a .env file."
        )

    init_kwargs = {
        "login":    int(login),
        "password": password,
        "server":   server,
        "timeout":  timeout,
    }
    if path:
        init_kwargs["path"] = path

    logger.info("Connecting to MT5 | account=%s server=%s", login, server)

    if not mt5.initialize(**init_kwargs):
        error = mt5.last_error()
        raise Exception(f"MT5 initialization failed: {error}")

    info = mt5.account_info()
    if info is None:
        mt5.shutdown()
        raise Exception("MT5 initialized but could not retrieve account info.")

    logger.info(
        "MT5 connected | Account: %s | Balance: %.2f %s | Server: %s",
        info.login, info.balance, info.currency, info.server,
    )
    return True


def disconnect():
    """Gracefully shut down the MT5 connection."""
    mt5.shutdown()
    logger.info("MT5 disconnected.")


def get_filling_mode(symbol: str) -> int:
    """
    Return the filling mode supported by this symbol/broker.
    Tries FOK first, then IOC, then RETURN (Market).
    Defaults to IOC if symbol info unavailable.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC

    filling = info.filling_mode
    # filling_mode is a bitmask: 1=FOK, 2=IOC, 4=RETURN
    if filling & 1:    # FOK supported
        return mt5.ORDER_FILLING_FOK
    elif filling & 2:  # IOC supported
        return mt5.ORDER_FILLING_IOC
    else:              # RETURN (market execution)
        return mt5.ORDER_FILLING_RETURN
