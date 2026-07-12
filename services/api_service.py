"""
API service — persistent aiohttp session with connection pooling.

Each lookup has its own URL and KEY from environment variables.
No URLs or keys are hardcoded anywhere.

URL template placeholders (any of these work):
  {query} / {mobile} / {number} / {num} / {q}  → the search value
  {key}   / {api_key} / {token}                  → the API key

If NO query placeholder is present in the URL, the query is appended
as ?num=VALUE automatically (common for Indian OSINT APIs).
If NO key placeholder is present, key goes as ?key=VALUE alongside the query
(or as Bearer header if the URL has its own query placeholder).

Error return values (as dicts with "_error" key):
  {"_error": "not_configured"} — API URL env var is missing
  {"_error": "api_failed"}     — network error or non-200 HTTP status
  {"_error": "no_data"}        — API returned success but empty/no records
"""
import json
import aiohttp
import asyncio
import logging
import os
from urllib.parse import urlparse, urlencode, urlunparse, parse_qs, urljoin

from services import cache_service

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6)

# Sentinel error dicts — checked in search handler for user-facing messages
ERR_NOT_CONFIGURED = {"_error": "not_configured"}
ERR_API_FAILED      = {"_error": "api_failed"}
ERR_NO_DATA         = {"_error": "no_data"}

# Placeholder lists — checked in order, first match wins
_QUERY_PLACEHOLDERS = ["{query}", "{mobile}", "{number}", "{num}", "{q}"]
_KEY_PLACEHOLDERS   = ["{key}", "{api_key}", "{token}"]

# ── Global persistent session ─────────────────────────────────────────────────
_session: aiohttp.ClientSession | None = None
_session_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _session_lock
    if _session_lock is None:
        _session_lock = asyncio.Lock()
    return _session_lock


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is not None and not _session.closed:
        return _session
    async with _get_lock():
        if _session is None or _session.closed:
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=20,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                keepalive_timeout=30,
            )
            _session = aiohttp.ClientSession(
                timeout=TIMEOUT,
                connector=connector,
                connector_owner=True,
                raise_for_status=False,
            )
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ── URL + key builder ─────────────────────────────────────────────────────────

def _build_request(url_template: str, query: str, api_key: str
                   ) -> tuple[str, dict, dict]:
    """
    Build (final_url, headers, params) for an API call.

    Strategy:
      1. Look for any known query placeholder in the URL → replace inline.
         If none found → add  ?num=<query>  in params dict (aiohttp appends it).
      2. Look for any known key placeholder in the URL → replace inline.
         If none found AND we already used params for query → add  ?key=<key>  in params.
         If none found AND we used inline replacement for query → use Bearer header.
    """
    url = url_template
    params: dict[str, str] = {}
    headers: dict[str, str] = {}

    # ── query injection ───────────────────────────────────────────────────────
    query_inline = False
    for ph in _QUERY_PLACEHOLDERS:
        if ph in url:
            url = url.replace(ph, query)
            query_inline = True
            break

    if not query_inline:
        # No placeholder in URL — append as ?num= query param
        params["num"] = query
        logger.debug("No query placeholder in URL — using params: num=%s", query)

    # ── key injection ─────────────────────────────────────────────────────────
    key_inline = False
    if api_key:
        for ph in _KEY_PLACEHOLDERS:
            if ph in url:
                url = url.replace(ph, api_key)
                key_inline = True
                break

        if not key_inline:
            if not query_inline:
                # Already using params → add key there too
                params["key"] = api_key
            else:
                # URL has its own query params → use Bearer header
                headers["Authorization"] = f"Bearer {api_key}"

    return url, headers, params


# ── Core HTTP GET ─────────────────────────────────────────────────────────────

async def _fetch_json(url: str, headers: dict, params: dict
                      ) -> dict | list | None:
    """
    GET the URL, return parsed JSON or None on any error.
    Logs every step so failures are visible in bot.log.
    """
    try:
        session = await get_session()
        # Build display URL (hide key value if present)
        display_params = {k: ("***" if k == "key" else v) for k, v in params.items()}
        logger.info("API REQUEST → %s | params=%s | headers_keys=%s",
                    url, display_params, list(headers.keys()))

        async with session.get(url, headers=headers, params=params) as resp:
            raw_text = await resp.text()
            logger.info("API RESPONSE ← HTTP %s | Content-Type: %s | body_len: %d",
                        resp.status, resp.content_type, len(raw_text))
            logger.info("API RAW BODY: %s", raw_text[:800])

            if resp.status != 200:
                logger.warning("API non-200 status %s — body: %s",
                               resp.status, raw_text[:300])
                return None

            try:
                data = json.loads(raw_text)
                logger.info("API JSON parsed OK — type: %s", type(data).__name__)
                return data
            except json.JSONDecodeError as exc:
                logger.error("API JSON parse failed: %s | body: %s",
                             exc, raw_text[:300])
                return None

    except asyncio.TimeoutError:
        logger.error("API TIMEOUT — %s", url)
        return None
    except aiohttp.ClientError as exc:
        logger.error("API CONNECTION ERROR — %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.error("API UNEXPECTED ERROR — %s: %s", url, exc)
        return None


# ── Record normaliser ─────────────────────────────────────────────────────────

def _pick(rec: dict, *keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", [], {}) and str(v).strip().lower() not in (
                "none", "null", "n/a", "nan", "na"):
            return str(v).strip()
    return None


def _normalize_record(rec: dict) -> dict:
    return {
        "name":    _pick(rec, "name", "full_name", "subscriber_name", "customer_name"),
        "father":  _pick(rec, "father_name", "father", "fathers_name", "fname"),
        "mobile":  _pick(rec, "mobile", "phone", "number", "mobile_number", "num", "contact"),
        "alt":     _pick(rec, "alt", "alternate", "alt_mobile", "alt_number",
                         "alternate_number", "secondary_mobile"),
        "aadhar":  _pick(rec, "id", "aadhar", "aadhaar", "uid", "aadhar_number",
                         "aadhaar_number", "uidai"),
        "email":   _pick(rec, "email", "email_id", "mail"),
        "circle":  _pick(rec, "circle", "operator", "telecom", "carrier",
                         "service_provider", "network", "sim"),
        "address": _pick(rec, "address", "full_address", "addr", "location"),
        "dob":     _pick(rec, "dob", "date_of_birth", "birth_date"),
        "gender":  _pick(rec, "gender", "sex"),
        "state":   _pick(rec, "state", "province"),
        "city":    _pick(rec, "city", "district", "town"),
    }


def _extract_records(raw: dict | list) -> list[dict] | None:
    """
    Pull records out of any common API response shape.
    Logs exactly what it finds/rejects.
    """
    if isinstance(raw, list):
        logger.info("API response is a bare list — %d item(s)", len(raw))
        payload = raw

    elif isinstance(raw, dict):
        # Status check
        status_raw = raw.get("status", raw.get("Status", ""))
        status_str = str(status_raw).strip().lower()
        logger.info("API status field value: '%s' (type: %s)",
                    status_str, type(status_raw).__name__)

        # Explicit failure
        FAIL_STATUSES = {"error", "fail", "failed", "false", "0",
                         "404", "400", "401", "403", "no", "not found"}
        if status_str in FAIL_STATUSES:
            msg = raw.get("message", raw.get("msg", ""))
            logger.warning("API returned failure status '%s' — message: %s",
                           status_str, msg)
            return None

        # Find payload in wrapper
        payload = None
        for key in ("data", "result", "results", "records", "response",
                    "output", "hits", "info", "details", "user", "users"):
            if key in raw and raw[key]:
                payload = raw[key]
                logger.info("Found payload in wrapper key '%s'", key)
                break

        if payload is None:
            logger.info("No wrapper key found — treating root dict as single record")
            payload = raw

        if isinstance(payload, dict):
            payload = [payload]

    else:
        logger.warning("API response is unexpected type: %s", type(raw).__name__)
        return None

    if not isinstance(payload, list):
        logger.warning("Payload is not a list after unwrapping: %s",
                       type(payload).__name__)
        return None

    logger.info("Normalising %d raw record(s)", len(payload))
    records = [_normalize_record(r) for r in payload if isinstance(r, dict)]
    records = [r for r in records if any(v for v in r.values())]
    logger.info("After normalisation: %d non-empty record(s)", len(records))
    return records or None


# ── Number Lookup ─────────────────────────────────────────────────────────────

async def lookup_number(mobile: str) -> list[dict] | dict:
    """
    Returns list[dict] on success, or one of ERR_* sentinel dicts on failure.
    """
    api_url = os.getenv("NUMBER_API_URL", "")
    api_key = os.getenv("NUMBER_API_KEY", "")

    if not api_url:
        logger.warning("Number lookup — NUMBER_API_URL not set")
        return ERR_NOT_CONFIGURED

    logger.info("Number lookup → query: %s | url_template: %s | key_set: %s",
                mobile, api_url, bool(api_key))

    cache_k = cache_service.cache_key("number", mobile)
    cached  = cache_service.get(cache_k)
    if cached is not None:
        logger.info("Number lookup cache HIT for %s", mobile)
        return cached

    url, headers, params = _build_request(api_url, mobile, api_key)
    raw = await _fetch_json(url, headers, params)

    if raw is None:
        logger.error("Number lookup API call failed for %s", mobile)
        return ERR_API_FAILED

    records = _extract_records(raw)
    if not records:
        logger.warning("Number lookup — no usable records for %s", mobile)
        return ERR_NO_DATA

    logger.info("Number lookup SUCCESS — %d record(s) for %s", len(records), mobile)
    cache_service.set(cache_k, records)
    return records


# ── Generic lookup for other services ─────────────────────────────────────────

async def _generic_lookup(service: str, url_var: str, key_var: str,
                          query: str) -> list[dict] | dict:
    """Shared helper — returns list[dict] on success, ERR_* on failure."""
    api_url = os.getenv(url_var, "")
    api_key = os.getenv(key_var, "")

    if not api_url:
        logger.warning("%s lookup — %s not set", service, url_var)
        return ERR_NOT_CONFIGURED

    logger.info("%s lookup → query: %s | url_template: %s | key_set: %s",
                service, query, api_url, bool(api_key))

    cache_k = cache_service.cache_key(service, query)
    cached  = cache_service.get(cache_k)
    if cached is not None:
        logger.info("%s lookup cache HIT for %s", service, query)
        return cached

    url, headers, params = _build_request(api_url, query, api_key)
    raw = await _fetch_json(url, headers, params)

    if raw is None:
        logger.error("%s lookup API call failed for %s", service, query)
        return ERR_API_FAILED

    records = _extract_records(raw)
    if not records:
        logger.warning("%s lookup — no usable records for %s", service, query)
        return ERR_NO_DATA

    logger.info("%s lookup SUCCESS — %d record(s) for %s",
                service, len(records), query)
    cache_service.set(cache_k, records)
    return records


async def lookup_telegram(username_or_id: str) -> list[dict] | dict:
    return await _generic_lookup(
        "Telegram", "TELEGRAM_API_URL", "TELEGRAM_API_KEY", username_or_id)


async def lookup_aadhaar(aadhaar: str) -> list[dict] | dict:
    return await _generic_lookup(
        "Aadhaar", "AADHAAR_API_URL", "AADHAAR_API_KEY", aadhaar)


async def lookup_family(aadhaar_or_name: str) -> list[dict] | dict:
    return await _generic_lookup(
        "Family", "FAMILY_API_URL", "FAMILY_API_KEY", aadhaar_or_name)


async def lookup_ifsc(ifsc: str) -> dict:
    """
    Returns raw IFSC data dict on success, or ERR_* sentinel on failure.
    Uses free public Razorpay IFSC API — no key required.
    Response shape: flat dict with uppercase keys (BANK, BRANCH, CITY, etc.)
    """
    api_url = os.getenv("IFSC_API_URL", "https://ifsc.razorpay.com/{query}")
    ifsc = ifsc.upper().strip()

    logger.info("IFSC lookup → query: %s | url: %s", ifsc, api_url)

    cache_k = cache_service.cache_key("ifsc", ifsc)
    cached  = cache_service.get(cache_k)
    if cached is not None:
        logger.info("IFSC lookup cache HIT for %s", ifsc)
        return cached

    url, headers, params = _build_request(api_url, ifsc, "")
    raw = await _fetch_json(url, headers, params)

    if raw is None:
        logger.error("IFSC lookup failed for %s (network/404)", ifsc)
        return ERR_NO_DATA

    if not isinstance(raw, dict) or "BANK" not in raw:
        logger.warning("IFSC lookup — unexpected response shape: %s", str(raw)[:200])
        return ERR_NO_DATA

    logger.info("IFSC lookup SUCCESS for %s — bank: %s", ifsc, raw.get("BANK", "?"))
    cache_service.set(cache_k, raw)
    return raw


async def lookup_pincode(pincode: str) -> dict:
    """
    Returns processed pincode dict on success, or ERR_* sentinel on failure.
    Uses free public India Post API — no key required.
    Response shape: [{"Status":"Success","PostOffice":[{Name,District,State,...}]}]
    """
    api_url = os.getenv("PINCODE_API_URL",
                        "https://api.postalpincode.in/pincode/{query}")

    logger.info("Pincode lookup → query: %s | url: %s", pincode, api_url)

    cache_k = cache_service.cache_key("pincode", pincode)
    cached  = cache_service.get(cache_k)
    if cached is not None:
        logger.info("Pincode lookup cache HIT for %s", pincode)
        return cached

    url, headers, params = _build_request(api_url, pincode, "")
    raw = await _fetch_json(url, headers, params)

    if raw is None:
        logger.error("Pincode lookup API call failed for %s", pincode)
        return ERR_API_FAILED

    # Unwrap: [{"Status":"Success","PostOffice":[...]}]
    item = raw[0] if isinstance(raw, list) and raw else raw
    if not isinstance(item, dict):
        return ERR_NO_DATA

    if str(item.get("Status", "")).strip().lower() == "error":
        logger.warning("Pincode lookup — no records for %s", pincode)
        return ERR_NO_DATA

    offices = item.get("PostOffice") or []
    if not offices:
        return ERR_NO_DATA

    first = offices[0]
    result = {
        "pincode":  pincode,
        "state":    first.get("State"),
        "district": first.get("District"),
        "region":   first.get("Region"),
        "division": first.get("Division"),
        "circle":   first.get("Circle"),
        "country":  first.get("Country"),
        "offices":  offices,
    }
    logger.info("Pincode lookup SUCCESS — %d post office(s) for %s",
                len(offices), pincode)
    cache_service.set(cache_k, result)
    return result


async def lookup_vehicle(reg_no: str) -> list[dict] | dict:
    return await _generic_lookup(
        "Vehicle", "VEHICLE_API_URL", "VEHICLE_API_KEY", reg_no.upper())
