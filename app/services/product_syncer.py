"""Sync Shopify products into the local DB for AI content context."""
import asyncio
from datetime import datetime
from typing import AsyncGenerator

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.product import Product


_PRODUCTS_QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title handle descriptionHtml vendor productType tags status
      seo { title description }
      featuredImage { url altText }
      priceRangeV2 {
        minVariantPrice { amount currencyCode }
      }
      onlineStoreUrl
    }
  }
}
"""


class ProductSyncer:
    RATE_LIMIT_DELAY = 0.5

    def __init__(self, shop_domain: str, access_token: str):
        self.shop_domain = shop_domain.strip().rstrip("/")
        self.access_token = access_token
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
        return gid.rsplit("/", 1)[-1]

    @staticmethod
    def _strip_html(html: str) -> str:
        """Extract plain text from HTML for AI context (max 2000 chars)."""
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)[:2000]
        except Exception:
            import re
            return re.sub(r"<[^>]+>", " ", html).strip()[:2000]

    async def iter_products(self) -> AsyncGenerator[dict, None]:
        cursor = None
        while True:
            data = await self._gql(_PRODUCTS_QUERY, {"first": 50, "after": cursor})
            page = data["products"]
            for node in page["nodes"]:
                yield node
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]

    def _parse_product(self, node: dict) -> dict:
        numeric_id = self._gid_to_id(node["id"])
        handle = node.get("handle", "")
        seo = node.get("seo") or {}
        price_range = node.get("priceRangeV2") or {}
        min_price_obj = (price_range.get("minVariantPrice") or {})
        image = node.get("featuredImage") or {}
        desc_html = node.get("descriptionHtml") or ""

        return {
            "shop_domain": self.shop_domain,
            "platform_id": numeric_id,
            "title": node.get("title", ""),
            "handle": handle,
            "description_html": desc_html,
            "description_text": self._strip_html(desc_html),
            "vendor": node.get("vendor"),
            "product_type": node.get("productType") or None,
            "tags": node.get("tags") or [],
            "status": (node.get("status") or "ACTIVE").lower(),
            "price_min": float(min_price_obj["amount"]) if min_price_obj.get("amount") else None,
            "currency": min_price_obj.get("currencyCode", "USD"),
            "featured_image_url": image.get("url"),
            "featured_image_alt": image.get("altText"),
            "platform_url": (
                node.get("onlineStoreUrl")
                or f"https://{self.shop_domain}/products/{handle}"
            ),
            "seo_title": seo.get("title"),
            "seo_description": seo.get("description"),
            "synced_at": datetime.utcnow(),
        }

    def _upsert(self, db: Session, data: dict) -> tuple[Product, bool]:
        prod = (
            db.query(Product)
            .filter_by(shop_domain=data["shop_domain"], platform_id=data["platform_id"])
            .first()
        )
        if prod:
            for k, v in data.items():
                setattr(prod, k, v)
            return prod, False
        prod = Product(**data)
        db.add(prod)
        return prod, True

    async def sync_all(self, db: Session) -> dict:
        stats = {"synced": 0, "updated": 0, "skipped": 0, "errors": []}
        try:
            async for node in self.iter_products():
                try:
                    status = (node.get("status") or "ACTIVE").lower()
                    if status == "archived":
                        stats["skipped"] += 1
                        continue
                    data = self._parse_product(node)
                    _, is_new = self._upsert(db, data)
                    if is_new:
                        stats["synced"] += 1
                    else:
                        stats["updated"] += 1
                except Exception as e:
                    stats["errors"].append(f"{node.get('id')}: {e}")
            db.commit()
        except Exception as e:
            stats["errors"].append(f"Fatal: {e}")
            db.rollback()
        return stats
