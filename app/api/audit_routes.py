import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.blog_post import BlogPost
from app.services.seo_auditor import SeoAuditor
from app.services.ranking_checker import RankingChecker
from app.config import settings

audit_router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@audit_router.get("/posts")
def audit_all_posts(db: Session = Depends(get_db)):
    """SEO audit all blog posts. Returns scores + issues."""
    posts = db.query(BlogPost).all()
    if not posts:
        return []
    auditor = SeoAuditor()
    results = [auditor.audit_post(p) for p in posts]
    return sorted(results, key=lambda x: x["score"])


@audit_router.get("/posts/{post_id}")
def audit_single_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    return SeoAuditor().audit_post(post)


class RankingsRequest(BaseModel):
    keywords: List[str]
    shop_domain: Optional[str] = None


@audit_router.post("/rankings")
async def check_rankings(body: RankingsRequest, db: Session = Depends(get_db)):
    """Check Google ranking positions for given keywords."""
    shop = (body.shop_domain or settings.SHOPIFY_SHOP_DOMAIN or "").strip()
    if not shop:
        raise HTTPException(422, "shop_domain required (or set SHOPIFY_SHOP_DOMAIN env var)")
    keywords = [k.strip() for k in body.keywords if k.strip()]
    if not keywords:
        raise HTTPException(422, "Provide at least one keyword")
    checker = RankingChecker(shop)
    results = await checker.check_many(keywords)
    # Sort: ranking first (by position), then not-ranking
    ranking = sorted([r for r in results if r["position"]], key=lambda x: x["position"])
    not_ranking = [r for r in results if not r["position"]]
    return {"shop": shop, "results": ranking + not_ranking}


class PlanRequest(BaseModel):
    rankings: List[dict]
    shop_domain: Optional[str] = None


@audit_router.post("/plan")
def generate_ranking_plan(body: PlanRequest, db: Session = Depends(get_db)):
    """Use GPT-4o to generate a prioritized ranking improvement plan."""
    from openai import OpenAI
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    posts = db.query(BlogPost).limit(30).all()
    posts_text = "\n".join(
        f"- [ID:{p.id}] \"{p.title}\" (keyword: {p.focus_keyword or 'none'}, status: {p.status})"
        for p in posts
    )

    rankings_text = "\n".join(
        f"- \"{r['keyword']}\": " + (f"position {r['position']} at {r.get('ranking_url', '')}" if r.get('position') else "not ranking")
        for r in body.rankings
    )

    prompt = f"""You are a senior SEO strategist. Analyze these keyword rankings and existing blog posts, then create a data-driven action plan.

KEYWORD RANKINGS:
{rankings_text}

EXISTING BLOG POSTS:
{posts_text}

Return a JSON object with exactly these keys:
{{
  "summary": "2-3 sentence overall assessment",
  "quick_wins": [
    {{"keyword": "...", "current_position": 12, "target_position": 3, "post_id": 5, "post_title": "...", "action": "specific update recommendation", "effort": "low|medium|high"}}
  ],
  "content_updates": [
    {{"post_id": 5, "post_title": "...", "recommendations": ["add FAQ section", "increase word count to 1500+", "add internal links"]}}
  ],
  "new_articles": [
    {{"keyword": "...", "reason": "...", "suggested_title": "...", "estimated_impact": "high|medium|low"}}
  ],
  "priority_actions": ["action 1", "action 2", "action 3", "action 4", "action 5"]
}}

Be specific and actionable. Base recommendations on actual ranking data."""

    resp = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        max_tokens=2500,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(resp.choices[0].message.content)


# ── Google Search Console ─────────────────────────────────────────────────────

@audit_router.get("/gsc/status")
def gsc_status():
    """Check if GSC is configured and credentials are valid."""
    from app.services import gsc_client as gsc
    if not gsc.is_configured():
        return {
            "configured": False,
            "message": "Set GOOGLE_SERVICE_ACCOUNT_JSON and GSC_SITE_URL in Railway env vars.",
        }
    try:
        gsc._get_credentials()
        return {"configured": True, "site_url": settings.GSC_SITE_URL}
    except Exception as e:
        return {"configured": False, "error": str(e)}


@audit_router.get("/gsc/overview")
def gsc_overview(days: int = 28):
    from app.services import gsc_client as gsc
    if not gsc.is_configured():
        raise HTTPException(422, "GSC not configured")
    try:
        return gsc.get_overview(days)
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/pages")
def gsc_top_pages(days: int = 28, limit: int = 25):
    from app.services import gsc_client as gsc
    if not gsc.is_configured():
        raise HTTPException(422, "GSC not configured")
    try:
        return gsc.get_top_pages(days, limit)
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/queries")
def gsc_top_queries(days: int = 28, limit: int = 25, page: Optional[str] = None):
    from app.services import gsc_client as gsc
    if not gsc.is_configured():
        raise HTTPException(422, "GSC not configured")
    try:
        return gsc.get_top_queries(days, limit, page)
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/opportunities")
def gsc_opportunities(days: int = 28):
    from app.services import gsc_client as gsc
    if not gsc.is_configured():
        raise HTTPException(422, "GSC not configured")
    try:
        return gsc.get_opportunities(days)
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/sparkline")
def gsc_sparkline(days: int = 90):
    from app.services import gsc_client as gsc
    if not gsc.is_configured():
        raise HTTPException(422, "GSC not configured")
    try:
        return gsc.get_sparkline(days)
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")
