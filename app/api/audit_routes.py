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


def _audit_to_instructions(audit: dict) -> tuple[str, int | None]:
    """
    Convert audit result into (instruction_string, target_word_count).
    Returns target_word_count so callers can enforce it in the rewrite.
    """
    import re as _re
    lines = ["Fix ALL SEO issues listed below. Apply every fix precisely:"]
    target_word_count = None

    if audit.get("cta_external_leaks"):
        leaks = ", ".join(f'"{t}"' for t in audit["cta_external_leaks"])
        lines.append(
            f"1. CTA LINK FIX (critical): The phrases {leaks} are linked to external domains. "
            "Replace every external link on CTA/navigational phrases with an internal link "
            "(use an existing related article URL, or remove entirely). "
            "NEVER link 'learn more / read more / discover / explore / find out / check out' "
            "to any external site."
        )

    for i, issue in enumerate(audit.get("issues", []), start=2):
        lower = issue.lower()

        if "word" in lower and ("short" in lower or "only" in lower):
            # Extract current count and calculate deficit
            m = _re.search(r'(\d+)\s+word', issue)
            current_wc = int(m.group(1)) if m else audit.get("word_count", 0)
            target_word_count = 1500
            deficit = max(0, target_word_count - current_wc)
            h2s = max(1, audit.get("h2_count", 3))
            per_sec = max(80, deficit // h2s)
            lines.append(
                f"{i}. WORD COUNT (critical): Article has {current_wc} words — MUST reach "
                f"{target_word_count}+ words. Add ≈{deficit} more words:\n"
                f"   • Expand each of the {h2s} H2 sections by ≥{per_sec} words with "
                f"real examples, data, comparisons, or sub-points\n"
                f"   • Expand FAQ answers to ≥80 words each\n"
                f"   • Add 1-2 new H2 sections on related subtopics if body is still short\n"
                f"   NO filler sentences — every added word must be informative."
            )

        elif "keyword" in lower and "title" in lower:
            lines.append(
                f"{i}. TITLE KEYWORD: Rewrite the SEO title to naturally include the focus keyword "
                f"'{audit.get('focus_keyword', '')}' within 50-60 characters."
            )
        elif "density" in lower and "low" in lower:
            lines.append(
                f"{i}. KEYWORD DENSITY: Increase natural usage of '{audit.get('focus_keyword', '')}' "
                "throughout the article (target 1-3%). Sprinkle it in headings, intro, body, and FAQ."
            )
        elif "density" in lower and "high" in lower:
            lines.append(
                f"{i}. KEYWORD DENSITY: Reduce keyword stuffing for '{audit.get('focus_keyword', '')}'. "
                "Replace repeated exact-matches with synonyms and related phrases."
            )
        elif "h2" in lower or "heading" in lower:
            lines.append(
                f"{i}. HEADINGS: Add at least 3 descriptive H2 section headings. "
                "Each heading should include keyword variations and clearly label a distinct section."
            )
        elif "internal link" in lower:
            lines.append(
                f"{i}. INTERNAL LINKS: Add 2-4 internal links using descriptive anchor text "
                "(not 'click here'). Link to related articles or products on the same site."
            )
        elif "alt text" in lower or "missing alt" in lower:
            lines.append(
                f"{i}. IMAGE ALT TEXT: Add descriptive alt text to every image. "
                "Include the focus keyword in the featured image alt text where natural."
            )
        elif "featured image" in lower:
            lines.append(f"{i}. FEATURED IMAGE: Ensure a featured image is set for this article.")
        elif "title" in lower and "char" in lower:
            m = _re.search(r'(\d+)\s+char', issue)
            cur_len = int(m.group(1)) if m else 0
            action = "Shorten" if cur_len > 60 else "Lengthen"
            lines.append(
                f"{i}. TITLE LENGTH ({cur_len} chars): {action} the SEO title to 50-60 characters "
                "for optimal Google display. Keep the focus keyword front-loaded."
            )
        else:
            lines.append(f"{i}. {issue}")

    for w in audit.get("warnings", []):
        lower_w = w.lower()
        if "semantic" in lower_w or "coverage" in lower_w:
            kws = audit.get("semantic_keywords", [])
            if kws:
                lines.append(
                    f"SEMANTIC KEYWORDS: Weave more of these related terms naturally into "
                    f"the content (don't stuff, use contextually): {', '.join(kws[:8])}."
                )
        elif "rel=" in lower_w or "noopener" in lower_w:
            lines.append(
                'EXTERNAL LINKS: Add target="_blank" rel="noopener noreferrer" to all external links.'
            )

    lines.append(
        "\nCRITICAL RULES:\n"
        "- Apply EVERY fix above without exception\n"
        "- Keep the article's main topic, tone, and overall structure intact\n"
        "- Do NOT change the article title (only the SEO meta title if instructed)\n"
        "- Return the complete rewritten article, not a summary or partial version"
    )
    return "\n".join(lines), target_word_count


@audit_router.get("/pre-publish/{post_id}")
def pre_publish_audit(
    post_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run independent SEO audit on a draft before publishing. Blocks/warns on issues."""
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "audit", db)
    result = SeoAuditor().audit_post(post)
    result["ready_to_publish"] = (
        result["score"] >= 70
        and not result["cta_external_leaks"]
        and len(result["issues"]) == 0
    )
    return result


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


@audit_router.post("/posts/{post_id}/fix")
async def auto_fix_post(
    post_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Auto-fix SEO issues: audit → generate instructions → rewrite → save draft."""
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "write", db)

    # 1. Audit current state
    auditor = SeoAuditor()
    audit   = auditor.audit_post(post)

    # Persist issues to KB so Learning Agent learns from them
    auditor.save_to_kb(audit, post.shop_domain, db)

    if not audit["issues"] and not audit.get("cta_external_leaks"):
        return {"message": "No issues found — post is already optimised.", "audit": audit, "changed": False}

    # 2. Build fix instructions (returns instructions + optional word count target)
    instructions, target_word_count = _audit_to_instructions(audit)

    # 3. Load brand profile
    brand_profile = None
    try:
        from app.models.brand_profile import BrandProfile
        bp = db.query(BrandProfile).filter_by(shop_domain=post.shop_domain).first()
        if not bp and post.shop_domain:
            bp = db.query(BrandProfile).filter_by(shop_domain=None).first()
        if bp:
            brand_profile = {
                "brand_name": bp.brand_name, "brand_style": bp.brand_style,
                "brand_description": bp.brand_description, "tone_of_voice": bp.tone_of_voice,
                "output_requirements": bp.output_requirements,
            }
    except Exception:
        pass

    # 4. Rewrite — pass target_word_count so rewrite() can enforce it with expansion loop
    from app.services.content_writer import ContentWriter
    from app.models.blog_post import PostStatus
    writer = ContentWriter()
    result = await writer.rewrite(
        post=post,
        instructions=instructions,
        brand_profile=brand_profile,
        target_word_count=target_word_count,
    )

    # 5. Save
    post.content_html    = result["content_html"]
    post.seo_title       = result["seo_title"]
    post.seo_description = result["seo_description"]
    post.tags            = result["tags"]
    if result.get("image_prompt"):
        post.image_prompt = result["image_prompt"]
    post.status = PostStatus.DRAFT
    db.commit()
    db.refresh(post)

    # 6. Re-audit to show improvement
    new_audit = auditor.audit_post(post)

    # Trigger Learning Agent to synthesize fresh lessons from the updated KB
    try:
        from app.agents.learning_agent import LearningAgent
        await LearningAgent().synthesize_lessons(post.shop_domain, db)
    except Exception:
        pass  # non-critical; lessons will be synthesized on next pipeline run

    return {
        "changed": True,
        "post_id": post.id,
        "instructions_used": instructions,
        "before": {"score": audit["score"], "grade": audit["grade"], "issues": audit["issues"]},
        "after":  {"score": new_audit["score"], "grade": new_audit["grade"], "issues": new_audit["issues"]},
        "usage": result.get("usage"),
    }


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


@audit_router.get("/volume/test")
async def test_volume_connection(
    keyword: str = "seo",
    user=Depends(get_current_user),
):
    """Debug endpoint — tests DataForSEO credentials and shows raw response."""
    from app.services.volume_service import test_connection
    return await test_connection(keyword)


@audit_router.post("/volume")
async def get_keyword_volumes(body: VolumeRequest, user=Depends(get_current_user)):
    """Fetch monthly search volume for a list of keywords via DataForSEO."""
    from app.services.volume_service import get_search_volumes, is_configured
    if not is_configured():
        return {"configured": False, "message": "Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in environment variables.", "results": {}}
    kws = [k.strip() for k in body.keywords if k.strip()]
    if not kws:
        raise HTTPException(422, "Provide at least one keyword")
    try:
        data = await get_search_volumes(kws, body.language_code, body.location_code)
        return {"configured": True, "results": data}
    except Exception as e:
        raise HTTPException(502, f"DataForSEO error: {e}")


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
