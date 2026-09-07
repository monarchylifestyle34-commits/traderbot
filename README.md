# Sweep-Reversal Trading Bot

Automates the liquidity-sweep / false-breakout reversal strategy:
TradingView (signal) → Python webhook receiver → Tradovate (execution).

## How it works

1. `pinescript/sweep_reversal_signal.pine` runs on your higher timeframe
   chart (1h/30m/15m). It detects when a candle sweeps the prior candle's
   high or low and then closes back inside - the false-breakout reversal.
   When that happens it fires a webhook alert with the setup details
   (direction, entry trigger level, stop, target).
2. `bot/main.py` receives that webhook, checks it against your risk
   limits, and hands it to `bot/watcher.py`.
3. `watcher.py` polls live price until it breaks the entry trigger level
   (the lower-timeframe confirmation step), then places a bracket order
   (entry + stop + target) via `bot/broker_tradovate.py`.
4. `bot/risk.py` enforces hard caps - max contracts, max open positions,
   and a daily loss limit that engages a kill switch - independent of
   whatever the strategy says.

## Setup

1. **Tradovate account**: sign up for a demo account first at tradovate.com,
   then request API access (Tradovate's API Access Program) to get an
   `appId`/`cid`/`sec`.
2. `cd bot && pip install -r requirements.txt`
3. `cp .env.example .env` and fill in your Tradovate demo credentials and
   a random `WEBHOOK_SECRET`.
4. Run the bot: `uvicorn main:app --host 0.0.0.0 --port 8000` (from inside
   `bot/`). For TradingView to reach it, it needs a public URL - use a
   VPS, or a tunnel like ngrok/Cloudflare Tunnel during testing.
5. In TradingView, add `pinescript/sweep_reversal_signal.pine` as a new
   Pine Script indicator on your chosen chart/timeframe.
6. Create an alert on the "Bullish Sweep Setup" / "Bearish Sweep Setup"
   conditions. Set:
   - Trigger: **Once Per Bar Close**
   - Expiration: **Open-ended**
   - Webhook URL: your server's `https://.../webhook`
   - Message: the JSON string the script builds, plus your secret, e.g.
     add `"secret":"<your WEBHOOK_SECRET>"` into the JSON payload.
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
- Set `DAILY_LOSS_LIMIT_USD`, `MAX_CONTRACTS_PER_TRADE`, and
  `MAX_OPEN_POSITIONS` in `.env` to numbers you're actually comfortable
  losing - these are your real safety net, not the strategy logic.
- Run the bot somewhere it won't silently die (a VPS with a process
  manager like `systemd` or `supervisor`), and monitor `/status` for the
  kill switch tripping.

## Files

```
pinescript/sweep_reversal_signal.pine   Pine Script v6 signal generator
bot/config.py                            Settings from .env
bot/risk.py                              Hard account-level risk limits
bot/broker_tradovate.py                  Tradovate REST client
bot/watcher.py                           Watches for entry trigger, places order
bot/main.py                              FastAPI webhook receiver
bot/requirements.txt
bot/.env.example
```
