import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.blog_post import BlogPost
from app.services.auth_service import check_store_scope, get_current_user, get_user_shops
from app.services.seo_auditor import SeoAuditor
from app.services.ranking_checker import RankingChecker
from app.config import settings

audit_router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@audit_router.get("/posts")
def audit_all_posts(
    shop_domain: Optional[str] = Query(None, description="Filter by shop domain"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SEO audit blog posts for the active store (or all stores the user can access)."""
    q = db.query(BlogPost)
    if shop_domain:
        check_store_scope(user, shop_domain, "audit", db)
        q = q.filter(BlogPost.shop_domain == shop_domain)
    elif user.role != "admin":
        shops = get_user_shops(user, db)
        if not shops:
            return []
        q = q.filter(BlogPost.shop_domain.in_(shops))
    posts = q.all()
    if not posts:
        return []
    auditor = SeoAuditor()
    results = [auditor.audit_post(p) for p in posts]
    return sorted(results, key=lambda x: x["score"])


@audit_router.get("/posts/{post_id}")
def audit_single_post(
    post_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "audit", db)
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


class VolumeRequest(BaseModel):
    keywords: List[str]
    language_code: str = "en"
    location_code: int = 2840


@audit_router.post("/volume")
async def get_keyword_volumes(body: VolumeRequest, user=Depends(get_current_user)):
    """Fetch monthly search volume for a list of keywords via DataForSEO."""
    from app.services.volume_service import get_search_volumes
    kws = [k.strip() for k in body.keywords if k.strip()]
    if not kws:
        raise HTTPException(422, "Provide at least one keyword")
    data = await get_search_volumes(kws, body.language_code, body.location_code)
    if not data and kws:
        return {"configured": False, "message": "Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD to enable search volume.", "results": {}}
    return {"configured": True, "results": data}


class PlanRequest(BaseModel):
    rankings: List[dict]
    shop_domain: Optional[str] = None


@audit_router.post("/plan")
def generate_ranking_plan(body: PlanRequest, db: Session = Depends(get_db)):
    """Use GPT-4o to generate a prioritized ranking improvement plan."""
    from openai import OpenAI
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    posts_q = db.query(BlogPost)
    if body.shop_domain:
        posts_q = posts_q.filter(BlogPost.shop_domain == body.shop_domain)
    posts = posts_q.limit(30).all()
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

def _gsc(shop_domain: Optional[str], db):
    from app.services.gsc_client import get_client_for_brand
    client = get_client_for_brand(shop_domain, db)
    if not client:
        raise HTTPException(422, "GSC not configured for this brand. Connect via Brand Profile → Search Console.")
    return client


@audit_router.get("/gsc/sites")
def gsc_list_sites(shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    """List all GSC properties accessible with the current credentials."""
    try:
        return _gsc(shop_domain, db).list_sites()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/status")
def gsc_status(shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    from app.services.gsc_client import get_client_for_brand
    client = get_client_for_brand(shop_domain, db)
    if not client:
        return {
            "configured": False,
            "message": "Connect Google Search Console in Brand Profile settings.",
        }
    try:
        client._headers()   # test credentials
        return {"configured": True, "site_url": client.site_url}
    except Exception as e:
        return {"configured": False, "error": str(e)}


@audit_router.get("/gsc/overview")
def gsc_overview(days: int = 28, shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        return _gsc(shop_domain, db).get_overview(days)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/pages")
def gsc_top_pages(days: int = 28, limit: int = 25, shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        return _gsc(shop_domain, db).get_top_pages(days, limit)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/queries")
def gsc_top_queries(days: int = 28, limit: int = 25, page: Optional[str] = None,
                    shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        return _gsc(shop_domain, db).get_top_queries(days, limit, page)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/opportunities")
def gsc_opportunities(days: int = 28, shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        return _gsc(shop_domain, db).get_opportunities(days)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")


@audit_router.get("/gsc/sparkline")
def gsc_sparkline(days: int = 90, shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        return _gsc(shop_domain, db).get_sparkline(days)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GSC API error: {e}")
