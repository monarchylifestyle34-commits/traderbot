"""
Central configuration, loaded from environment variables (.env file).
Never hardcode credentials directly in source.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


@dataclass
class Settings:
    # --- Tradovate credentials ---
    tradovate_username: str = _get("TRADOVATE_USERNAME")
    tradovate_password: str = _get("TRADOVATE_PASSWORD")
    tradovate_app_id: str = _get("TRADOVATE_APP_ID")
    tradovate_app_version: str = _get("TRADOVATE_APP_VERSION", "1.0")
    tradovate_cid: str = _get("TRADOVATE_CID")          # client/vendor ID
    tradovate_sec: str = _get("TRADOVATE_SEC")           # client secret
    tradovate_account_id: str = _get("TRADOVATE_ACCOUNT_ID")

    # Use the demo environment until you've validated the full pipeline.
    tradovate_env: str = _get("TRADOVATE_ENV", "demo")   # "demo" or "live"

    # --- Webhook security ---
    # A shared secret you also put in the TradingView alert payload,
    # so random internet traffic to your endpoint can't place orders.
    webhook_secret: str = _get("WEBHOOK_SECRET", required=True)

    # --- Risk limits (hard caps, independent of strategy logic) ---
    max_contracts_per_trade: int = int(_get("MAX_CONTRACTS_PER_TRADE", "1"))
    max_open_positions: int = int(_get("MAX_OPEN_POSITIONS", "1"))
    daily_loss_limit_usd: float = float(_get("DAILY_LOSS_LIMIT_USD", "500"))
    entry_watch_timeout_sec: int = int(_get("ENTRY_WATCH_TIMEOUT_SEC", "1800"))  # abandon setup after 30 min

    # --- Server ---
    host: str = _get("BOT_HOST", "0.0.0.0")
    port: int = int(_get("BOT_PORT", "8000"))


settings = Settings()
