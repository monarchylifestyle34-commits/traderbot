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
    def __init__(self):
        self.base_url = BASE_URLS[settings.tradovate_env]
        self.md_base_url = MD_BASE_URLS[settings.tradovate_env]
        self._access_token = None
        self._token_expiry = 0
        self._client = httpx.Client(timeout=10.0)

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
        r = self._client.get(
            f"{self.md_base_url}/md/getQuote",
            params={"symbol": symbol},
            headers=self._headers(),
        )
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
        qty: int,
        stop_price: float,
        target_price: float,
    ) -> dict:
        """
        Places a market entry with attached stop-loss and take-profit.
        Tradovate's OSO (Order-Sends-Order) structure is the standard way
        to attach bracket legs — verify current field names against the
        /order/placeOSO endpoint docs before relying on this in live trading.
        """
        payload = {
            "accountSpec": settings.tradovate_username,
            "accountId": int(settings.tradovate_account_id),
            "action": action,
            "symbol": symbol,
            "orderQty": qty,
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
        result = r.json()
        if r.status_code != 200 or result.get("failureReason"):
            log.error("Order placement failed: %s", result)
        else:
            log.info("Order placed: %s %s x%s -> %s", action, symbol, qty, result)
        return result


tradovate = TradovateClient()
