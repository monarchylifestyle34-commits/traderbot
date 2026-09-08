"""
Central configuration, loaded from environment variables (.env file).
Never hardcode credentials directly in source.
"""
import os
import json
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _get_symbol_map(name: str) -> dict[str, str]:
    raw = _get(name, "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be a JSON object") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(mapped, str)
        for key, mapped in value.items()
    ):
        raise RuntimeError(f"{name} must map string symbols to string symbols")
    return {key.upper(): mapped for key, mapped in value.items()}


@dataclass
class Settings:
    # --- Broker selection ---
    # Supported values: "tradovate" and "fusion_ctrader".
    broker: str = _get("BROKER", "tradovate").lower()

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
    tradovate_symbol_map: dict[str, str] = None

    # --- Fusion Markets cTrader Open API credentials ---
    # Create an Open API application at openapi.ctrader.com, grant it
    # trading scope, then copy the resulting account access token here.
    ctrader_client_id: str = _get("CTRADER_CLIENT_ID")
    ctrader_client_secret: str = _get("CTRADER_CLIENT_SECRET")
    ctrader_access_token: str = _get("CTRADER_ACCESS_TOKEN")
    ctrader_account_id: str = _get("CTRADER_ACCOUNT_ID")
    ctrader_env: str = _get("CTRADER_ENV", "demo")
    ctrader_lots_per_trade: float = float(_get("CTRADER_LOTS_PER_TRADE", "0.01"))
    ctrader_request_timeout_sec: float = float(_get("CTRADER_REQUEST_TIMEOUT_SEC", "10"))
    ctrader_symbol_map: dict[str, str] = None

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

    def __post_init__(self):
        self.tradovate_symbol_map = _get_symbol_map("TRADOVATE_SYMBOL_MAP")
        self.ctrader_symbol_map = _get_symbol_map("CTRADER_SYMBOL_MAP")

        if self.broker not in {"tradovate", "fusion_ctrader"}:
            raise RuntimeError("BROKER must be 'tradovate' or 'fusion_ctrader'")

        env = self.tradovate_env if self.broker == "tradovate" else self.ctrader_env
        if env not in {"demo", "live"}:
            raise RuntimeError(f"{self.broker} environment must be 'demo' or 'live'")

        if self.max_contracts_per_trade <= 0:
            raise RuntimeError("MAX_CONTRACTS_PER_TRADE must be greater than zero")
        if self.ctrader_lots_per_trade <= 0:
            raise RuntimeError("CTRADER_LOTS_PER_TRADE must be greater than zero")

    @property
    def broker_environment(self) -> str:
        return self.tradovate_env if self.broker == "tradovate" else self.ctrader_env

    @property
    def order_quantity(self) -> float:
        if self.broker == "tradovate":
            return float(self.max_contracts_per_trade)
        return self.ctrader_lots_per_trade


settings = Settings()
