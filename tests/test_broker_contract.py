import os
import time
import unittest

os.environ.setdefault("WEBHOOK_SECRET", "test-secret")

from broker import BrokerError, OrderResult, create_broker
from broker_tradovate import TradovateClient
from broker_ctrader import (
    CTraderClient,
    ProtoOAExecutionEvent,
    ProtoOAExecutionType,
)
from config import settings


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.last_payload = None

    def post(self, _url, json, headers):
        self.last_payload = json
        return self.response

    def close(self):
        pass


class BrokerContractTests(unittest.TestCase):
    def setUp(self):
        self.old_account_id = settings.tradovate_account_id
        self.old_username = settings.tradovate_username
        self.old_map = settings.tradovate_symbol_map
        settings.tradovate_account_id = "42"
        settings.tradovate_username = "demo-user"
        settings.tradovate_symbol_map = {"MES1!": "MESU6"}

    def tearDown(self):
        settings.tradovate_account_id = self.old_account_id
        settings.tradovate_username = self.old_username
        settings.tradovate_symbol_map = self.old_map

    def make_client(self, response):
        client = TradovateClient()
        client._client.close()
        client._client = FakeHttpClient(response)
        client._access_token = "test-token"
        client._token_expiry = time.time() + 60
        return client

    def test_order_result_is_broker_neutral(self):
        result = OrderResult(True, "example", order_id="123")
        self.assertTrue(result.success)
        self.assertEqual(result.order_id, "123")

    def test_tradovate_success_maps_symbol_and_returns_order_result(self):
        client = self.make_client(FakeResponse({"orderId": 123}))
        result = client.place_bracket_order("MES1!", "Buy", 1, 5000, 5010)

        self.assertTrue(result.success)
        self.assertEqual(result.order_id, "123")
        self.assertEqual(client._client.last_payload["symbol"], "MESU6")
        self.assertEqual(client._client.last_payload["orderQty"], 1)

    def test_tradovate_failure_is_not_reported_as_success(self):
        client = self.make_client(
            FakeResponse({"failureReason": "Rejected", "failureText": "bad order"})
        )
        result = client.place_bracket_order("MES1!", "Buy", 1, 5000, 5010)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "bad order")

    def test_tradovate_rejects_fractional_contracts(self):
        client = self.make_client(FakeResponse({"orderId": 123}))
        result = client.place_bracket_order("MES1!", "Buy", 0.5, 5000, 5010)

        self.assertFalse(result.success)
        self.assertIn("whole number", result.error)

    def test_unknown_broker_is_rejected(self):
        with self.assertRaises(BrokerError):
            create_broker("unknown")

    def test_ctrader_market_order_uses_relative_protection(self):
        client = CTraderClient.__new__(CTraderClient)
        client.account_id = 77
        client._symbol_id = lambda _symbol: 12
        client._volume_for_lots = lambda _symbol_id, _lots: 100000
        client.get_last_price = lambda _symbol: 1.20000
        captured = {}

        def send(request):
            captured["request"] = request
            return ProtoOAExecutionEvent(
                ctidTraderAccountId=77,
                executionType=ProtoOAExecutionType.ORDER_ACCEPTED,
            )

        client._send = send
        result = client.place_bracket_order("EURUSD", "Buy", 0.01, 1.19000, 1.22000)

        self.assertTrue(result.success)
        self.assertEqual(captured["request"].volume, 100000)
        self.assertEqual(captured["request"].relativeStopLoss, 1000)
        self.assertEqual(captured["request"].relativeTakeProfit, 2000)


if __name__ == "__main__":
    unittest.main()
