"""Broker-neutral trading interface and configured broker factory."""
from dataclasses import dataclass, field
from typing import Any, Protocol

from config import settings


class BrokerError(RuntimeError):
    """Raised when a broker operation cannot be completed safely."""


@dataclass(frozen=True)
class OrderResult:
    success: bool
    broker: str
    order_id: str | None = None
    position_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Broker(Protocol):
    name: str

    def get_last_price(self, symbol: str) -> float | None:
        """Return the most recent available price for a signal symbol."""

    def place_bracket_order(
        self,
        symbol: str,
        action: str,
        qty: float,
        stop_price: float,
        target_price: float,
    ) -> OrderResult:
        """Place a market entry protected by a stop and take-profit."""

    def close(self) -> None:
        """Release network resources held by the adapter."""


def create_broker(name: str | None = None) -> Broker:
    selected = (name or settings.broker).lower()
    if selected == "tradovate":
        from broker_tradovate import TradovateClient

        return TradovateClient()
    if selected == "fusion_ctrader":
        from broker_ctrader import CTraderClient

        return CTraderClient()
    raise BrokerError(f"Unsupported broker: {selected}")


broker = create_broker()
