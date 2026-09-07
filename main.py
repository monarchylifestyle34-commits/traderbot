"""
Webhook receiver. TradingView POSTs the alert JSON here when the
Pine Script sweep-reversal setup fires. We validate it, then hand
off to a background watcher that waits for the lower-timeframe
entry trigger before placing the real order.

Run with: uvicorn main:app --host 0.0.0.0 --port 8000
"""
import logging
from fastapi import FastAPI, Request, HTTPException

from config import settings
from watcher import start_watch_thread
from risk import risk_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Sweep Reversal Trading Bot")

REQUIRED_FIELDS = {"event", "direction", "symbol", "entry_trigger", "stop", "target"}

@app.get("/")
async def ping(request: Request):
    return "PONG"


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    # --- Security: require a shared secret so random traffic can't
    # trigger real orders. Add "secret": "<your secret>" to the
    # TradingView alert JSON payload. ---
    if body.get("secret") != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    missing = REQUIRED_FIELDS - body.keys()
    if missing:
        raise HTTPException(status_code=400, detail=f"missing fields: {missing}")

    if body["event"] != "setup":
        return {"status": "ignored", "reason": "unrecognized event type"}

    if body["direction"] not in ("long", "short"):
        raise HTTPException(status_code=400, detail="direction must be 'long' or 'short'")

    log.info("Received setup: %s", body)

    ok, reason = risk_manager.can_open_new_trade(settings.max_contracts_per_trade)
    if not ok:
        log.warning("Setup received but rejected by risk manager: %s", reason)
        return {"status": "rejected", "reason": reason}

    start_watch_thread(body)
    return {"status": "watching"}


@app.get("/status")
async def status():
    return {
        "kill_switch": risk_manager.kill_switch,
        "realized_pnl_today": risk_manager.realized_pnl_today,
        "open_positions": risk_manager.open_positions,
        "env": settings.tradovate_env,
    }

@app.post("/admin/reset-kill-switch")
async def reset_kill_switch(request: Request):
    body = await request.json()
    if body.get("secret") != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid secret")
    risk_manager.manual_reset()
    return {"status": "reset"}
