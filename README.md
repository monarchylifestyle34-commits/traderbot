# Sweep-Reversal Trading Bot

Automates the liquidity-sweep / false-breakout reversal strategy:
TradingView (signal) → Python webhook receiver → configured broker (execution).

Supported execution adapters:

- Tradovate REST API
- Fusion Markets accounts through cTrader Open API

## How it works

1. `pinescript/sweep_reversal_signal.pine` runs on your higher timeframe
   chart (1h/30m/15m). It detects when a candle sweeps the prior candle's
   high or low and then closes back inside - the false-breakout reversal.
   When that happens it fires a webhook alert with the setup details
   (direction, entry trigger level, stop, target).
2. `main.py` receives that webhook, checks it against your risk limits,
   and hands it to `watcher.py`.
3. `watcher.py` asks the selected broker adapter for live price until it breaks the entry trigger level
   (the lower-timeframe confirmation step), then places a bracket order
   (entry + stop + target) through the common interface in `broker.py`.
4. `risk.py` enforces the configured broker quantity, max open positions,
   and a daily loss limit that engages a kill switch.

## Setup

1. Create a Python 3.10+ virtual environment and run `pip install -r requirements.txt`.
2. Choose `BROKER=tradovate` or `BROKER=fusion_ctrader` in `.env`.
3. `cp .env.example .env`, fill in the selected broker's demo credentials,
   and set a random `WEBHOOK_SECRET`.
4. Run the bot: `uvicorn main:app --host 0.0.0.0 --port 8000`. For
   TradingView to reach it, it needs a public URL - use a
   VPS, or a tunnel like ngrok/Cloudflare Tunnel during testing.
5. In TradingView, add `sweep_reversal_signal.pine` as a new
   Pine Script indicator on your chosen chart/timeframe.
6. Create one alert and choose **Any alert() function call** as its condition.
   Set:
   - Expiration: **Open-ended**
   - Webhook URL: your server's `https://.../webhook`
   - Replace `<change this>` in the Pine source with your `WEBHOOK_SECRET`
     before adding it to the chart. The script's `alert()` call builds the JSON.
   - Note: TradingView requires a **paid plan (Plus or higher)** for
     webhook alerts.

## Before going live

- **Test on `TRADOVATE_ENV=demo` end-to-end first.** Confirm the full
  chain - Pine Script alert → webhook received → entry watcher triggers →
  order actually lands in your demo account - before touching
  `TRADOVATE_ENV=live`.
- **Verify the Tradovate API payloads against current docs** before
  relying on them with real money - `bot/broker_tradovate.py` uses the
  `/order/placeOSO` bracket-order endpoint; field names can change, and
  this hasn't been tested against a live account.
- The watcher currently polls REST for quotes once per second, which is
  fine for testing but not ideal for fast markets - consider moving to
  Tradovate's market-data WebSocket for lower latency before trading
  size that matters.
- For cTrader, register an application in the cTrader Open API portal, add a
  redirect URI, grant `trading` scope to the Fusion Markets account, and use
  the returned `ctidTraderAccountId`. The UI login number is not an API
  account ID. The adapter maintains the required TLS connection and converts
  `CTRADER_LOTS_PER_TRADE` into the symbol-specific protocol volume.
- cTrader does not accept absolute stop-loss/take-profit prices on market
  orders. The adapter therefore attaches relative protection calculated from
  the latest quote. Fast price movement can shift the final absolute stop and
  target, so demo-test the resulting fills and protection levels.
- Use the per-broker symbol maps when TradingView's ticker is not the exact
  orderable broker symbol. Never send continuous futures tickers directly to
  Tradovate.
- Set `DAILY_LOSS_LIMIT_USD`, `MAX_OPEN_POSITIONS`, and either
  `MAX_CONTRACTS_PER_TRADE` or `CTRADER_LOTS_PER_TRADE` to numbers you're
  actually comfortable losing. These are your safety net, not the strategy.
- Run the bot somewhere it won't silently die (a VPS with a process
  manager like `systemd` or `supervisor`), and monitor `/status` for the
  kill switch tripping.

## Files

```
sweep_reversal_signal.pine   Pine Script v6 signal generator
config.py                    Settings from .env
broker.py                    Broker protocol, order result, adapter factory
broker_tradovate.py          Tradovate REST adapter
broker_ctrader.py            Fusion/cTrader Open API adapter
risk.py                      Hard account-level risk limits
watcher.py                   Watches for entry trigger, places order
main.py                      FastAPI webhook receiver
requirements.txt
.env.example
```

## Switching brokers

The selected adapter is fixed when the process starts. Change `BROKER` and
restart the bot; do not switch a running process with active watchers.

Tradovate example:

```dotenv
BROKER=tradovate
TRADOVATE_ENV=demo
TRADOVATE_SYMBOL_MAP={"MES1!":"MESU6"}
```

Fusion Markets cTrader example:

```dotenv
BROKER=fusion_ctrader
CTRADER_ENV=demo
CTRADER_LOTS_PER_TRADE=0.01
CTRADER_SYMBOL_MAP={"GOLD":"XAUUSD"}
```
