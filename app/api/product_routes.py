from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.product import Product
from app.services.auth_service import check_store_scope, get_current_user, get_user_shops

product_router = APIRouter(prefix="/api/v1/products", tags=["products"])


@product_router.post("/sync")
async def sync_products(
    shop_domain: str = Query(..., description="Store to sync products from"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pull all active/draft products from Shopify into the local KB."""
    shop = shop_domain.strip().lower()
    check_store_scope(user, shop, "write", db)

    from app.models.shopify_store import ShopifyStore
    store = db.query(ShopifyStore).filter_by(shop_domain=shop).first()
    if not store or not store.access_token:
        raise HTTPException(422, f"No access token for {shop}. Connect the store first.")

    from app.services.product_syncer import ProductSyncer
    syncer = ProductSyncer(shop_domain=shop, access_token=store.access_token)
    stats = await syncer.sync_all(db)
    stats["shop"] = shop
    return stats


@product_router.get("/")
def list_products(
    shop_domain: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List synced products, optionally filtered by store / search / type."""
    q = db.query(Product)
    if shop_domain:
        check_store_scope(user, shop_domain, "read", db)
        q = q.filter(Product.shop_domain == shop_domain)
    elif user.role != "admin":
        shops = get_user_shops(user, db)
        if not shops:
            return {"items": [], "total": 0, "page": page, "limit": limit}
        q = q.filter(Product.shop_domain.in_(shops))

    if search:
        q = q.filter(
            Product.title.ilike(f"%{search}%")
            | Product.product_type.ilike(f"%{search}%")
            | Product.vendor.ilike(f"%{search}%")
        )
    if product_type:
        q = q.filter(Product.product_type == product_type)

    total = q.count()
    products = q.order_by(Product.title).offset((page - 1) * limit).limit(limit).all()

    return {
        "items": [
            {
                "id": p.id,
                "shop_domain": p.shop_domain,
                "platform_id": p.platform_id,
                "title": p.title,
                "handle": p.handle,
                "vendor": p.vendor,
                "product_type": p.product_type,
                "tags": p.tags or [],
                "status": p.status,
                "price_min": p.price_min,
                "currency": p.currency,
                "featured_image_url": p.featured_image_url,
                "platform_url": p.platform_url,
                "seo_title": p.seo_title,
                "description_text": (p.description_text or "")[:200],
                "synced_at": p.synced_at,
            }
            for p in products
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


@product_router.get("/types")
def list_product_types(
    shop_domain: Optional[str] = Query(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Distinct product types for filter dropdown."""
    q = db.query(Product.product_type).distinct()
    if shop_domain:
        check_store_scope(user, shop_domain, "read", db)
        q = q.filter(Product.shop_domain == shop_domain)
    return sorted(r[0] for r in q.all() if r[0])


@product_router.get("/count")
def count_products(
    shop_domain: Optional[str] = Query(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Product)
    if shop_domain:
        check_store_scope(user, shop_domain, "read", db)
        q = q.filter(Product.shop_domain == shop_domain)
    elif user.role != "admin":
        shops = get_user_shops(user, db)
        q = q.filter(Product.shop_domain.in_(shops)) if shops else q.filter(False)
    return {"count": q.count()}
