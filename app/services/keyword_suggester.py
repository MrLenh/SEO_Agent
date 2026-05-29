"""
Store-aware keyword suggestions.

Analyzes a store's existing blog content to identify themes,
then uses GPT-4o + Serper to recommend keywords worth targeting.
"""
import json
from typing import Optional

import httpx

from app.config import settings


def _extract_store_themes(posts: list) -> str:
    """Build a concise theme summary from blog post titles/keywords."""
    titles = [p.title for p in posts if p.title][:30]
    keywords = list({p.focus_keyword for p in posts if p.focus_keyword})[:20]
    tags_flat = []
    for p in posts[:20]:
        if p.tags:
            tags_flat.extend(p.tags if isinstance(p.tags, list) else [])
    unique_tags = list(set(tags_flat))[:20]

    lines = []
    if titles:
        lines.append("Blog titles: " + " | ".join(titles[:15]))
    if keywords:
        lines.append("Focus keywords: " + ", ".join(keywords))
    if unique_tags:
        lines.append("Content tags: " + ", ".join(unique_tags[:15]))
    return "\n".join(lines) or "No content yet"


def _serper_related(seed_keywords: list[str], api_key: str) -> list[str]:
    """Fetch PAA and related searches from Serper for seed keywords."""
    if not api_key or not seed_keywords:
        return []
    related = []
    for kw in seed_keywords[:5]:
        try:
            resp = httpx.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": kw, "num": 10},
                timeout=10.0,
            )
            data = resp.json()
            for item in data.get("peopleAlsoAsk", []):
                related.append(item.get("question", ""))
            for item in data.get("relatedSearches", []):
                related.append(item.get("query", ""))
        except Exception:
            pass
    return [r for r in related if r][:30]


def generate_suggestions(posts: list, shop_domain: str, limit: int = 40) -> dict:
    """
    Returns keyword suggestions categorised by type.
    posts: list of BlogPost ORM objects for the store.
    """
    from app.agents.base import get_client, get_model
    client = get_client()

    themes_text = _extract_store_themes(posts)
    existing_kws = list({p.focus_keyword for p in posts if p.focus_keyword})[:20]

    related_from_serper = _serper_related(existing_kws or [shop_domain.split(".")[0]], settings.SERPER_API_KEY)
    serper_text = ("\nTrending related searches found online:\n" + "\n".join(f"- {r}" for r in related_from_serper)) if related_from_serper else ""

    prompt = f"""You are an expert SEO strategist. Based on this Shopify store's existing content, generate targeted keyword suggestions.

Store domain: {shop_domain}

Store content summary:
{themes_text}
{serper_text}

Return a JSON object with these keys:
{{
  "store_niche": "1-sentence description of what this store sells",
  "priority_keywords": [
    {{"keyword": "...", "intent": "commercial|informational|navigational", "difficulty": "low|medium|high", "reason": "why this keyword fits"}}
  ],
  "long_tail": [
    {{"keyword": "...", "intent": "...", "difficulty": "low|medium|high", "reason": "..."}}
  ],
  "trending_topics": [
    {{"topic": "...", "suggested_title": "...", "reason": "why trending now"}}
  ],
  "content_gaps": [
    {{"keyword": "...", "reason": "what the store is missing"}}
  ]
}}

Rules:
- priority_keywords: 8-10 high-value keywords directly related to the store's niche
- long_tail: 10-15 specific long-tail keywords with lower competition
- trending_topics: 5 timely/seasonal content ideas relevant to the niche
- content_gaps: 5 keywords competitors likely rank for that this store doesn't cover
- Be specific and realistic. Focus on keywords that would actually drive buyers.
- Return only valid JSON."""

    resp = client.chat.completions.create(
        model=get_model("research"),
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(resp.choices[0].message.content)
