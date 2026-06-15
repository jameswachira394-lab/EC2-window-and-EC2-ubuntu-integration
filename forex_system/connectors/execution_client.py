"""
Execution Client
────────────────
HTTP client that delegates ALL order execution and position queries
to the Windows MT5 Execution Server running on the Windows EC2 instance.

Set the server URL via environment variable:
    export MT5_SERVER_URL=http://<windows-ec2-ip>:8000

The Windows server must be running mt5_server.py (FastAPI + MetaTrader5).
"""

from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Server URL ─────────────────────────────────────────────────────
_DEFAULT_URL = "http://localhost:8000"
SERVER_URL = os.environ.get("MT5_SERVER_URL", _DEFAULT_URL).rstrip("/")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("'requests' not installed — ExecutionClient running in stub mode.")


class ExecutionClient:
    """
    Sends trade instructions to the Windows MT5 server via HTTP.
    Falls back to stub (log-only) mode if requests is not installed
    or the server is unreachable.
    """

    def __init__(self, server_url: str = SERVER_URL, timeout: int = 10):
        self.base_url = server_url.rstrip("/")
        self.timeout  = timeout
        self._stub    = not REQUESTS_AVAILABLE
        logger.info("ExecutionClient → %s%s",
                    self.base_url,
                    " [STUB MODE]" if self._stub else "")

    # ── Health check ───────────────────────────────────────────────

    def ping(self) -> bool:
        """Returns True if the Windows MT5 server is reachable."""
        if self._stub:
            return False
        try:
            r = requests.get(f"{self.base_url}/health", timeout=self.timeout)
            return r.status_code == 200
        except Exception as e:
            logger.warning("MT5 server unreachable: %s", e)
            return False

    # ── Order placement ────────────────────────────────────────────

    def place_order(
        self,
        symbol:    str,
        direction: str,     # "BUY" | "SELL"
        lot_size:  float,
        entry:     float,
        sl:        float,
        tp1:       float,
        tp2:       float,
        tp3:       float,
        comment:   str = "InstitutionalBot",
    ) -> dict:
        payload = {
            "symbol":    symbol,
            "direction": direction,
            "lot_size":  lot_size,
            "entry":     entry,
            "sl":        sl,
            "tp1":       tp1,
            "tp2":       tp2,
            "tp3":       tp3,
            "comment":   comment,
        }

        if self._stub:
            logger.info("[STUB] place_order: %s", payload)
            return {"status": "STUB", **payload}

        try:
            r = requests.post(
                f"{self.base_url}/order",
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            result = r.json()
            logger.info("Order placed via MT5 server: %s", result)
            return result
        except Exception as e:
            logger.error("place_order failed: %s", e)
            return {"status": "ERROR", "message": str(e)}

    # ── Position queries ───────────────────────────────────────────

    def get_open_positions(self, symbol: Optional[str] = None) -> list:
        if self._stub:
            return []
        try:
            params = {"symbol": symbol} if symbol else {}
            r = requests.get(
                f"{self.base_url}/positions",
                params=params,
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("positions", [])
        except Exception as e:
            logger.error("get_open_positions failed: %s", e)
            return []

    def close_position(self, ticket: int) -> bool:
        if self._stub:
            logger.info("[STUB] close_position ticket=%d", ticket)
            return True
        try:
            r = requests.post(
                f"{self.base_url}/positions/{ticket}/close",
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("success", False)
        except Exception as e:
            logger.error("close_position failed: %s", e)
            return False

    # ── Account info ───────────────────────────────────────────────

    def get_account_info(self) -> dict:
        if self._stub:
            return {"balance": 10_000.0, "equity": 10_000.0, "currency": "USD"}
        try:
            r = requests.get(f"{self.base_url}/account", timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("get_account_info failed: %s", e)
            return {}