"""
Risk management — the account-level guardrails that sit BETWEEN the
strategy signal and actually sending an order. These limits apply no
matter what the strategy says, and are the most important file in
this project when you're live with real money.
"""
import datetime as dt
import logging

from config import settings

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self):
        self.kill_switch = False
        self.realized_pnl_today = 0.0
        self.open_positions = 0
        self._day = dt.date.today()

    def _roll_day_if_needed(self):
        today = dt.date.today()
        if today != self._day:
            self._day = today
            self.realized_pnl_today = 0.0
            log.info("New trading day — daily PnL counter reset.")

    def record_trade_result(self, pnl_usd: float):
        self._roll_day_if_needed()
        self.realized_pnl_today += pnl_usd
        if self.realized_pnl_today <= -abs(settings.daily_loss_limit_usd):
            self.kill_switch = True
            log.warning(
                "DAILY LOSS LIMIT HIT (%.2f). Kill switch engaged — "
                "no new trades until manually reset.", self.realized_pnl_today
            )

    def can_open_new_trade(self, quantity: float) -> tuple[bool, str]:
        self._roll_day_if_needed()

        if self.kill_switch:
            return False, "kill switch is engaged (daily loss limit hit)"

        if self.open_positions >= settings.max_open_positions:
            return False, f"max open positions ({settings.max_open_positions}) reached"

        if quantity <= 0:
            return False, "requested size must be greater than zero"

        if quantity > settings.order_quantity:
            return False, (
                f"requested size {quantity} exceeds configured per-trade size "
                f"({settings.order_quantity})"
            )

        if self.realized_pnl_today <= -abs(settings.daily_loss_limit_usd):
            self.kill_switch = True
            return False, "daily loss limit reached"

        return True, "ok"

    def register_open(self):
        self.open_positions += 1

    def register_close(self):
        self.open_positions = max(0, self.open_positions - 1)

    def manual_reset(self):
        """Call explicitly (e.g. via an admin endpoint) to clear the kill switch."""
        self.kill_switch = False
        log.info("Kill switch manually reset.")


risk_manager = RiskManager()
