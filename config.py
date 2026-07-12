import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "")
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK", "")

FREE_DAILY_SEARCHES = int(os.getenv("FREE_DAILY_SEARCHES", "5"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "3"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

NUMBER_API_URL  = os.getenv("NUMBER_API_URL", "")
NUMBER_API_KEY  = os.getenv("NUMBER_API_KEY", "")

AADHAAR_API_URL = os.getenv("AADHAAR_API_URL", "")
AADHAAR_API_KEY = os.getenv("AADHAAR_API_KEY", "")

FAMILY_API_URL  = os.getenv("FAMILY_API_URL", "")
FAMILY_API_KEY  = os.getenv("FAMILY_API_KEY", "")

IFSC_API_URL    = os.getenv("IFSC_API_URL", "https://ifsc.razorpay.com/{query}")
IFSC_API_KEY    = os.getenv("IFSC_API_KEY", "")

PINCODE_API_URL = os.getenv("PINCODE_API_URL", "https://api.postalpincode.in/pincode/{query}")
PINCODE_API_KEY = os.getenv("PINCODE_API_KEY", "")

TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "")
TELEGRAM_API_KEY = os.getenv("TELEGRAM_API_KEY", "")

VEHICLE_API_URL = os.getenv("VEHICLE_API_URL", "")
VEHICLE_API_KEY = os.getenv("VEHICLE_API_KEY", "")


def _warn_missing(service: str, url_var: str, key_var: str) -> None:
    if not os.getenv(url_var):
        logger.warning(
            "⚠️  %s API not configured — set %s in your environment. "
            "Lookups for this service will return no results.",
            service, url_var,
        )
    if not os.getenv(key_var):
        logger.warning(
            "⚠️  %s API key not set — set %s in your environment.",
            service, key_var,
        )


def validate_api_config() -> None:
    """Log warnings for any unconfigured API services. Never raises."""
    _warn_missing("Number",   "NUMBER_API_URL",   "NUMBER_API_KEY")
    _warn_missing("Aadhaar",  "AADHAAR_API_URL",  "AADHAAR_API_KEY")
    _warn_missing("Family",   "FAMILY_API_URL",   "FAMILY_API_KEY")
    _warn_missing("Telegram", "TELEGRAM_API_URL", "TELEGRAM_API_KEY")
    _warn_missing("Vehicle",  "VEHICLE_API_URL",  "VEHICLE_API_KEY")
    # IFSC and Pincode use free public APIs — only warn if URL is missing, no key needed
    if not os.getenv("IFSC_API_URL"):
        logger.warning("⚠️  IFSC API URL not set — set IFSC_API_URL in your environment.")
    if not os.getenv("PINCODE_API_URL"):
        logger.warning("⚠️  Pincode API URL not set — set PINCODE_API_URL in your environment.")


def is_service_configured(service: str) -> bool:
    """
    Return True if the given service has its API URL set in the environment.
    service keys: number, aadhaar, family, ifsc, pincode, telegram, vehicle
    Checks the live env at call time — picks up new vars after a bot restart.
    IFSC and Pincode always return True — they use free public APIs with no key required.
    """
    # These two always have a working public API, no env var needed
    if service in ("ifsc", "pincode"):
        return True
    url_vars = {
        "number":   "NUMBER_API_URL",
        "aadhaar":  "AADHAAR_API_URL",
        "family":   "FAMILY_API_URL",
        "telegram": "TELEGRAM_API_URL",
        "vehicle":  "VEHICLE_API_URL",
    }
    var = url_vars.get(service)
    return bool(var and os.getenv(var))


PREMIUM_PLANS = {
    "1day":    {"label": "1 Day",    "price": 49,   "days": 1},
    "3days":   {"label": "3 Days",   "price": 99,   "days": 3},
    "7days":   {"label": "7 Days",   "price": 149,  "days": 7},
    "15days":  {"label": "15 Days",  "price": 199,  "days": 15},
    "1month":  {"label": "1 Month",  "price": 299,  "days": 30},
    "2months": {"label": "2 Months", "price": 449,  "days": 60},
    "3months": {"label": "3 Months", "price": 599,  "days": 90},
    "6months": {"label": "6 Months", "price": 799,  "days": 180},
    "1year":   {"label": "1 Year",   "price": 1199, "days": 365},
}

BOT_VERSION = "2.0.0"
BOT_NAME = "CYBER WILD WAVE"

MAINTENANCE_MODE = False
