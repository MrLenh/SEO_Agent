"""
Google Search Console API client using service account auth.

Setup:
  1. Google Cloud Console → Enable "Google Search Console API"
  2. Create a Service Account → download JSON key
  3. In GSC → Settings → Users and permissions → Add the service account email as "Restricted"
  4. Set env vars: GOOGLE_SERVICE_ACCOUNT_JSON and GSC_SITE_URL
"""
import json
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from app.config import settings

_GSC_BASE = "https://www.googleapis.com/webmasters/v3"


def _get_credentials():
    """Return a valid google.oauth2 service account credential object."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    sa_info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    creds.refresh(Request())
    return creds


def _headers() -> dict:
    creds = _get_credentials()
    return {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}


def _site_path() -> str:
    return quote(settings.GSC_SITE_URL.rstrip("/"), safe="")


def _date_range(days: int) -> tuple[str, str]:
    end = date.today() - timedelta(days=2)   # GSC has ~2 day lag
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _query(body: dict) -> dict:
    url = f"{_GSC_BASE}/sites/{_site_path()}/searchAnalytics/query"
    resp = httpx.post(url, json=body, headers=_headers(), timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def is_configured() -> bool:
    return bool(settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GSC_SITE_URL)


def get_overview(days: int = 28) -> dict:
    """Total clicks, impressions, CTR, avg position for the period."""
    start, end = _date_range(days)
    data = _query({"startDate": start, "endDate": end, "dimensions": []})
    rows = data.get("rows", [])
    if not rows:
        return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    r = rows[0]
    return {
        "clicks": int(r.get("clicks", 0)),
        "impressions": int(r.get("impressions", 0)),
        "ctr": round(r.get("ctr", 0) * 100, 2),
        "position": round(r.get("position", 0), 1),
        "start_date": start,
        "end_date": end,
        "days": days,
    }


def get_top_pages(days: int = 28, limit: int = 20) -> list[dict]:
    """Top pages ranked by clicks."""
    start, end = _date_range(days)
    data = _query({
        "startDate": start,
        "endDate": end,
        "dimensions": ["page"],
        "rowLimit": limit,
        "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
    })
    return [
        {
            "page": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in data.get("rows", [])
    ]


def get_top_queries(days: int = 28, limit: int = 20, page: str = None) -> list[dict]:
    """Top search queries by clicks, optionally filtered by page URL."""
    start, end = _date_range(days)
    body: dict = {
        "startDate": start,
        "endDate": end,
        "dimensions": ["query"],
        "rowLimit": limit,
        "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
    }
    if page:
        body["dimensionFilterGroups"] = [{
            "filters": [{"dimension": "page", "operator": "equals", "expression": page}]
        }]
    data = _query(body)
    return [
        {
            "query": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in data.get("rows", [])
    ]


def get_opportunities(days: int = 28, limit: int = 30) -> list[dict]:
    """
    Queries at position 5-20 with ≥50 impressions — high potential, easy to push.
    Sorted by impressions descending.
    """
    start, end = _date_range(days)
    data = _query({
        "startDate": start,
        "endDate": end,
        "dimensions": ["query", "page"],
        "rowLimit": 200,
        "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}],
    })
    opportunities = []
    for r in data.get("rows", []):
        pos = r.get("position", 0)
        impr = r.get("impressions", 0)
        if 4 < pos <= 20 and impr >= 50:
            opportunities.append({
                "query": r["keys"][0],
                "page": r["keys"][1],
                "clicks": int(r.get("clicks", 0)),
                "impressions": int(impr),
                "ctr": round(r.get("ctr", 0) * 100, 2),
                "position": round(pos, 1),
                "potential": "quick_win" if pos <= 10 else "medium_term",
            })
    return sorted(opportunities, key=lambda x: x["impressions"], reverse=True)[:limit]


def get_sparkline(days: int = 90) -> list[dict]:
    """Daily clicks + impressions for a time-series chart."""
    start, end = _date_range(days)
    data = _query({
        "startDate": start,
        "endDate": end,
        "dimensions": ["date"],
        "rowLimit": 365,
        "orderBy": [{"fieldName": "date", "sortOrder": "ASCENDING"}],
    })
    return [
        {
            "date": r["keys"][0],
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
        }
        for r in data.get("rows", [])
    ]
