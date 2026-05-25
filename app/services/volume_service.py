"""
Keyword search volume via DataForSEO.

Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in env to enable.
Login  = your DataForSEO account email  (e.g. you@example.com)
Password = the API password shown at https://app.dataforseo.com/api-access
           (NOT your website login password — it is a separate API password)
"""
import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.dataforseo.com/v3"
_VOLUME_ENDPOINT = f"{_BASE}/keywords_data/google_ads/search_volume/live"


def is_configured() -> bool:
    return bool(settings.DATAFORSEO_LOGIN and settings.DATAFORSEO_PASSWORD)


def _masked_login() -> str:
    """Show login with masked password for safe display in debug output."""
    login = settings.DATAFORSEO_LOGIN or ""
    pwd = settings.DATAFORSEO_PASSWORD or ""
    masked_pwd = pwd[:2] + "***" + pwd[-2:] if len(pwd) > 4 else "***"
    return f"{login} / {masked_pwd}"


async def get_search_volumes(
    keywords: list[str],
    language_code: str = "en",
    location_code: int = 2840,  # 2840 = United States
) -> dict[str, dict]:
    """
    Fetch monthly search volume for a list of keywords.
    Returns {keyword: {volume, competition, cpc}} or raises on error.
    Returns {} if DataForSEO not configured.
    """
    if not is_configured():
        return {}
    if not keywords:
        return {}

    results: dict[str, dict] = {}
    for i in range(0, len(keywords), 100):
        chunk = keywords[i : i + 100]
        payload = [{
            "location_code": location_code,
            "language_code": language_code,
            "keywords": chunk,
        }]
        async with httpx.AsyncClient(
            auth=(settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD),
            timeout=30.0,
        ) as client:
            resp = await client.post(_VOLUME_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()

        top_code = data.get("status_code")
        if top_code and top_code != 20000:
            raise ValueError(f"DataForSEO error {top_code}: {data.get('status_message')}")

        for task in data.get("tasks", []):
            task_code = task.get("status_code")
            if task_code and task_code != 20000:
                logger.warning("DataForSEO task error %s: %s", task_code, task.get("status_message"))
                continue
            for item in task.get("result") or []:
                kw = item.get("keyword", "")
                if kw:
                    results[kw] = {
                        "volume": item.get("search_volume"),
                        "competition": item.get("competition_index"),  # 0-100 int
                        "cpc": item.get("cpc"),
                    }

    return results


async def test_connection(keyword: str = "seo") -> dict:
    """
    Test DataForSEO credentials with two steps:
    1. GET /v3/ — lightweight ping to verify credentials
    2. POST volume endpoint — verify endpoint access
    Returns full debug info safe to display in UI.
    """
    if not is_configured():
        return {
            "ok": False,
            "step": "config",
            "error": "DATAFORSEO_LOGIN or DATAFORSEO_PASSWORD not set in Railway environment variables.",
            "hint": "Login = DataForSEO account email. Password = API password from https://app.dataforseo.com/api-access",
        }

    auth = (settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD)
    login_display = _masked_login()

    # Step 1: ping to validate credentials
    try:
        async with httpx.AsyncClient(auth=auth, timeout=15.0) as client:
            ping = await client.get(f"{_BASE}/")
    except Exception as e:
        return {"ok": False, "step": "ping", "login_used": login_display, "error": str(e)}

    if ping.status_code == 401:
        try:
            msg = ping.json().get("status_message", ping.text[:200])
        except Exception:
            msg = ping.text[:200]
        return {
            "ok": False,
            "step": "credentials",
            "http_status": 401,
            "login_used": login_display,
            "error": "Invalid credentials (HTTP 401).",
            "api_message": msg,
            "hint": (
                "1. Login at https://app.dataforseo.com\n"
                "2. Go to API Access page\n"
                "3. DATAFORSEO_LOGIN = your email address\n"
                "4. DATAFORSEO_PASSWORD = the API password shown on that page "
                "(it is different from your website login password)"
            ),
        }

    if ping.status_code != 200:
        return {
            "ok": False,
            "step": "ping",
            "http_status": ping.status_code,
            "login_used": login_display,
            "error": f"Unexpected HTTP {ping.status_code} on credentials check.",
        }

    # Step 2: test the actual volume endpoint
    payload = [{"location_code": 2840, "language_code": "en", "keywords": [keyword]}]
    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
            resp = await client.post(_VOLUME_ENDPOINT, json=payload)
        raw = resp.json()
    except Exception as e:
        return {"ok": False, "step": "volume_endpoint", "login_used": login_display, "error": str(e)}

    if resp.status_code == 403:
        return {
            "ok": False,
            "step": "endpoint_access",
            "http_status": 403,
            "login_used": login_display,
            "error": "Credentials valid but access denied to Google Ads Keywords endpoint.",
            "hint": "Your DataForSEO plan may not include Keywords Data. Check your plan at https://app.dataforseo.com",
        }

    top_code = raw.get("status_code")
    task = (raw.get("tasks") or [{}])[0]
    result_item = (task.get("result") or [{}])[0] if task.get("result") else {}

    return {
        "ok": resp.status_code == 200 and top_code == 20000,
        "step": "volume_endpoint",
        "http_status": resp.status_code,
        "api_status_code": top_code,
        "api_status_message": raw.get("status_message"),
        "task_status_code": task.get("status_code"),
        "task_status_message": task.get("status_message"),
        "login_used": login_display,
        "cost": raw.get("cost"),
        "sample_result": {
            "keyword": result_item.get("keyword"),
            "search_volume": result_item.get("search_volume"),
            "competition_index": result_item.get("competition_index"),
            "cpc": result_item.get("cpc"),
        } if result_item else None,
        "hint": None if (resp.status_code == 200 and top_code == 20000) else (
            raw.get("status_message") or f"HTTP {resp.status_code}"
        ),
    }
