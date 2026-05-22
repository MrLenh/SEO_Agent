"""
Shopify OAuth 2.0 flow for Partner Apps.
Legacy Custom Apps are no longer creatable as of Jan 1, 2026.

Flow:
  1. User visits GET /auth/shopify?shop=mystore.myshopify.com
  2. App redirects → Shopify authorization page
  3. Merchant approves → Shopify redirects to GET /auth/shopify/callback
  4. App exchanges code for access_token, stores in DB
  5. All subsequent API calls use token from DB
"""
import hashlib
import hmac as hmac_mod
from datetime import datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.shopify_store import ShopifyStore

auth_router = APIRouter(prefix="/auth", tags=["auth"])

SCOPES = "read_online_store_pages,write_online_store_pages"


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


# ── Step 1: Start OAuth ───────────────────────────────────────────────────────

@auth_router.get("/shopify", include_in_schema=False)
async def shopify_oauth_start(shop: str = Query(..., description="mystore.myshopify.com")):
    missing = []
    if not settings.SHOPIFY_API_KEY:
        missing.append("SHOPIFY_API_KEY")
    if not settings.APP_URL:
        missing.append("APP_URL")
    if missing:
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Config Error</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-50 flex items-center justify-center min-h-screen">
<div class="bg-white rounded-2xl shadow-lg p-10 text-center max-w-md">
  <div class="text-5xl mb-4">⚠️</div>
  <h2 class="text-xl font-bold text-red-700 mb-3">Missing Environment Variables</h2>
  <div class="text-left bg-red-50 rounded-lg p-4 mb-6 text-sm font-mono">
    {"<br>".join(f"❌ {v}" for v in missing)}
  </div>
  <p class="text-gray-500 text-sm mb-4">Set these on Railway → Variables, then redeploy.</p>
  <a href="/" class="inline-block px-5 py-2 bg-indigo-600 text-white rounded-xl">← Back</a>
</div></body></html>""", status_code=200)

    redirect_uri = f"{settings.APP_URL.rstrip('/')}/auth/shopify/callback"
    url = (
        f"https://{shop}/admin/oauth/authorize?"
        + urlencode({
            "client_id": settings.SHOPIFY_API_KEY,
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
        })
    )
    return RedirectResponse(url)


# ── Step 2: OAuth callback ────────────────────────────────────────────────────

@auth_router.get("/shopify/callback", include_in_schema=False)
async def shopify_oauth_callback(
    request: Request,
    shop: str = Query(...),
    code: str = Query(...),
    db: Session = Depends(get_db),
):
    # Validate HMAC
    params = dict(request.query_params)
    if not _verify_shopify_hmac(params.copy(), settings.SHOPIFY_API_SECRET):
        raise HTTPException(400, "Invalid HMAC — possible tampering")

    # Exchange authorization code → access token
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://{shop}/admin/oauth/access_token",
            json={
                "client_id": settings.SHOPIFY_API_KEY,
                "client_secret": settings.SHOPIFY_API_SECRET,
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


# ── Status endpoint ───────────────────────────────────────────────────────────

@auth_router.get("/shopify/status")
def shopify_status(db: Session = Depends(get_db)):
    """List all connected Shopify stores."""
    stores = db.query(ShopifyStore).all()
    return [
        {
            "shop": s.shop_domain,
            "scope": s.scope,
            "installed_at": s.installed_at,
        }
        for s in stores
    ]
