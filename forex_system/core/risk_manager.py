"""
Risk Manager
────────────
Enforces: max daily loss, max weekly loss, consecutive loss limit.
All state is persisted to a simple JSON file so it survives restarts.
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime, date, timezone
from pathlib import Path

from config.settings import (
    MAX_DAILY_LOSS_PCT,
    MAX_WEEKLY_LOSS_PCT,
    MAX_CONSEC_LOSSES,
    RISK_PER_TRADE_PCT,
)

logger = logging.getLogger(__name__)

STATE_FILE = Path("logs/risk_state.json")


def _week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class RiskManager:

    def __init__(self, initial_balance: float):
        self.initial_balance = initial_balance
        self._state = self._load_state()

    # ── Public ─────────────────────────────────────────────────────

    def can_trade(self, current_balance: float) -> tuple[bool, str]:
        """
        Returns (True, "") if trading is allowed, or (False, reason) otherwise.
        """
        today     = date.today().isoformat()
        week      = _week_key(date.today())
        state     = self._state

        # Consecutive losses
        if state.get("consec_losses", 0) >= MAX_CONSEC_LOSSES:
            return False, (f"{MAX_CONSEC_LOSSES} consecutive losses hit — "
                           "resume next session")

        # Daily loss
        daily_loss_pct = state.get("daily_loss_pct", {}).get(today, 0.0)
        if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
            return False, (f"Daily loss limit {MAX_DAILY_LOSS_PCT}% reached "
                           f"({daily_loss_pct:.2f}% lost today)")

        # Weekly loss
        weekly_loss_pct = state.get("weekly_loss_pct", {}).get(week, 0.0)
        if weekly_loss_pct >= MAX_WEEKLY_LOSS_PCT:
            return False, (f"Weekly loss limit {MAX_WEEKLY_LOSS_PCT}% reached "
                           f"({weekly_loss_pct:.2f}% lost this week)")

        return True, ""

    def record_trade_result(
        self,
        pnl_usd:          float,
        account_balance:  float,
    ):
        """Call after each closed trade with net P&L in account currency."""
        today = date.today().isoformat()
        week  = _week_key(date.today())

        loss_pct = -pnl_usd / account_balance * 100 if account_balance > 0 else 0.0
        loss_pct = max(0.0, loss_pct)   # only count losses

        state = self._state
        state.setdefault("daily_loss_pct",  {})[today] = \
            state["daily_loss_pct"].get(today, 0.0)  + loss_pct
        state.setdefault("weekly_loss_pct", {})[week]  = \
            state["weekly_loss_pct"].get(week,  0.0)  + loss_pct

        if pnl_usd < 0:
            state["consec_losses"] = state.get("consec_losses", 0) + 1
        else:
            state["consec_losses"] = 0

        state["last_trade"] = {
            "date":    today,
            "pnl_usd": pnl_usd,
            "consec":  state["consec_losses"],
        }

        self._save_state()
        logger.info(
            "Trade result recorded | PNL=%.2f USD | consec_losses=%d | "
            "daily_loss=%.2f%% | weekly_loss=%.2f%%",
            pnl_usd, state["consec_losses"],
            state["daily_loss_pct"].get(today, 0),
            state["weekly_loss_pct"].get(week, 0),
        )

    def reset_session(self):
        """Call at start of new session to reset consecutive loss counter."""
        self._state["consec_losses"] = 0
        self._save_state()
        logger.info("Session reset — consecutive loss counter cleared.")

    def summary(self) -> dict:
        today = date.today().isoformat()
        week  = _week_key(date.today())
        return {
            "consec_losses":    self._state.get("consec_losses", 0),
            "daily_loss_pct":   self._state.get("daily_loss_pct",  {}).get(today, 0.0),
            "weekly_loss_pct":  self._state.get("weekly_loss_pct", {}).get(week,  0.0),
            "max_daily_pct":    MAX_DAILY_LOSS_PCT,
            "max_weekly_pct":   MAX_WEEKLY_LOSS_PCT,
            "max_consec":       MAX_CONSEC_LOSSES,
        }

    # ── Persistence ────────────────────────────────────────────────

    def _load_state(self) -> dict:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self._state, f, indent=2)
