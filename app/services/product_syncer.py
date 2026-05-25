"""
Live Shopify product fetcher — NO local sync.

Products are fetched directly from the Shopify Admin GraphQL API at
article-generation time so data is always current (price, description,
availability).  The local `products` table is only a lightweight tracking
registry (which products to monitor for SEO ranking).
"""
import asyncio
from typing import Optional

import httpx

from app.config import settings


_SEARCH_QUERY = """
query SearchProducts($query: String!, $first: Int!) {
  products(first: $first, query: $query) {
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

_BY_ID_QUERY = """
query ProductById($id: ID!) {
  product(id: $id) {
    id title handle descriptionHtml vendor productType tags status
    seo { title description }
    featuredImage { url altText }
    priceRangeV2 {
      minVariantPrice { amount currencyCode }
    }
    onlineStoreUrl
  }
}
"""


def _gid(numeric_id: str) -> str:
    return f"gid://shopify/Blog/{numeric_id}"


def _parse_node(node: dict, shop_domain: str) -> dict:
    gid = node.get("id", "")
    numeric_id = gid.rsplit("/", 1)[-1] if "/" in gid else gid
    handle = node.get("handle", "")
    seo = node.get("seo") or {}
    price_obj = ((node.get("priceRangeV2") or {}).get("minVariantPrice") or {})
    image = node.get("featuredImage") or {}

    desc_html = node.get("descriptionHtml") or ""
    desc_text = ""
    if desc_html:
        try:
            from bs4 import BeautifulSoup
            desc_text = BeautifulSoup(desc_html, "lxml").get_text(" ", strip=True)[:1500]
        except Exception:
            import re
            desc_text = re.sub(r"<[^>]+>", " ", desc_html).strip()[:1500]

    return {
        "platform_id": numeric_id,
        "title": node.get("title", ""),
        "handle": handle,
        "description_text": desc_text,
        "vendor": node.get("vendor"),
        "product_type": node.get("productType") or None,
        "tags": node.get("tags") or [],
        "status": (node.get("status") or "ACTIVE").lower(),
        "price_min": float(price_obj["amount"]) if price_obj.get("amount") else None,
        "currency": price_obj.get("currencyCode", "USD"),
        "featured_image_url": image.get("url"),
        "featured_image_alt": image.get("altText"),
        "platform_url": (
            node.get("onlineStoreUrl")
            or f"https://{shop_domain}/products/{handle}"
        ),
        "seo_title": seo.get("title"),
        "seo_description": seo.get("description"),
    }


async def _gql(endpoint: str, headers: dict, query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
        resp = await client.post(endpoint, json={"query": query, "variables": variables})
        resp.raise_for_status()
        data = resp.json()
        if errors := data.get("errors"):
            raise ValueError(str(errors[0].get("message", errors)))
        return data["data"]


async def fetch_products_for_keyword(
    shop_domain: str,
    access_token: str,
    keyword: str,
    limit: int = 8,
    api_version: Optional[str] = None,
) -> list[dict]:
    """
    Query Shopify live for products matching `keyword`.
    Returns parsed product dicts with fresh data — never reads local DB.
    Used by content_writer when generating articles.
    """
    version = api_version or settings.SHOPIFY_API_VERSION
    endpoint = f"https://{shop_domain}/admin/api/{version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }
    try:
        data = await _gql(endpoint, headers, _SEARCH_QUERY, {
            "query": keyword,
            "first": limit,
        })
        nodes = data.get("products", {}).get("nodes") or []
        return [_parse_node(n, shop_domain) for n in nodes if n.get("status", "ACTIVE").lower() != "archived"]
    except Exception:
        return []


async def fetch_product_by_id(
    shop_domain: str,
    access_token: str,
    platform_id: str,
    api_version: Optional[str] = None,
) -> Optional[dict]:
    """Fetch a single tracked product by its Shopify numeric ID."""
    version = api_version or settings.SHOPIFY_API_VERSION
    endpoint = f"https://{shop_domain}/admin/api/{version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }
    try:
        gid = f"gid://shopify/Product/{platform_id}"
        data = await _gql(endpoint, headers, _BY_ID_QUERY, {"id": gid})
        node = data.get("product")
        return _parse_node(node, shop_domain) if node else None
    except Exception:
        return None
