"""
Shopify Admin REST API crawler.
Syncs all blog channels and articles into the local database.
Handles pagination, deduplication, and rate limiting.
"""
import asyncio
import re
import time
from datetime import datetime
from typing import AsyncGenerator, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.blog_post import BlogChannel, BlogPost, Platform, PostStatus


class ShopifyCrawler:
    RATE_LIMIT_DELAY = 0.6  # ~1 req/sec — safe under Shopify's 2 req/sec limit

    def __init__(
        self,
        shop_domain: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        self.shop_domain = (shop_domain or settings.SHOPIFY_SHOP_DOMAIN).strip().rstrip("/")
        self.access_token = access_token or settings.SHOPIFY_ACCESS_TOKEN
        self.api_version = settings.SHOPIFY_API_VERSION
        self.base_url = f"https://{self.shop_domain}/admin/api/{self.api_version}"
        self._client: Optional[httpx.AsyncClient] = None

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    async def _client_get(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "X-Shopify-Access-Token": self.access_token,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _get(self, url: str, params: dict = None) -> dict:
        client = await self._client_get()
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return resp.json()

    def _next_page_url(self, response: httpx.Response) -> Optional[str]:
        """Extract next page URL from Shopify Link header."""
        link_header = response.headers.get("link", "")
        match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        return match.group(1) if match else None

    # ── Blog channels ─────────────────────────────────────────────────────────

    async def fetch_blogs(self) -> list[dict]:
        data = await self._get(f"{self.base_url}/blogs.json")
        return data.get("blogs", [])

    # ── Articles (paginated) ──────────────────────────────────────────────────

    async def iter_articles(self, blog_id: int) -> AsyncGenerator[dict, None]:
        """Yield all articles for a blog using cursor-based pagination."""
        url = f"{self.base_url}/blogs/{blog_id}/articles.json"
        params = {
            "limit": 250,
            "fields": (
                "id,title,handle,body_html,summary_html,author,tags,"
                "image,published_at,updated_at,status"
            ),
        }

        client = await self._client_get()

        while url:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            await asyncio.sleep(self.RATE_LIMIT_DELAY)

            articles = resp.json().get("articles", [])
            for article in articles:
                yield article

            # Cursor pagination — only on first request; next URL includes params
            url = self._next_page_url(resp)
            params = None  # next URL already has params baked in

    async def fetch_metafields(self, article_id: int) -> dict:
        """Fetch SEO metafields (title_tag, description_tag) for an article."""
        try:
            data = await self._get(
                f"{self.base_url}/articles/{article_id}/metafields.json",
                params={"namespace": "global"},
            )
            result = {}
            for mf in data.get("metafields", []):
                if mf.get("key") == "title_tag":
                    result["seo_title"] = mf.get("value")
                elif mf.get("key") == "description_tag":
                    result["seo_description"] = mf.get("value")
            return result
        except Exception:
            return {}

    # ── DB sync ───────────────────────────────────────────────────────────────

    def _upsert_channel(self, db: Session, blog: dict) -> BlogChannel:
        channel = (
            db.query(BlogChannel)
            .filter_by(platform=Platform.SHOPIFY, platform_id=str(blog["id"]))
            .first()
        )
        if not channel:
            channel = BlogChannel(
                platform=Platform.SHOPIFY,
                platform_id=str(blog["id"]),
            )
            db.add(channel)

        channel.title = blog.get("title")
        channel.handle = blog.get("handle")
        channel.commentable = blog.get("commentable")
        channel.synced_at = datetime.utcnow()
        db.flush()
        return channel

    def _parse_article(self, article: dict, channel: BlogChannel, metafields: dict) -> dict:
        tags_raw = article.get("tags", "")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

        image = article.get("image") or {}
        pub_at = article.get("published_at")
        published_at = datetime.fromisoformat(pub_at.replace("Z", "+00:00")) if pub_at else None

        status = PostStatus.PUBLISHED if article.get("status") == "active" else PostStatus.DRAFT

        # Build canonical URL
        handle = article.get("handle", "")
        url = (
            f"https://{self.shop_domain}/blogs/{channel.handle}/{handle}"
            if channel.handle and handle
            else None
        )

        return {
            "platform": Platform.SHOPIFY,
            "platform_id": str(article["id"]),
            "platform_url": url,
            "channel_id": channel.id,
            "title": article.get("title", ""),
            "slug": handle,
            "content_html": article.get("body_html"),
            "excerpt_html": article.get("summary_html"),
            "author": article.get("author"),
            "tags": tags,
            "featured_image_url": image.get("src"),
            "featured_image_alt": image.get("alt"),
            "seo_title": metafields.get("seo_title"),
            "seo_description": metafields.get("seo_description"),
            "status": status,
            "source": "synced",
            "published_at": published_at,
            "synced_at": datetime.utcnow(),
        }

    def _upsert_post(self, db: Session, data: dict) -> tuple[BlogPost, bool]:
        """Returns (post, is_new)."""
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
        """
        Full sync: crawl every blog channel + every article → upsert to DB.
        Returns sync statistics.
        """
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

                async for article in self.iter_articles(int(blog["id"])):
                    try:
                        mf = {}
                        if fetch_metafields:
                            mf = await self.fetch_metafields(int(article["id"]))

                        data = self._parse_article(article, channel, mf)
                        post, is_new = self._upsert_post(db, data)

                        if is_new:
                            stats["articles_synced"] += 1
                        else:
                            stats["articles_updated"] += 1

                    except Exception as e:
                        stats["errors"].append(
                            f"Article {article.get('id')}: {str(e)}"
                        )
                        stats["articles_skipped"] += 1

                db.commit()

        except Exception as e:
            stats["errors"].append(f"Fatal: {str(e)}")
            db.rollback()
        finally:
            await self.close()

        stats["duration_seconds"] = round(time.monotonic() - started, 2)
        return stats
