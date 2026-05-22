"""
Shopify OAuth 2.0 flow for Partner Apps.
Legacy Custom Apps are no longer creatable as of Jan 1, 2026.

Flow:
  1. Admin POSTs /auth/shopify/install-url {shop} → server returns install_url
  2. Frontend redirects the browser to install_url (Shopify authorization page)
  3. Merchant approves → Shopify redirects to GET /auth/shopify/callback
  4. App exchanges code for access_token, stores in DB
  5. All subsequent API calls use token from DB

All connect endpoints (install-url, connect-token, status, debug-url)
require admin auth. Only the callback is public (validated via HMAC).
"""
import hashlib
import hmac as hmac_mod
from datetime import datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.shopify_store import ShopifyStore
from app.services.auth_service import require_admin
from pydantic import BaseModel as _Base

auth_router = APIRouter(prefix="/auth", tags=["auth"])

SCOPES = "read_content,write_content"


class _InstallUrlBody(_Base):
    shop: str


class _ConnectTokenBody(_Base):
    shop_domain: str
    access_token: str


def _verify_shopify_hmac(params: dict, secret: str) -> bool:
    """Validate Shopify HMAC signature on OAuth callback."""
    hmac_value = params.pop("hmac", "")
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac_mod.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac_mod.compare_digest(digest, hmac_value)


def get_store_token(shop_domain: str, db: Session) -> str:
    """Get access token for a shop: DB first, then env var fallback."""
    store = db.query(ShopifyStore).filter_by(shop_domain=shop_domain).first()
    if store and store.access_token:
        return store.access_token
    return settings.SHOPIFY_ACCESS_TOKEN


def _get_oauth_config(db: Session) -> dict:
    """Read Partner App credentials: DB (saved from UI) first, then env vars."""
    try:
        from app.models.system_settings import SystemSetting
        rows = {
            r.key: r.value
            for r in db.query(SystemSetting).filter(
                SystemSetting.key.in_(["SHOPIFY_API_KEY", "SHOPIFY_API_SECRET", "APP_URL"])
            ).all()
        }
    except Exception:
        rows = {}
    return {
        "api_key":    rows.get("SHOPIFY_API_KEY") or settings.SHOPIFY_API_KEY or "",
        "api_secret": rows.get("SHOPIFY_API_SECRET") or settings.SHOPIFY_API_SECRET or "",
        "app_url":    rows.get("APP_URL") or settings.APP_URL or "",
    }


# ── Step 1: Build install URL (admin only) ───────────────────────────────────

@auth_router.post("/shopify/install-url")
def shopify_install_url(
    body: _InstallUrlBody,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Admin-only: build the Shopify OAuth install URL for `shop`.
    Frontend then redirects the browser to the returned `install_url`.
    Replaces the old GET /auth/shopify so only admins can initiate installs.
    """
    shop = body.shop.strip().lower()
    if not shop.endswith(".myshopify.com"):
        raise HTTPException(422, "shop must end with .myshopify.com")

    cfg = _get_oauth_config(db)
    missing = [k for k, v in (("SHOPIFY_API_KEY", cfg["api_key"]),
                              ("APP_URL", cfg["app_url"])) if not v]
    if missing:
        raise HTTPException(422, f"Missing OAuth config: {', '.join(missing)}")

    base_url = cfg["app_url"].strip().rstrip("/")
    redirect_uri = f"{base_url}/auth/shopify/callback"
    install_url = (
        f"https://{shop}/admin/oauth/authorize?"
        + urlencode({
            "client_id": cfg["api_key"],
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
        })
    )
    return {"install_url": install_url, "redirect_uri": redirect_uri}


# ── Step 2: OAuth callback ────────────────────────────────────────────────────

@auth_router.get("/shopify/callback", include_in_schema=False)
async def shopify_oauth_callback(
    request: Request,
    shop: str = Query(...),
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    cfg = _get_oauth_config(db)

    # Validate HMAC
    params = dict(request.query_params)
    if not _verify_shopify_hmac(params.copy(), cfg["api_secret"]):
        raise HTTPException(400, "Invalid HMAC — possible tampering")

    # Exchange authorization code → access token
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://{shop}/admin/oauth/access_token",
            json={
                "client_id":     cfg["api_key"],
                "client_secret": cfg["api_secret"],
                "code": code,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    access_token = data.get("access_token", "")
    scope = data.get("scope", "")

    if not access_token:
        raise HTTPException(502, f"Shopify did not return an access token: {data}")

    # Upsert store record
    store = db.query(ShopifyStore).filter_by(shop_domain=shop).first()
    if not store:
        store = ShopifyStore(shop_domain=shop)
        db.add(store)
    store.access_token = access_token
    store.scope = scope
    store.installed_at = datetime.utcnow()
    db.commit()

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Shopify Connected</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 flex items-center justify-center min-h-screen">
  <div class="bg-white rounded-2xl shadow-lg p-10 text-center max-w-md">
    <div class="text-5xl mb-4">✅</div>
    <h2 class="text-xl font-bold text-gray-800 mb-2">Shopify Connected!</h2>
    <p class="text-gray-600 mb-1"><strong>{shop}</strong></p>
    <p class="text-sm text-gray-400 mb-6">Scopes: {scope}</p>
    <p class="text-sm text-green-700 bg-green-50 rounded-lg p-3 mb-6">
      Access token saved securely to database.<br>
      Sync and publish will now work automatically.
    </p>
    <a href="/" class="inline-block px-6 py-3 bg-indigo-600 text-white rounded-xl font-medium hover:bg-indigo-700">
      Go to Dashboard →
    </a>
  </div>
</body>
</html>""")


# ── Debug: show exact OAuth URL ──────────────────────────────────────────────

@auth_router.get("/shopify/debug-url", include_in_schema=False)
def shopify_debug_url(
    shop: str = Query("example.myshopify.com"),
    admin=Depends(require_admin),
):
    """Show the exact OAuth URL + redirect_uri that will be sent to Shopify."""
    base_url = settings.APP_URL.strip().rstrip("/")
    redirect_uri = f"{base_url}/auth/shopify/callback"
    oauth_url = (
        f"https://{shop}/admin/oauth/authorize?"
        + urlencode({
            "client_id": settings.SHOPIFY_API_KEY,
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
        })
    )
    return {
        "app_url_raw": repr(settings.APP_URL),
        "app_url_cleaned": base_url,
        "redirect_uri": redirect_uri,
        "redirect_uri_length": len(redirect_uri),
        "scopes": SCOPES,
        "full_oauth_url": oauth_url,
        "partner_dashboard_must_have": redirect_uri,
    }


# ── Manual token connect (Custom App) ────────────────────────────────────────

@auth_router.post("/shopify/connect-token")
def shopify_connect_token(
    body: _ConnectTokenBody,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Save a Shopify Custom App token directly — no OAuth needed.
    Use this when you created a Custom App in Shopify Admin and have the token.
    """
    shop = body.shop_domain.strip().lower()
    if not shop or not body.access_token:
        raise HTTPException(422, "shop_domain and access_token are required")
    if not shop.endswith(".myshopify.com"):
        raise HTTPException(422, "shop_domain must end with .myshopify.com")

    store = db.query(ShopifyStore).filter_by(shop_domain=shop).first()
    if not store:
        store = ShopifyStore(shop_domain=shop)
        db.add(store)
    store.access_token = body.access_token
    store.scope = "custom_app"
    store.installed_at = datetime.utcnow()
    db.commit()
    return {"shop_domain": shop, "connected": True}


# ── Status endpoint ───────────────────────────────────────────────────────────

@auth_router.get("/shopify/status")
def shopify_status(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """List all connected Shopify stores. Admin only — non-admin users
    should use GET /api/v1/users/stores/accessible."""
    stores = db.query(ShopifyStore).all()
    return [
        {
            "shop": s.shop_domain,
            "scope": s.scope,
            "installed_at": s.installed_at,
        }
        for s in stores
    ]
