"""
Market Trend Research via DataForSEO.

Endpoints:
  POST /api/v1/trends/explore   — Google Trends timeline + top/rising queries
  POST /api/v1/trends/keywords  — Keyword ideas with volume, competition, CPC
  GET  /api/v1/trends/status    — Check if DataForSEO is configured
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)
trends_router = APIRouter(prefix="/api/v1/trends", tags=["trends"])

_BASE = "https://api.dataforseo.com/v3"

# DataForSEO location codes for common countries
LOCATION_CODES = {
    "us": 2840,  "gb": 2826, "au": 2036, "ca": 2124,
    "vn": 2704,  "sg": 2702, "de": 2276, "fr": 2250,
    "jp": 2392,  "kr": 2410, "in": 2356, "br": 2076,
    "th": 2764,  "id": 2360, "my": 2458, "ph": 2608,
}
LANGUAGE_CODES = {
    "en": "en", "vi": "vi", "de": "de", "fr": "fr",
    "ja": "ja", "ko": "ko", "pt": "pt", "es": "es",
    "th": "th", "id": "id",
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class TrendsExploreRequest(BaseModel):
    keyword: str
    country: str = "us"
    language: str = "en"
    time_range: str = "past_12_months"  # past_7_days|past_30_days|past_12_months|past_5_years


class KeywordIdeasRequest(BaseModel):
    keyword: str
    country: str = "us"
    language: str = "en"
    limit: int = 30


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth():
    return (settings.DATAFORSEO_LOGIN, settings.DATAFORSEO_PASSWORD)


def _location(country: str) -> int:
    return LOCATION_CODES.get(country.lower(), 2840)


def _language(lang: str) -> str:
    return LANGUAGE_CODES.get(lang.lower(), "en")


async def _post(endpoint: str, payload: list) -> dict:
    async with httpx.AsyncClient(auth=_auth(), timeout=45.0) as client:
        resp = await client.post(f"{_BASE}/{endpoint}", json=payload)
        resp.raise_for_status()
        return resp.json()


# ── Routes ────────────────────────────────────────────────────────────────────

@trends_router.get("/status")
def trends_status():
    configured = bool(settings.DATAFORSEO_LOGIN and settings.DATAFORSEO_PASSWORD)
    return {"configured": configured}


@trends_router.post("/explore")
async def explore_trends(body: TrendsExploreRequest):
    """Google Trends: interest over time + top and rising related queries."""
    if not settings.DATAFORSEO_LOGIN:
        raise HTTPException(402, "DataForSEO not configured — set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD")

    kw = body.keyword.strip()
    if not kw:
        raise HTTPException(422, "keyword required")

    payload = [{
        "keywords":      [kw],
        "location_code": _location(body.country),
        "language_code": _language(body.language),
        "type":          "web",
        "time_range":    body.time_range,
    }]

    try:
        data = await _post("keywords_data/google_trends/explore/live", payload)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"DataForSEO error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(502, f"DataForSEO request failed: {e}")

    timeline = []
    top_queries = []
    rising_queries = []

    for task in data.get("tasks", []):
        for result in task.get("result") or []:
            rtype = result.get("type", "")
            if rtype == "google_trends_graph":
                for item in result.get("items") or []:
                    val = (item.get("values") or [{}])[0].get("value", 0)
                    timeline.append({
                        "date": item.get("date_from", ""),
                        "value": val,
                    })
            elif rtype == "google_trends_queries_list":
                title = result.get("title", "")
                queries = [
                    {"query": it.get("query", ""), "value": it.get("value", 0)}
                    for it in (result.get("items") or [])
                ]
                if title == "top":
                    top_queries = queries
                elif title == "rising":
                    rising_queries = queries

    return {
        "keyword":        kw,
        "country":        body.country,
        "language":       body.language,
        "time_range":     body.time_range,
        "timeline":       timeline,
        "top_queries":    top_queries[:10],
        "rising_queries": rising_queries[:10],
    }


@trends_router.post("/keywords")
async def keyword_ideas(body: KeywordIdeasRequest):
    """Keyword ideas: related keywords with search volume, competition, CPC."""
    if not settings.DATAFORSEO_LOGIN:
        raise HTTPException(402, "DataForSEO not configured — set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD")

    kw = body.keyword.strip()
    if not kw:
        raise HTTPException(422, "keyword required")

    payload = [{
        "keywords":      [kw],
        "location_code": _location(body.country),
        "language_code": _language(body.language),
    }]

    try:
        data = await _post("keywords_data/google_ads/keywords_for_keywords/live", payload)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"DataForSEO error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(502, f"DataForSEO request failed: {e}")

    keywords = []
    for task in data.get("tasks", []):
        for result in task.get("result") or []:
            for item in result.get("keywords_data") or []:
                vol = item.get("search_volume") or 0
                keywords.append({
                    "keyword":     item.get("keyword", ""),
                    "volume":      vol,
                    "competition": round((item.get("competition") or 0) * 100),  # 0-100
                    "cpc":         round(item.get("cpc") or 0, 2),
                    "trend":       item.get("monthly_searches"),  # list of {year, month, search_volume}
                })

    # Sort by volume desc, exclude the seed keyword itself
    keywords = [k for k in keywords if k["keyword"].lower() != kw.lower()]
    keywords.sort(key=lambda x: x["volume"] or 0, reverse=True)

    return {
        "keyword":  kw,
        "country":  body.country,
        "language": body.language,
        "results":  keywords[: body.limit],
    }
