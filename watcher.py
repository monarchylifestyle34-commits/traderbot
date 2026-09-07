"""
After a "setup" webhook arrives (higher-timeframe sweep + reversal
confirmed), this watches live price for the entry trigger to break
in the expected direction, then submits the order. This is the
"drop to a lower timeframe and enter on the break" step from the
strategy, done in Python instead of a second Pine Script.
"""
import time
import logging
import threading

from config import settings
from broker_tradovate import tradovate
from risk import risk_manager

log = logging.getLogger("watcher")


def watch_and_enter(setup: dict):
    """
    setup keys: symbol, direction ("long"/"short"), entry_trigger,
    stop, target.
    Runs in a background thread so the webhook endpoint returns
    immediately to TradingView.
    """
    symbol = setup["symbol"]
    direction = setup["direction"]
    entry_trigger = float(setup["entry_trigger"])
    stop = float(setup["stop"])
    target = float(setup["target"])

    deadline = time.time() + settings.entry_watch_timeout_sec
    log.info(
        "Watching %s for %s entry break of %.4f (stop %.4f, target %.4f)",
        symbol, direction, entry_trigger, stop, target,
    )

    while time.time() < deadline:
        price = tradovate.get_last_price(symbol)
        if price is None:
            time.sleep(1)
            continue

        triggered = (
            (direction == "long" and price >= entry_trigger)
            or (direction == "short" and price <= entry_trigger)
        )
        if triggered:
            ok, reason = risk_manager.can_open_new_trade(settings.max_contracts_per_trade)
            if not ok:
                log.warning("Entry triggered for %s but blocked by risk manager: %s", symbol, reason)
                return

            action = "Buy" if direction == "long" else "Sell"
            result = tradovate.place_bracket_order(
                symbol=symbol,
                action=action,
                qty=settings.max_contracts_per_trade,
                stop_price=stop,
                target_price=target,
            )
            if not result.get("failureReason"):
                risk_manager.register_open()
            return

        time.sleep(1)

    log.info("Setup for %s expired unfilled after %ss — abandoning.",
              symbol, settings.entry_watch_timeout_sec)


def start_watch_thread(setup: dict):
    t = threading.Thread(target=watch_and_enter, args=(setup,), daemon=True)
    t.start()
