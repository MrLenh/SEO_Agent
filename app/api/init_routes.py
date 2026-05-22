from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.blog_post import BlogChannel, BlogPost, Platform
from app.schemas.blog_post import BlogChannelOut, BlogPostOut, SyncResult
from app.services.shopify_crawler import ShopifyCrawler

router = APIRouter(prefix="/api/v1/init", tags=["init"])


@router.post("/shopify", response_model=SyncResult)
async def init_shopify(
    shop_domain: Optional[str] = Query(None, description="Override shop domain from env"),
    access_token: Optional[str] = Query(None, description="Override access token from env"),
    fetch_metafields: bool = Query(False, description="Also fetch SEO metafields (slower)"),
    db: Session = Depends(get_db),
):
    """
    Crawl all blog posts from Shopify and store in local DB.
    Safe to re-run — existing posts are updated, not duplicated.
    """
    crawler = ShopifyCrawler(shop_domain=shop_domain, access_token=access_token)

    if not crawler.shop_domain or not crawler.access_token:
        raise HTTPException(
            status_code=422,
            detail="SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN must be set (env or query params)",
        )

    stats = await crawler.sync_all(db, fetch_metafields=fetch_metafields)
    return SyncResult(**stats)


# ── Blog post listing ─────────────────────────────────────────────────────────

blog_router = APIRouter(prefix="/api/v1/blogs", tags=["blogs"])


@blog_router.get("/channels")
def list_channels(db: Session = Depends(get_db)):
    """List all synced blog channels (blogs). Used to pick blog_id for publishing."""
    channels = db.query(BlogChannel).order_by(BlogChannel.title).all()
    return [
        {"id": c.id, "platform_id": c.platform_id, "title": c.title, "handle": c.handle}
        for c in channels
    ]


@blog_router.get("/", response_model=list[BlogPostOut])
def list_posts(
    platform: Optional[Platform] = None,
    source: Optional[str] = Query(None, description="'synced' | 'generated'"),
    keyword: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List all synced/generated blog posts."""
    q = db.query(BlogPost)

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
    return posts


@blog_router.get("/{post_id}", response_model=BlogPostOut)
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post
