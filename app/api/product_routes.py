"""
Products KB — tracking registry only.

Products tracked here are monitored for SEO ranking.
Full product data is fetched live from Shopify at article-generation time.

Routes:
  GET    /api/v1/products/               — list tracked products
  POST   /api/v1/products/search         — search live Shopify (to pick which to track)
  POST   /api/v1/products/track          — add product to tracking
  PATCH  /api/v1/products/{id}           — update notes / pause tracking
  DELETE /api/v1/products/{id}           — stop tracking
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product
from app.services.auth_service import check_store_scope, get_current_user, get_user_shops

product_router = APIRouter(prefix="/api/v1/products", tags=["products"])


# ── Live search (reads Shopify, never the local DB) ───────────────────────────

class _SearchBody(BaseModel):
    shop_domain: str
    keyword: str
    limit: int = 10


@product_router.post("/search")
async def search_shopify_products(
    body: _SearchBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search Shopify live for products matching a keyword. Used to pick products to track."""
    check_store_scope(user, body.shop_domain, "read", db)

    from app.models.shopify_store import ShopifyStore
    store = db.query(ShopifyStore).filter_by(shop_domain=body.shop_domain).first()
    if not store or not store.access_token:
        raise HTTPException(422, f"No access token for {body.shop_domain}")

    from app.services.product_syncer import fetch_products_for_keyword
    results = await fetch_products_for_keyword(
        shop_domain=body.shop_domain,
        access_token=store.access_token,
        keyword=body.keyword,
        limit=min(body.limit, 20),
    )
    # mark which are already tracked
    tracked_ids = {
        p.platform_id
        for p in db.query(Product.platform_id)
        .filter(Product.shop_domain == body.shop_domain)
        .all()
    }
    for r in results:
        r["tracked"] = r["platform_id"] in tracked_ids
    return results


# ── Tracking CRUD ─────────────────────────────────────────────────────────────

class _TrackBody(BaseModel):
    shop_domain: str
    platform_id: str
    handle: str
    title: str
    product_type: Optional[str] = None
    platform_url: Optional[str] = None
    notes: Optional[str] = None


@product_router.post("/track")
def track_product(
    body: _TrackBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a Shopify product to the SEO tracking registry."""
    check_store_scope(user, body.shop_domain, "write", db)

    existing = db.query(Product).filter_by(
        shop_domain=body.shop_domain, platform_id=body.platform_id
    ).first()
    if existing:
        return _product_out(existing)

    prod = Product(
        shop_domain=body.shop_domain,
        platform_id=body.platform_id,
        handle=body.handle,
        title=body.title,
        product_type=body.product_type,
        platform_url=body.platform_url or f"https://{body.shop_domain}/products/{body.handle}",
        notes=body.notes,
        status="tracked",
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return _product_out(prod)


class _PatchBody(BaseModel):
    status: Optional[str] = None   # "tracked" | "paused"
    notes: Optional[str] = None


@product_router.patch("/{product_id}")
def update_tracked_product(
    product_id: int,
    body: _PatchBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(404, "Tracked product not found")
    check_store_scope(user, prod.shop_domain, "write", db)
    if body.status is not None:
        if body.status not in ("tracked", "paused"):
            raise HTTPException(422, "status must be 'tracked' or 'paused'")
        prod.status = body.status
    if body.notes is not None:
        prod.notes = body.notes
    db.commit()
    return _product_out(prod)


@product_router.delete("/{product_id}")
def untrack_product(
    product_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(404, "Tracked product not found")
    check_store_scope(user, prod.shop_domain, "write", db)
    db.delete(prod)
    db.commit()
    return {"untracked": product_id}


@product_router.get("/")
def list_tracked_products(
    shop_domain: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List products being tracked for SEO ranking."""
    q = db.query(Product)
    if shop_domain:
        check_store_scope(user, shop_domain, "read", db)
        q = q.filter(Product.shop_domain == shop_domain)
    elif user.role != "admin":
        shops = get_user_shops(user, db)
        q = q.filter(Product.shop_domain.in_(shops)) if shops else q.filter(False)
    if status:
        q = q.filter(Product.status == status)

    total = q.count()
    products = q.order_by(Product.tracked_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [_product_out(p) for p in products],
        "total": total,
        "page": page,
        "limit": limit,
    }


# ── Live refresh for a single tracked product ─────────────────────────────────

@product_router.get("/{product_id}/live")
async def get_live_product(
    product_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch fresh data for a tracked product directly from Shopify."""
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(404, "Tracked product not found")
    check_store_scope(user, prod.shop_domain, "read", db)

    from app.models.shopify_store import ShopifyStore
    from app.services.product_syncer import fetch_product_by_id
    store = db.query(ShopifyStore).filter_by(shop_domain=prod.shop_domain).first()
    if not store or not store.access_token:
        raise HTTPException(422, "No access token — store not connected")

    live = await fetch_product_by_id(prod.shop_domain, store.access_token, prod.platform_id)
    if not live:
        raise HTTPException(502, "Product not found in Shopify")
    return {**_product_out(prod), "live": live}


def _product_out(p: Product) -> dict:
    return {
        "id": p.id,
        "shop_domain": p.shop_domain,
        "platform_id": p.platform_id,
        "handle": p.handle,
        "title": p.title,
        "product_type": p.product_type,
        "platform_url": p.platform_url,
        "status": p.status,
        "notes": p.notes,
        "tracked_at": p.tracked_at,
    }
