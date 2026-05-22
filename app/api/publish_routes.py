"""
Phase 3A/3B — Publish drafts to Shopify & generate DALL-E 3 banners.

Routes:
  POST /api/v1/publish/{post_id}/shopify        — 3A: publish only (no image)
  POST /api/v1/publish/{post_id}/generate-image — 3B: generate DALL-E image, return URL
  POST /api/v1/publish/{post_id}/full           — 3A+3B: generate image + publish in one shot
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.blog_post import BlogChannel, BlogPost
from app.schemas.publish import FullPublishRequest, GenerateImageRequest, PublishToShopifyRequest
from app.services.image_generator import ImageGenerator
from app.services.shopify_publisher import ShopifyPublisher

publish_router = APIRouter(prefix="/api/v1/publish", tags=["publish"])


def _get_post_or_404(post_id: int, db: Session) -> BlogPost:
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def _channel_handle(db: Session, channel_id: Optional[int]) -> Optional[str]:
    if not channel_id:
        return None
    ch = db.query(BlogChannel).filter(BlogChannel.id == channel_id).first()
    return ch.handle if ch else None


# ── 3A: Publish to Shopify (no image) ────────────────────────────────────────

@publish_router.post("/{post_id}/shopify")
async def publish_to_shopify(
    post_id: int,
    body: PublishToShopifyRequest,
    db: Session = Depends(get_db),
):
    """Publish a draft to Shopify without generating an image."""
    post = _get_post_or_404(post_id, db)

    publisher = ShopifyPublisher(shop_domain=body.shop_domain, db=db)
    try:
        article = await publisher.publish_article(
            post=post,
            blog_id=body.blog_id,
            author=body.author,
            published=body.published,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Shopify API error: {e}")

    blog_handle = _channel_handle(db, post.channel_id)
    updated = publisher.sync_after_publish(db, post, article, blog_handle)

    return {
        "post_id": updated.id,
        "shopify_article_id": updated.platform_id,
        "platform_url": updated.platform_url,
        "status": updated.status,
        "image_uploaded": False,
    }


# ── 3B: Generate DALL-E 3 banner (preview URL) ───────────────────────────────

@publish_router.post("/{post_id}/generate-image")
def generate_image_for_post(
    post_id: int,
    body: GenerateImageRequest,
    db: Session = Depends(get_db),
):
    """
    Generate a DALL-E 3 banner for the post.
    Returns a temporary OpenAI URL (valid ~1 hr) + revised prompt.
    Does NOT publish to Shopify.
    """
    post = _get_post_or_404(post_id, db)
    prompt = (
        body.prompt
        or post.image_prompt
        or f"Professional SEO blog banner about: {post.focus_keyword or post.title}"
    )

    generator = ImageGenerator()
    try:
        result = generator.generate(prompt=prompt, size=body.size, response_format="url")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"DALL-E error: {e}")

    return {
        "post_id": post.id,
        "image_url": result["url"],
        "revised_prompt": result["revised_prompt"],
        "size": body.size,
    }


# ── Full publish: DALL-E + Shopify in one shot ────────────────────────────────

@publish_router.post("/{post_id}/full")
async def full_publish(
    post_id: int,
    body: FullPublishRequest,
    db: Session = Depends(get_db),
):
    """
    One-shot publish:
    1. Generate DALL-E 3 banner URL (if generate_image=True) — Shopify fetches it
    2. Create article via GraphQL articleCreate mutation
    3. Update local DB with Shopify IDs + CDN image URL
    """
    post = _get_post_or_404(post_id, db)

    image_url: Optional[str] = None
    revised_prompt: Optional[str] = None

    if body.generate_image:
        prompt = (
            body.image_prompt
            or post.image_prompt
            or f"Professional SEO blog banner about: {post.focus_keyword or post.title}"
        )
        generator = ImageGenerator()
        try:
            # Use URL format — Shopify GraphQL accepts image.src URL directly
            img = generator.generate(prompt=prompt, size=body.image_size, response_format="url")
            image_url = img["url"]
            revised_prompt = img["revised_prompt"]
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"DALL-E error: {e}")

    publisher = ShopifyPublisher(shop_domain=body.shop_domain, db=db)
    try:
        article = await publisher.publish_article(
            post=post,
            blog_id=body.blog_id,
            author=body.author,
            published=body.published,
            image_url=image_url,
            image_alt=post.title,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Shopify API error: {e}")

    blog_handle = _channel_handle(db, post.channel_id)
    updated = publisher.sync_after_publish(db, post, article, blog_handle)

    return {
        "post_id": updated.id,
        "shopify_article_id": updated.platform_id,
        "platform_url": updated.platform_url,
        "featured_image_url": updated.featured_image_url,
        "status": updated.status,
        "image_uploaded": image_url is not None,
        "revised_prompt": revised_prompt,
    }
