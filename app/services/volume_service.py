"""
Keyword search volume via DataForSEO.

Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in env to enable.
Returns {} gracefully when not configured.
"""
from typing import Optional
import httpx
from app.config import settings

_ENDPOINT = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"


async def get_search_volumes(
    keywords: list[str],
    language_code: str = "en",
    location_code: int = 2840,  # 2840 = United States
) -> dict[str, dict]:
    """
    Fetch monthly search volume for a list of keywords.

    Returns {keyword: {volume, competition, cpc}} or {} if DataForSEO not configured.
    competition is 0.0–1.0 (we use it as difficulty proxy).
    """
    if not settings.DATAFORSEO_LOGIN or not settings.DATAFORSEO_PASSWORD:
        return {}
    if not keywords:
        return {}

    # DataForSEO allows max 1000 keywords per task; we batch in 100s to be safe
    results: dict[str, dict] = {}
    for i in range(0, len(keywords), 100):
        chunk = keywords[i : i + 100]
        payload = [{"location_code": location_code, "language_code": language_code, "keywords": chunk}]
        try:
            async with httpx.AsyncClient(
                auth=(settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD),
                timeout=30.0,
            ) as client:
                resp = await client.post(_ENDPOINT, json=payload)
                resp.raise_for_status()
                data = resp.json()
            for task in data.get("tasks", []):
                for item in task.get("result") or []:
                    kw = item.get("keyword", "")
                    if kw:
                        results[kw] = {
                            "volume": item.get("search_volume"),
                            "competition": item.get("competition"),
                            "cpc": item.get("cpc"),
                        }
        except Exception:
            pass

    return results
