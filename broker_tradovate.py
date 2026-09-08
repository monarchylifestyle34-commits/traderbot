"""
Minimal Tradovate REST client: auth, quotes, and bracket order placement.

Reference: Tradovate REST API (partner.tradovate.com/api). Endpoints and
payload shapes can change — verify against current docs before going live,
especially order payload field names.

Base URLs:
  demo: https://demo.tradovateapi.com/v1
  live: https://live.tradovateapi.com/v1
  market data (quotes): https://md.tradovateapi.com/v1
"""
import time
import logging
import httpx

from broker import OrderResult
from config import settings

log = logging.getLogger("tradovate")

BASE_URLS = {
    "demo": "https://demo.tradovateapi.com/v1",
    "live": "https://live.tradovateapi.com/v1",
}
MD_BASE_URLS = {
    "demo": "https://md.tradovateapi.com/v1",
    "live": "https://md.tradovateapi.com/v1",
}


class TradovateClient:
    name = "tradovate"

    def __init__(self):
        self.base_url = BASE_URLS[settings.tradovate_env]
        self.md_base_url = MD_BASE_URLS[settings.tradovate_env]
        self._access_token = None
        self._token_expiry = 0
        self._client = httpx.Client(timeout=10.0)

    def _symbol(self, signal_symbol: str) -> str:
        return settings.tradovate_symbol_map.get(signal_symbol.upper(), signal_symbol)

    # ------------------------------------------------------------
    # AUTH
    # ------------------------------------------------------------
    def _authenticate(self):
        payload = {
            "name": settings.tradovate_username,
            "password": settings.tradovate_password,
            "appId": settings.tradovate_app_id,
            "appVersion": settings.tradovate_app_version,
            "cid": settings.tradovate_cid,
            "sec": settings.tradovate_sec,
        }
        r = self._client.post(f"{self.base_url}/auth/accesstokenrequest", json=payload)
        r.raise_for_status()
        data = r.json()
        if data.get("errorText"):
            raise RuntimeError(f"Tradovate auth failed: {data['errorText']}")

        self._access_token = data["accessToken"]
        # Refresh a bit before the actual expiry to be safe.
        self._token_expiry = time.time() + 85 * 60
        log.info("Authenticated with Tradovate (%s env).", settings.tradovate_env)

    def _headers(self) -> dict:
        if not self._access_token or time.time() >= self._token_expiry:
            self._authenticate()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------
    # MARKET DATA
    # ------------------------------------------------------------
    def get_last_price(self, symbol: str) -> float | None:
        """
        Polls the latest quote for a contract symbol. For production use,
        prefer the market-data WebSocket (md.tradovateapi.com) over polling
        REST — this is a simple version to keep the scaffolding readable.
        """
        broker_symbol = self._symbol(symbol)
        try:
            r = self._client.get(
                f"{self.md_base_url}/md/getQuote",
                params={"symbol": broker_symbol},
                headers=self._headers(),
            )
        except Exception as exc:
            log.warning("Quote fetch failed for %s: %s", broker_symbol, exc)
            return None
        if r.status_code != 200:
            log.warning("Quote fetch failed (%s): %s", r.status_code, r.text)
            return None
        data = r.json()
        return data.get("last") or data.get("lastPrice")

    # ------------------------------------------------------------
    # ORDERS
    # ------------------------------------------------------------
    def place_bracket_order(
        self,
        symbol: str,
        action: str,       # "Buy" or "Sell"
        qty: float,
        stop_price: float,
        target_price: float,
    ) -> OrderResult:
        """
        Places a market entry with attached stop-loss and take-profit.
        Tradovate's OSO (Order-Sends-Order) structure is the standard way
        to attach bracket legs — verify current field names against the
        /order/placeOSO endpoint docs before relying on this in live trading.
        """
        if not float(qty).is_integer() or qty <= 0:
            return OrderResult(
                success=False,
                broker=self.name,
                error="Tradovate quantity must be a positive whole number of contracts",
            )

        broker_symbol = self._symbol(symbol)
        try:
            payload = {
                "accountSpec": settings.tradovate_username,
                "accountId": int(settings.tradovate_account_id),
                "action": action,
                "symbol": broker_symbol,
                "orderQty": int(qty),
                "orderType": "Market",
                "isAutomated": True,
                "bracket1": {
                    "action": "Sell" if action == "Buy" else "Buy",
                    "orderType": "Stop",
                    "stopPrice": stop_price,
                },
                "bracket2": {
                    "action": "Sell" if action == "Buy" else "Buy",
                    "orderType": "Limit",
                    "price": target_price,
                },
            }
            r = self._client.post(
                f"{self.base_url}/order/placeOSO", json=payload, headers=self._headers()
            )
            r.raise_for_status()
            result = r.json()
        except Exception as exc:
            log.error("Tradovate order placement failed: %s", exc)
            return OrderResult(success=False, broker=self.name, error=str(exc))

        if result.get("failureReason"):
            error = result.get("failureText") or result["failureReason"]
            log.error("Tradovate order placement failed: %s", result)
            return OrderResult(success=False, broker=self.name, error=error, raw=result)

        log.info("Order placed: %s %s x%s -> %s", action, broker_symbol, qty, result)
        return OrderResult(
            success=True,
            broker=self.name,
            order_id=str(result["orderId"]) if result.get("orderId") is not None else None,
            raw=result,
        )

    def close(self) -> None:
        self._client.close()
