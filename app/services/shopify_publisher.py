"""
Publish AI-generated articles to Shopify via Admin REST API.
Handles image attachment (base64) and post-publish DB sync.
"""
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.blog_post import BlogPost, PostStatus


class ShopifyPublisher:
    def __init__(
        self,
        shop_domain: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        self.shop_domain = (shop_domain or settings.SHOPIFY_SHOP_DOMAIN).strip().rstrip("/")
        self.access_token = access_token or settings.SHOPIFY_ACCESS_TOKEN
        self.api_version = settings.SHOPIFY_API_VERSION
        self.base_url = f"https://{self.shop_domain}/admin/api/{self.api_version}"

    def _headers(self) -> dict:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    async def publish_article(
        self,
        post: BlogPost,
        blog_id: int,
        author: str = "SEO Agent",
        published: bool = True,
        image_b64: Optional[str] = None,
        image_filename: str = "banner.jpg",
        image_alt: Optional[str] = None,
    ) -> dict:
        """POST article to Shopify. Returns the Shopify article dict."""
        tags_str = ", ".join(post.tags or [])

        article: dict = {
            "title": post.title,
            "author": author,
            "body_html": post.content_html or "",
            "summary_html": post.excerpt_html or "",
            "tags": tags_str,
            "published": published,
            "metafields": [
                {
                    "namespace": "global",
                    "key": "title_tag",
                    "value": (post.seo_title or post.title)[:255],
                    "type": "single_line_text_field",
                },
                {
                    "namespace": "global",
                    "key": "description_tag",
                    "value": (post.seo_description or "")[:255],
                    "type": "single_line_text_field",
                },
            ],
        }

        if image_b64:
            article["image"] = {
                "attachment": image_b64,
                "filename": image_filename,
                "alt": image_alt or post.title,
            }

        async with httpx.AsyncClient(headers=self._headers(), timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/blogs/{blog_id}/articles.json",
                json={"article": article},
            )
            resp.raise_for_status()
            return resp.json()["article"]

    async def update_article_image(
        self,
        blog_id: int,
        article_id: int,
        image_b64: str,
        filename: str = "banner.jpg",
        alt: str = "",
    ) -> dict:
        """Attach / replace the featured image of an existing Shopify article."""
        async with httpx.AsyncClient(headers=self._headers(), timeout=120.0) as client:
            resp = await client.put(
                f"{self.base_url}/blogs/{blog_id}/articles/{article_id}.json",
                json={
                    "article": {
                        "id": article_id,
                        "image": {
                            "attachment": image_b64,
                            "filename": filename,
                            "alt": alt,
                        },
                    }
                },
            )
            resp.raise_for_status()
            return resp.json()["article"]

    def sync_after_publish(
        self,
        db: Session,
        post: BlogPost,
        shopify_article: dict,
        blog_handle: Optional[str] = None,
    ) -> BlogPost:
        """Update local BlogPost with Shopify IDs, URL, and image after publish."""
        handle = shopify_article.get("handle", post.slug or "")
        blog_handle = blog_handle or "news"

        post.platform_id = str(shopify_article["id"])
        post.platform_url = f"https://{self.shop_domain}/blogs/{blog_handle}/{handle}"
        post.status = PostStatus.PUBLISHED
        post.published_at = datetime.utcnow()

        img = shopify_article.get("image") or {}
        if img.get("src"):
            post.featured_image_url = img["src"]
            post.featured_image_alt = img.get("alt") or post.title

        db.commit()
        db.refresh(post)
        return post
