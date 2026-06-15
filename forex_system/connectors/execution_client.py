"""
Execution Client
────────────────
Sends trade orders to the Windows MT5 Execution Server (mt5-executor)
via HTTP POST instead of calling MetaTrader5 directly.

The Ubuntu container NEVER imports MetaTrader5.
All actual order execution is delegated to:
    http://<MT5_SERVER_IP>:8000/buy   (or /sell)
"""

from __future__ import annotations
import logging
import os

import requests

logger = logging.getLogger(__name__)

# Read server IP from environment variable or fall back to config
_MT5_SERVER_URL = os.environ.get(
    "MT5_SERVER_URL",
    "http://10.0.1.153:8000",   # override with env var on Ubuntu EC2
).rstrip("/")

# Timeout for each HTTP request (seconds) 
_TIMEOUT = 10


class ExecutionClient:
    """
    Thin HTTP wrapper around the mt5-executor FastAPI server.
    Methods mirror MT5Connector.place_order / get_open_positions so that
    main.py can swap between the two with minimal changes.
    """

    def __init__(self, server_url: str = _MT5_SERVER_URL):
        self.server_url = server_url
        logger.info("ExecutionClient → MT5 server: %s", self.server_url)

    # ── Health check ───────────────────────────────────────────────

    def ping(self) -> bool:
        """Return True if the MT5 server is reachable."""
        try:
            r = requests.get(f"{self.server_url}/health", timeout=_TIMEOUT)
            return r.status_code == 200
        except requests.RequestException as exc:
            logger.warning("MT5 server unreachable: %s", exc)
            return False

    # ── Order execution ────────────────────────────────────────────

    def place_order(
        self,
        symbol:    str,
        direction: str,      # "BUY" | "SELL"
        lot_size:  float,
        entry:     float,
        sl:        float,
        tp1:       float,
        tp2:       float,
        tp3:       float,
        comment:   str = "InstitutionalBot",
    ) -> dict:
        """
        Delegates order execution to the Windows MT5 server.
        Returns a result dict with at minimum {"status": ..., "retcode": ...}.
        """
        endpoint = "buy" if direction == "BUY" else "sell"
        payload = {
            "symbol":    symbol,
            "volume":    lot_size,
            "entry":     entry,
            "sl":        sl,
            "tp1":       tp1,
            "tp2":       tp2,
            "tp3":       tp3,
            "comment":   comment,
        }

        logger.info(
            "→ Sending %s order for %s %.2f lots to %s/%s",
            direction, symbol, lot_size, self.server_url, endpoint,
        )

        try:
            response = requests.post(
                f"{self.server_url}/{endpoint}",
                json=payload,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            result = response.json()
            logger.info("← MT5 server response: %s", result)
            return result

        except requests.Timeout:
            logger.error("MT5 server timed out for %s %s", direction, symbol)
            return {"status": "ERROR", "reason": "timeout"}

        except requests.HTTPError as exc:
            logger.error("MT5 server HTTP error: %s", exc)
            return {"status": "ERROR", "reason": str(exc)}

        except requests.RequestException as exc:
            logger.error("MT5 server connection error: %s", exc)
            return {"status": "ERROR", "reason": str(exc)}

    # ── Position queries ───────────────────────────────────────────

    def get_open_positions(self, symbol: str | None = None) -> list:
        """
        Query the MT5 server for open positions.
        Returns an empty list if the server is unreachable (fail-safe).
        """
        params = {"symbol": symbol} if symbol else {}
        try:
            response = requests.get(
                f"{self.server_url}/positions",
                params=params,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            return response.json().get("positions", [])
        except requests.RequestException as exc:
            logger.warning("Could not query positions: %s", exc)
            return []
