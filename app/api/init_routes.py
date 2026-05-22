from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.blog_post import BlogChannel, BlogPost, Platform
from app.schemas.blog_post import BlogChannelOut, BlogPostOut, PaginatedPosts, SyncResult
from app.services.auth_service import check_store_scope, get_current_user, get_user_shops
from app.services.shopify_crawler import ShopifyCrawler

router = APIRouter(prefix="/api/v1/init", tags=["init"])


@router.post("/shopify", response_model=SyncResult)
async def init_shopify(
    shop_domain: str = Query(..., description="Shop to sync, e.g. flagwix.myshopify.com"),
    fetch_metafields: bool = Query(False, description="Also fetch SEO metafields (slower)"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Crawl all blog posts from the given Shopify store into the local DB.
    Requires write access to the store. Safe to re-run — existing posts
    are updated, not duplicated.
    """
    shop = shop_domain.strip().lower()
    check_store_scope(user, shop, "write", db)

    from app.api.auth_routes import get_store_token
    from app.models.shopify_store import ShopifyStore

    store = db.query(ShopifyStore).filter_by(shop_domain=shop).first()
    if not store or not store.access_token:
        raise HTTPException(
            422,
            f"No saved access token for {shop}. Ask an admin to connect this store first.",
        )

    crawler = ShopifyCrawler(shop_domain=shop, access_token=store.access_token, db=db)
    stats = await crawler.sync_all(db, fetch_metafields=fetch_metafields)
    return SyncResult(**stats)


# ── Blog post listing ─────────────────────────────────────────────────────────

blog_router = APIRouter(prefix="/api/v1/blogs", tags=["blogs"])


def _scope_shop_filter(query, model, shop_domain: Optional[str], user, db: Session):
    """Apply shop_domain filter, auto-scoping to a user's allowed shops.

    - shop_domain provided: require the user to have access to it
    - shop_domain omitted: admin sees all; non-admin is restricted to their shops
    """
    if shop_domain:
        check_store_scope(user, shop_domain, "read", db)
        return query.filter(model.shop_domain == shop_domain)
    if user.role == "admin":
        return query
    shops = get_user_shops(user, db)
    if not shops:
        return query.filter(False)
    return query.filter(model.shop_domain.in_(shops))


@blog_router.get("/channels")
def list_channels(
    shop_domain: Optional[str] = Query(None, description="Filter by shop domain"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List blog channels for the active store (or all stores the user can access)."""
    q = _scope_shop_filter(db.query(BlogChannel), BlogChannel, shop_domain, user, db)
    channels = q.order_by(BlogChannel.title).all()
    return [
        {
            "id": c.id,
            "platform_id": c.platform_id,
            "shop_domain": c.shop_domain,
            "title": c.title,
            "handle": c.handle,
        }
        for c in channels
    ]


@blog_router.get("/", response_model=PaginatedPosts)
def list_posts(
    platform: Optional[Platform] = None,
    source: Optional[str] = Query(None, description="'synced' | 'generated'"),
    keyword: Optional[str] = None,
    shop_domain: Optional[str] = Query(None, description="Filter by shop domain"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List synced/generated blog posts for the active store (or all the user can access)."""
    q = _scope_shop_filter(db.query(BlogPost), BlogPost, shop_domain, user, db)

    if platform:
        q = q.filter(BlogPost.platform == platform)
    if source:
        q = q.filter(BlogPost.source == source)
    if keyword:
        q = q.filter(
            BlogPost.title.ilike(f"%{keyword}%")
            | BlogPost.focus_keyword.ilike(f"%{keyword}%")
        )

    total = q.count()
    posts = q.order_by(BlogPost.published_at.desc()).offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit if total else 0
    return PaginatedPosts(items=posts, total=total, page=page, limit=limit, total_pages=total_pages)


@blog_router.get("/{post_id}", response_model=BlogPostOut)
def get_post(
    post_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "read", db)
    return post
