"""
Shopify Admin GraphQL API crawler.
Replaces the old REST crawler — REST /blogs.json is deprecated in 2025-07+.
"""
import asyncio
import time
from datetime import datetime
from typing import AsyncGenerator, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.blog_post import BlogChannel, BlogPost, Platform, PostStatus


_BLOGS_QUERY = """
query Blogs {
  blogs(first: 50) {
    nodes { id title handle }
  }
}
"""

_ARTICLES_QUERY = """
query Articles($blogId: ID!, $first: Int!, $after: String) {
  blog(id: $blogId) {
    articles(first: $first, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id title handle body excerpt
        author { name }
        tags
        image { url altText }
        isPublished publishedAt updatedAt
      }
    }
  }
}
"""


class ShopifyCrawler:
    RATE_LIMIT_DELAY = 0.5

    def __init__(
        self,
        shop_domain: Optional[str] = None,
        access_token: Optional[str] = None,
        db=None,
    ):
        from sqlalchemy.orm import Session
        self.shop_domain = (shop_domain or settings.SHOPIFY_SHOP_DOMAIN).strip().rstrip("/")
        # Token priority: explicit arg → DB (OAuth) → env var
        if access_token:
            self.access_token = access_token
        elif db:
            from app.api.auth_routes import get_store_token
            self.access_token = get_store_token(self.shop_domain, db)
        else:
            self.access_token = settings.SHOPIFY_ACCESS_TOKEN
        self.api_version = settings.SHOPIFY_API_VERSION
        self.endpoint = f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    def _headers(self) -> dict:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    async def _gql(self, query: str, variables: dict = None) -> dict:
        async with httpx.AsyncClient(headers=self._headers(), timeout=30.0) as client:
            resp = await client.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
            )
            resp.raise_for_status()
            data = resp.json()
            if errors := data.get("errors"):
                raise ValueError(str(errors[0].get("message", errors)))
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
            return data["data"]

    @staticmethod
    def _gid_to_id(gid: str) -> str:
        """gid://shopify/Blog/12345678  →  '12345678'"""
        return gid.rsplit("/", 1)[-1]

    # ── Fetchers ──────────────────────────────────────────────────────────────

    async def fetch_blogs(self) -> list[dict]:
        data = await self._gql(_BLOGS_QUERY)
        return data["blogs"]["nodes"]

    async def iter_articles(self, blog_gid: str) -> AsyncGenerator[dict, None]:
        cursor = None
        while True:
            data = await self._gql(_ARTICLES_QUERY, {
                "blogId": blog_gid, "first": 50, "after": cursor,
            })
            page = data["blog"]["articles"]
            for node in page["nodes"]:
                yield node
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _upsert_channel(self, db: Session, blog: dict) -> BlogChannel:
        numeric_id = self._gid_to_id(blog["id"])
        channel = (
            db.query(BlogChannel)
            .filter_by(platform=Platform.SHOPIFY, platform_id=numeric_id)
            .first()
        )
        if not channel:
            channel = BlogChannel(platform=Platform.SHOPIFY, platform_id=numeric_id)
            db.add(channel)
        channel.title = blog.get("title")
        channel.handle = blog.get("handle")
        channel.synced_at = datetime.utcnow()
        db.flush()
        return channel

    def _parse_article(self, article: dict, channel: BlogChannel) -> dict:
        tags   = article.get("tags") or []
        image  = article.get("image") or {}
        author = (article.get("author") or {}).get("name")
        pub_at = article.get("publishedAt")
        published_at = datetime.fromisoformat(pub_at.replace("Z", "+00:00")) if pub_at else None
        status = PostStatus.PUBLISHED if article.get("isPublished") else PostStatus.DRAFT
        handle = article.get("handle", "")
        numeric_id = self._gid_to_id(article["id"])

        return {
            "platform": Platform.SHOPIFY,
            "platform_id": numeric_id,
            "platform_url": (
                f"https://{self.shop_domain}/blogs/{channel.handle}/{handle}"
                if channel.handle and handle else None
            ),
            "channel_id": channel.id,
            "title": article.get("title", ""),
            "slug": handle,
            "content_html": article.get("body"),
            "excerpt_html": article.get("excerpt"),
            "author": author,
            "tags": tags,
            "featured_image_url": image.get("url"),
            "featured_image_alt": image.get("altText"),
            "seo_title": None,
            "seo_description": None,
            "status": status,
            "source": "synced",
            "published_at": published_at,
            "synced_at": datetime.utcnow(),
        }

    def _upsert_post(self, db: Session, data: dict) -> tuple[BlogPost, bool]:
        post = (
            db.query(BlogPost)
            .filter_by(platform=Platform.SHOPIFY, platform_id=data["platform_id"])
            .first()
        )
        if post:
            for k, v in data.items():
                setattr(post, k, v)
            return post, False
        post = BlogPost(**data)
        db.add(post)
        return post, True

    # ── Public entry point ────────────────────────────────────────────────────

    async def sync_all(self, db: Session, fetch_metafields: bool = False) -> dict:
        started = time.monotonic()
        stats = {
            "platform": "shopify",
            "shop": self.shop_domain,
            "blogs_found": 0,
            "articles_synced": 0,
            "articles_skipped": 0,
            "articles_updated": 0,
            "errors": [],
        }
        try:
            blogs = await self.fetch_blogs()
            stats["blogs_found"] = len(blogs)
            for blog in blogs:
                channel = self._upsert_channel(db, blog)
                async for article in self.iter_articles(blog["id"]):
                    try:
                        data = self._parse_article(article, channel)
                        _, is_new = self._upsert_post(db, data)
                        if is_new:
                            stats["articles_synced"] += 1
                        else:
                            stats["articles_updated"] += 1
                    except Exception as e:
                        stats["errors"].append(f"Article {article.get('id')}: {e}")
                        stats["articles_skipped"] += 1
                db.commit()
        except Exception as e:
            stats["errors"].append(f"Fatal: {e}")
            db.rollback()
        stats["duration_seconds"] = round(time.monotonic() - started, 2)
        return stats
