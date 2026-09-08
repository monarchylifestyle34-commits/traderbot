"""Fusion Markets adapter using Spotware's official cTrader Open API SDK.

The cTrader API is a long-lived Protobuf/TLS connection rather than a REST
service.  This adapter owns the Twisted reactor thread and exposes the small,
synchronous interface used by the existing watcher threads.
"""
from concurrent.futures import Future
import logging
import threading
from typing import Any

from google.protobuf.json_format import MessageToDict

from broker import BrokerError, OrderResult
from config import settings

try:
    from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoErrorRes
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAAccountAuthReq,
        ProtoOAAccountAuthRes,
        ProtoOAApplicationAuthReq,
        ProtoOAApplicationAuthRes,
        ProtoOAErrorRes,
        ProtoOAExecutionEvent,
        ProtoOAGetAccountListByAccessTokenReq,
        ProtoOAGetAccountListByAccessTokenRes,
        ProtoOANewOrderReq,
        ProtoOAOrderErrorEvent,
        ProtoOASpotEvent,
        ProtoOASubscribeSpotsReq,
        ProtoOASymbolByIdReq,
        ProtoOASymbolsListReq,
        ProtoOASymbolsListRes,
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAExecutionType,
        ProtoOAOrderType,
        ProtoOATradeSide,
    )
    from twisted.internet import reactor
except ImportError as exc:  # pragma: no cover - depends on selected deployment
    raise RuntimeError(
        "fusion_ctrader requires the 'ctrader-open-api' package; "
        "install requirements.txt"
    ) from exc


log = logging.getLogger("ctrader")


class CTraderClient:
    name = "fusion_ctrader"

    def __init__(self):
        required = {
            "CTRADER_CLIENT_ID": settings.ctrader_client_id,
            "CTRADER_CLIENT_SECRET": settings.ctrader_client_secret,
            "CTRADER_ACCESS_TOKEN": settings.ctrader_access_token,
            "CTRADER_ACCOUNT_ID": settings.ctrader_account_id,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise BrokerError(f"Missing cTrader configuration: {', '.join(missing)}")

        self.account_id = int(settings.ctrader_account_id)
        host = (
            EndPoints.PROTOBUF_LIVE_HOST
            if settings.ctrader_env == "live"
            else EndPoints.PROTOBUF_DEMO_HOST
        )
        self._client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)

        self._ready = threading.Event()
        self._started = threading.Event()
        self._start_lock = threading.Lock()
        self._subscription_lock = threading.Lock()
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        self._symbols: dict[str, int] = {}
        self._full_symbols: dict[int, Any] = {}
        self._quotes: dict[int, float] = {}
        self._quote_events: dict[int, threading.Event] = {}
        self._subscribed: set[int] = set()

    def _ensure_started(self) -> None:
        if self._started.is_set():
            return
        with self._start_lock:
            if self._started.is_set():
                return
            self._thread = threading.Thread(
                target=self._run_reactor,
                name="ctrader-open-api",
                daemon=True,
            )
            self._thread.start()
            if not self._started.wait(settings.ctrader_request_timeout_sec):
                raise BrokerError("Timed out starting the cTrader Open API client")

    def _run_reactor(self) -> None:
        self._client.startService()
        self._started.set()
        reactor.run(installSignalHandlers=False)

    def _on_connected(self, client: Client) -> None:
        self._ready.clear()
        request = ProtoOAApplicationAuthReq(
            clientId=settings.ctrader_client_id,
            clientSecret=settings.ctrader_client_secret,
        )
        deferred = client.send(request)
        deferred.addCallbacks(self._on_application_authorized, self._on_auth_failure)

    def _on_application_authorized(self, _message: Any) -> None:
        try:
            self._expect(_message, ProtoOAApplicationAuthRes)
        except BrokerError as exc:
            self._on_auth_failure(exc)
            return
        request = ProtoOAGetAccountListByAccessTokenReq(
            accessToken=settings.ctrader_access_token
        )
        deferred = self._client.send(request)
        deferred.addCallbacks(self._on_accounts_received, self._on_auth_failure)

    def _on_accounts_received(self, envelope: Any) -> None:
        try:
            response = self._expect(envelope, ProtoOAGetAccountListByAccessTokenRes)
        except BrokerError as exc:
            self._on_auth_failure(exc)
            return
        account = next(
            (
                item
                for item in response.ctidTraderAccount
                if item.ctidTraderAccountId == self.account_id
            ),
            None,
        )
        expected_live = settings.ctrader_env == "live"
        if account is None:
            self._on_auth_failure(
                BrokerError("Configured cTrader account is not granted to this access token")
            )
            return
        if bool(account.isLive) != expected_live:
            self._on_auth_failure(
                BrokerError("CTRADER_ENV does not match the configured account type")
            )
            return

        request = ProtoOAAccountAuthReq(
            ctidTraderAccountId=self.account_id,
            accessToken=settings.ctrader_access_token,
        )
        deferred = self._client.send(request)
        deferred.addCallbacks(self._on_account_authorized, self._on_auth_failure)

    def _on_account_authorized(self, _message: Any) -> None:
        try:
            self._expect(_message, ProtoOAAccountAuthRes)
        except BrokerError as exc:
            self._on_auth_failure(exc)
            return
        request = ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id)
        deferred = self._client.send(request)
        deferred.addCallbacks(self._on_symbols_received, self._on_auth_failure)

    def _on_symbols_received(self, envelope: Any) -> None:
        try:
            response = self._expect(envelope, ProtoOASymbolsListRes)
        except BrokerError as exc:
            self._on_auth_failure(exc)
            return
        self._symbols = {
            item.symbolName.upper(): item.symbolId for item in response.symbol
        }
        self._last_error = None
        self._ready.set()
        log.info(
            "Authenticated Fusion Markets cTrader %s account %s (%s symbols).",
            settings.ctrader_env,
            self.account_id,
            len(self._symbols),
        )

    def _on_auth_failure(self, failure: Any) -> None:
        self._last_error = str(getattr(failure, "value", failure))
        self._ready.clear()
        log.error("cTrader authentication failed: %s", self._last_error)

    def _on_disconnected(self, _client: Client, reason: Any) -> None:
        self._ready.clear()
        self._subscribed.clear()
        log.warning("cTrader disconnected: %s", reason)

    def _on_message(self, _client: Client, envelope: Any) -> None:
        try:
            message = Protobuf.extract(envelope)
        except Exception:
            log.exception("Could not decode cTrader message")
            return

        if isinstance(message, ProtoOASpotEvent):
            prices = [value / 100000.0 for value in (message.bid, message.ask) if value]
            if prices:
                self._quotes[message.symbolId] = sum(prices) / len(prices)
                self._quote_events.setdefault(message.symbolId, threading.Event()).set()
        elif isinstance(message, (ProtoErrorRes, ProtoOAErrorRes, ProtoOAOrderErrorEvent)):
            log.error(
                "cTrader error: %s - %s",
                getattr(message, "errorCode", "UNKNOWN"),
                getattr(message, "description", ""),
            )

    def _wait_until_ready(self) -> None:
        self._ensure_started()
        if not self._ready.wait(settings.ctrader_request_timeout_sec):
            detail = f": {self._last_error}" if self._last_error else ""
            raise BrokerError(f"cTrader connection is not ready{detail}")

    @staticmethod
    def _expect(envelope: Any, expected_type: type) -> Any:
        response = Protobuf.extract(envelope)
        if isinstance(response, (ProtoErrorRes, ProtoOAErrorRes, ProtoOAOrderErrorEvent)):
            detail = getattr(response, "description", "")
            raise BrokerError(f"{response.errorCode}: {detail}".rstrip(": "))
        if not isinstance(response, expected_type):
            raise BrokerError(
                f"Expected {expected_type.__name__}, received {type(response).__name__}"
            )
        return response

    def _send(self, request: Any) -> Any:
        self._wait_until_ready()
        result: Future[Any] = Future()

        def send_from_reactor() -> None:
            deferred = self._client.send(
                request,
                responseTimeoutInSeconds=settings.ctrader_request_timeout_sec,
            )

            def succeeded(envelope: Any) -> None:
                if not result.done():
                    response = Protobuf.extract(envelope)
                    if isinstance(
                        response, (ProtoErrorRes, ProtoOAErrorRes, ProtoOAOrderErrorEvent)
                    ):
                        detail = getattr(response, "description", "")
                        result.set_exception(
                            BrokerError(f"{response.errorCode}: {detail}".rstrip(": "))
                        )
                    else:
                        result.set_result(response)

            def failed(failure: Any) -> None:
                if not result.done():
                    result.set_exception(
                        BrokerError(str(getattr(failure, "value", failure)))
                    )

            deferred.addCallbacks(succeeded, failed)

        reactor.callFromThread(send_from_reactor)
        return result.result(timeout=settings.ctrader_request_timeout_sec + 1)

    def _mapped_symbol(self, signal_symbol: str) -> str:
        return settings.ctrader_symbol_map.get(signal_symbol.upper(), signal_symbol)

    def _symbol_id(self, signal_symbol: str) -> int:
        self._wait_until_ready()
        broker_symbol = self._mapped_symbol(signal_symbol).upper()
        try:
            return self._symbols[broker_symbol]
        except KeyError as exc:
            raise BrokerError(
                f"Symbol {broker_symbol!r} is not available on the configured cTrader account"
            ) from exc

    def _full_symbol(self, symbol_id: int) -> Any:
        if symbol_id not in self._full_symbols:
            request = ProtoOASymbolByIdReq(ctidTraderAccountId=self.account_id)
            request.symbolId.append(symbol_id)
            response = self._send(request)
            if not response.symbol:
                raise BrokerError(f"cTrader returned no details for symbol ID {symbol_id}")
            self._full_symbols[symbol_id] = response.symbol[0]
        return self._full_symbols[symbol_id]

    def get_last_price(self, symbol: str) -> float | None:
        try:
            symbol_id = self._symbol_id(symbol)
            with self._subscription_lock:
                if symbol_id not in self._subscribed:
                    request = ProtoOASubscribeSpotsReq(
                        ctidTraderAccountId=self.account_id,
                        subscribeToSpotTimestamp=True,
                    )
                    request.symbolId.append(symbol_id)
                    self._send(request)
                    self._subscribed.add(symbol_id)

            quote_event = self._quote_events.setdefault(symbol_id, threading.Event())
            if symbol_id not in self._quotes:
                quote_event.wait(settings.ctrader_request_timeout_sec)
            return self._quotes.get(symbol_id)
        except (BrokerError, TimeoutError) as exc:
            log.warning("cTrader quote fetch failed for %s: %s", symbol, exc)
            return None

    def _volume_for_lots(self, symbol_id: int, lots: float) -> int:
        symbol = self._full_symbol(symbol_id)
        volume = int(round(lots * symbol.lotSize))
        if volume < symbol.minVolume or volume > symbol.maxVolume:
            raise BrokerError(
                f"{lots} lots is outside the broker volume range for this symbol"
            )
        if symbol.stepVolume and (volume - symbol.minVolume) % symbol.stepVolume:
            raise BrokerError(
                f"{lots} lots does not match the broker volume step for this symbol"
            )
        return volume

    def place_bracket_order(
        self,
        symbol: str,
        action: str,
        qty: float,
        stop_price: float,
        target_price: float,
    ) -> OrderResult:
        try:
            if action not in {"Buy", "Sell"}:
                raise BrokerError("cTrader action must be 'Buy' or 'Sell'")
            symbol_id = self._symbol_id(symbol)
            volume = self._volume_for_lots(symbol_id, qty)
            reference_price = self.get_last_price(symbol)
            if reference_price is None:
                raise BrokerError("No current cTrader quote is available")

            is_buy = action == "Buy"
            stop_distance = (
                reference_price - stop_price if is_buy else stop_price - reference_price
            )
            target_distance = (
                target_price - reference_price if is_buy else reference_price - target_price
            )
            if stop_distance <= 0 or target_distance <= 0:
                raise BrokerError("Stop and target are invalid relative to current market price")

            request = ProtoOANewOrderReq(
                ctidTraderAccountId=self.account_id,
                symbolId=symbol_id,
                orderType=ProtoOAOrderType.MARKET,
                tradeSide=ProtoOATradeSide.BUY if is_buy else ProtoOATradeSide.SELL,
                volume=volume,
                relativeStopLoss=max(1, round(stop_distance * 100000)),
                relativeTakeProfit=max(1, round(target_distance * 100000)),
                label="tradingview-webhook",
            )
            response = self._send(request)
            raw = MessageToDict(response, preserving_proto_field_name=True)

            if isinstance(response, (ProtoErrorRes, ProtoOAErrorRes, ProtoOAOrderErrorEvent)):
                error = getattr(response, "description", "") or response.errorCode
                return OrderResult(False, self.name, error=error, raw=raw)
            if not isinstance(response, ProtoOAExecutionEvent):
                return OrderResult(
                    False,
                    self.name,
                    error=f"Unexpected cTrader response: {type(response).__name__}",
                    raw=raw,
                )
            if response.executionType not in {
                ProtoOAExecutionType.ORDER_ACCEPTED,
                ProtoOAExecutionType.ORDER_FILLED,
                ProtoOAExecutionType.ORDER_PARTIAL_FILL,
            }:
                error = response.errorCode or f"execution type {response.executionType}"
                return OrderResult(False, self.name, error=error, raw=raw)

            order_id = str(response.order.orderId) if response.HasField("order") else None
            position_id = (
                str(response.position.positionId) if response.HasField("position") else None
            )
            log.info("cTrader order accepted: %s %s %.4f lots", action, symbol, qty)
            return OrderResult(
                True,
                self.name,
                order_id=order_id,
                position_id=position_id,
                raw=raw,
            )
        except Exception as exc:
            log.exception("cTrader order placement failed")
            return OrderResult(False, self.name, error=str(exc))

    def close(self) -> None:
        if self._started.is_set():
            reactor.callFromThread(self._client.stopService)
