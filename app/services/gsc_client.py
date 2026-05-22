"""
Google Search Console API client.

Auth modes (in priority order):
1. Per-brand OAuth2 refresh token stored in BrandProfile.gsc_refresh_token
2. Global service account from GOOGLE_SERVICE_ACCOUNT_JSON env var (fallback)

GSC never supported email-invite for service accounts — add them directly
in GSC → Settings → Users and Permissions → Add user (no invite needed).
"""
import json
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

import httpx

from app.config import settings

_GSC_BASE = "https://www.googleapis.com/webmasters/v3"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPES = "https://www.googleapis.com/auth/webmasters.readonly"


class GscClient:
    def __init__(self, site_url: str, refresh_token: Optional[str] = None):
        self.site_url = site_url.rstrip("/")
        self._refresh_token = refresh_token   # per-brand OAuth2 token

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _access_token_via_oauth(self) -> str:
        """Exchange refresh_token → access_token via Google OAuth2."""
        resp = httpx.post(_TOKEN_URL, data={
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": self._refresh_token,
            "grant_type":    "refresh_token",
        }, timeout=15.0)
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _access_token_via_service_account(self) -> str:
        """Get token from global service account JSON env var."""
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        sa_info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=[_SCOPES]
        )
        creds.refresh(Request())
        return creds.token

    def _headers(self) -> dict:
        if self._refresh_token:
            token = self._access_token_via_oauth()
        else:
            token = self._access_token_via_service_account()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ── Query helpers ─────────────────────────────────────────────────────────

    def _site_path(self) -> str:
        return quote(self.site_url, safe="")

    def _query(self, body: dict) -> dict:
        url = f"{_GSC_BASE}/sites/{self._site_path()}/searchAnalytics/query"
        resp = httpx.post(url, json=body, headers=self._headers(), timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _date_range(days: int) -> tuple[str, str]:
        end = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        return start.isoformat(), end.isoformat()

    # ── Public API ────────────────────────────────────────────────────────────

    def list_sites(self) -> list:
        """Return all GSC properties the authenticated account can access."""
        url = f"{_GSC_BASE}/sites"
        resp = httpx.get(url, headers=self._headers(), timeout=15.0)
        resp.raise_for_status()
        return [
            {"site_url": s["siteUrl"], "permission": s.get("permissionLevel", "")}
            for s in resp.json().get("siteEntry", [])
        ]

    def get_overview(self, days: int = 28) -> dict:
        start, end = self._date_range(days)
        data = self._query({"startDate": start, "endDate": end, "dimensions": []})
        rows = data.get("rows", [])
        if not rows:
            return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0,
                    "start_date": start, "end_date": end, "days": days}
        r = rows[0]
        return {
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
            "start_date": start, "end_date": end, "days": days,
        }

    def get_top_pages(self, days: int = 28, limit: int = 20) -> list:
        start, end = self._date_range(days)
        data = self._query({
            "startDate": start, "endDate": end,
            "dimensions": ["page"], "rowLimit": limit,
            "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
        })
        return [{"page": r["keys"][0], "clicks": int(r.get("clicks", 0)),
                 "impressions": int(r.get("impressions", 0)),
                 "ctr": round(r.get("ctr", 0) * 100, 2),
                 "position": round(r.get("position", 0), 1)}
                for r in data.get("rows", [])]

    def get_top_queries(self, days: int = 28, limit: int = 20, page: str = None) -> list:
        start, end = self._date_range(days)
        body: dict = {
            "startDate": start, "endDate": end,
            "dimensions": ["query"], "rowLimit": limit,
            "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
        }
        if page:
            body["dimensionFilterGroups"] = [{
                "filters": [{"dimension": "page", "operator": "equals", "expression": page}]
            }]
        data = self._query(body)
        return [{"query": r["keys"][0], "clicks": int(r.get("clicks", 0)),
                 "impressions": int(r.get("impressions", 0)),
                 "ctr": round(r.get("ctr", 0) * 100, 2),
                 "position": round(r.get("position", 0), 1)}
                for r in data.get("rows", [])]

    def get_opportunities(self, days: int = 28, limit: int = 30) -> list:
        start, end = self._date_range(days)
        data = self._query({
            "startDate": start, "endDate": end,
            "dimensions": ["query", "page"], "rowLimit": 200,
            "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}],
        })
        out = []
        for r in data.get("rows", []):
            pos, impr = r.get("position", 0), r.get("impressions", 0)
            if 4 < pos <= 20 and impr >= 50:
                out.append({"query": r["keys"][0], "page": r["keys"][1],
                            "clicks": int(r.get("clicks", 0)), "impressions": int(impr),
                            "ctr": round(r.get("ctr", 0) * 100, 2),
                            "position": round(pos, 1),
                            "potential": "quick_win" if pos <= 10 else "medium_term"})
        return sorted(out, key=lambda x: x["impressions"], reverse=True)[:limit]

    def get_sparkline(self, days: int = 90) -> list:
        start, end = self._date_range(days)
        data = self._query({
            "startDate": start, "endDate": end,
            "dimensions": ["date"], "rowLimit": 365,
            "orderBy": [{"fieldName": "date", "sortOrder": "ASCENDING"}],
        })
        return [{"date": r["keys"][0], "clicks": int(r.get("clicks", 0)),
                 "impressions": int(r.get("impressions", 0))}
                for r in data.get("rows", [])]


# ── Module-level helpers (backwards compat + brand lookup) ────────────────────

def get_client_for_brand(shop_domain: Optional[str], db) -> Optional["GscClient"]:
    """Return a GscClient for the given brand, or fall back to global env var config."""
    if shop_domain and db:
        from app.models.brand_profile import BrandProfile
        bp = db.query(BrandProfile).filter_by(shop_domain=shop_domain).first()
        if bp and bp.gsc_site_url and bp.gsc_refresh_token:
            return GscClient(bp.gsc_site_url, bp.gsc_refresh_token)
        if bp and bp.gsc_site_url and settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            return GscClient(bp.gsc_site_url)  # service account but custom site
    if settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GSC_SITE_URL:
        return GscClient(settings.GSC_SITE_URL)
    return None


def is_configured() -> bool:
    return bool(settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GSC_SITE_URL)


# Legacy module-level functions (used by existing audit_routes)
def _get_credentials():
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    sa_info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=[_SCOPES]
    )
    creds.refresh(Request())
    return creds
