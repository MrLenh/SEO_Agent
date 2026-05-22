import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.keyword import TopicCluster
from app.schemas.content import (
    GenerateArticleRequest,
    GeneratedArticleOut,
    KeywordResearchOut,
    KeywordResearchRequest,
    TopicPlanRequest,
)
from app.services.content_writer import ContentWriter
from app.services.keyword_analyzer import KeywordAnalyzer
from app.services.topic_planner import TopicPlanner

research_router = APIRouter(prefix="/api/v1/research", tags=["keyword-research"])
generate_router = APIRouter(prefix="/api/v1/generate", tags=["content-generation"])
topics_router = APIRouter(prefix="/api/v1/topics", tags=["topics"])


# ── Keyword Research ──────────────────────────────────────────────────────────

@research_router.post("/", response_model=KeywordResearchOut)
async def research_keyword(body: KeywordResearchRequest):
    """
    Research a keyword: returns People Also Ask, related searches, top SERP results.
    Use this before planning a topic cluster or generating an article.
    """
    analyzer = KeywordAnalyzer()
    result = await analyzer.research(body.keyword, body.country, body.language)
    return result


# ── Topic Planning ────────────────────────────────────────────────────────────

@topics_router.post("/plan")
async def plan_topic_cluster(body: TopicPlanRequest, db: Session = Depends(get_db)):
    """
    Research keyword → Claude generates a full topic cluster plan:
    1 pillar article + 5-7 supporting articles with outlines.
    Saves the cluster to DB for tracking.
    """
    planner = TopicPlanner()
    cluster = await planner.plan(body.seed_keyword, body.country, body.language, db=db)
    return cluster


@topics_router.get("/")
def list_topic_clusters(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all planned topic clusters."""
    clusters = (
        db.query(TopicCluster)
        .order_by(TopicCluster.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return clusters


@topics_router.get("/{cluster_id}")
def get_topic_cluster(cluster_id: int, db: Session = Depends(get_db)):
    cluster = db.query(TopicCluster).filter(TopicCluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Topic cluster not found")
    return cluster


# ── Content Generation ────────────────────────────────────────────────────────

@generate_router.post("/article")
async def generate_article(body: GenerateArticleRequest, db: Session = Depends(get_db)):
    """
    Generate a full SEO article using Claude.

    - Automatically finds related existing posts for internal linking
    - Injects external authority links from SERP data (if paa provided)
    - Saves as DRAFT in DB (does not publish to Shopify yet)
    - Returns full content + SEO meta + DALL-E image_prompt
    """
    writer = ContentWriter()

    # Fetch external refs for the focus keyword if Serper is configured
    external_refs = []
    try:
        analyzer = KeywordAnalyzer()
        research = await analyzer.research(body.focus_keyword)
        external_refs = research.get("top_results", [])
        paa = research.get("people_also_ask", []) + body.paa_questions
    except Exception:
        paa = body.paa_questions

    # Generate slug from title
    slug = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")

    # Load brand profile for the store
    brand_profile = None
    try:
        from app.models.brand_profile import BrandProfile
        bp = db.query(BrandProfile).filter_by(shop_domain=body.shop_domain).first()
        if not bp and body.shop_domain:
            bp = db.query(BrandProfile).filter_by(shop_domain=None).first()
        if bp:
            brand_profile = {
                "brand_name": bp.brand_name,
                "brand_style": bp.brand_style,
                "brand_description": bp.brand_description,
                "tone_of_voice": bp.tone_of_voice,
                "output_requirements": bp.output_requirements,
            }
    except Exception:
        pass

    # Collect improvement lessons from past feedback for this store
    feedback_lessons = []
    try:
        from app.models.article_feedback import ArticleFeedback
        past = (
            db.query(ArticleFeedback)
            .filter(
                ArticleFeedback.shop_domain == body.shop_domain,
                ArticleFeedback.improvement_notes != "",
                ArticleFeedback.improvement_notes.isnot(None),
            )
            .order_by(ArticleFeedback.created_at.desc())
            .limit(10)
            .all()
        )
        # Weight by rating: low-rated feedback is most important to learn from
        for f in past:
            if f.improvement_notes and f.improvement_notes.strip():
                prefix = "⚠️ Avoid" if f.rating <= 2 else ("✅ Keep" if f.rating >= 4 else "💡 Note")
                feedback_lessons.append(f"{prefix} (rated {f.rating}/5): {f.improvement_notes.strip()}")
    except Exception:
        pass

    result = await writer.write(
        title=body.title,
        focus_keyword=body.focus_keyword,
        outline=body.outline,
        paa_questions=paa,
        external_refs=external_refs,
        language=body.language,
        tone=body.tone,
        word_count=body.word_count,
        db=db,
        exclude_slug=slug,
        brand_profile=brand_profile,
        feedback_lessons=feedback_lessons,
    )

    # Save draft to DB
    post = writer.save_draft(
        db=db,
        title=body.title,
        slug=slug,
        focus_keyword=body.focus_keyword,
        result=result,
        platform=body.platform,
        channel_id=body.blog_channel_id,
        cluster_id=body.cluster_id,
    )

    return {
        "id": post.id,
        "title": post.title,
        "slug": post.slug,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "tags": post.tags,
        "image_prompt": result["image_prompt"],
        "platform_url": post.platform_url,
        "status": post.status,
        "source": post.source,
        "internal_links_count": len(result["internal_links"]),
        "usage": result["usage"],
        "content_preview": result["content_html"][:500] + "...",
    }


@generate_router.post("/article/{post_id}/rewrite")
async def rewrite_article(
    post_id: int,
    body: "_RewriteBody",
    db: Session = Depends(get_db),
):
    from app.models.blog_post import BlogPost, PostStatus
    from app.models.brand_profile import BrandProfile
    from app.models.article_feedback import ArticleFeedback

    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")

    # Load brand profile
    brand_profile = None
    shop = body.shop_domain
    bp = db.query(BrandProfile).filter_by(shop_domain=shop).first()
    if not bp and shop:
        bp = db.query(BrandProfile).filter_by(shop_domain=None).first()
    if bp:
        brand_profile = {
            "brand_name": bp.brand_name, "brand_style": bp.brand_style,
            "brand_description": bp.brand_description, "tone_of_voice": bp.tone_of_voice,
            "output_requirements": bp.output_requirements,
        }

    # Load feedback lessons
    feedback_lessons = []
    try:
        past = (
            db.query(ArticleFeedback)
            .filter(
                ArticleFeedback.shop_domain == shop,
                ArticleFeedback.improvement_notes != "",
                ArticleFeedback.improvement_notes.isnot(None),
            )
            .order_by(ArticleFeedback.created_at.desc())
            .limit(10).all()
        )
        for f in past:
            if f.improvement_notes and f.improvement_notes.strip():
                prefix = "⚠️ Avoid" if f.rating <= 2 else ("✅ Keep" if f.rating >= 4 else "💡 Note")
                feedback_lessons.append(f"{prefix} (rated {f.rating}/5): {f.improvement_notes.strip()}")
    except Exception:
        pass

    writer = ContentWriter()
    result = await writer.rewrite(
        post=post,
        instructions=body.instructions,
        brand_profile=brand_profile,
        feedback_lessons=feedback_lessons,
    )

    # Update post in DB
    post.content_html    = result["content_html"]
    post.seo_title       = result["seo_title"]
    post.seo_description = result["seo_description"]
    post.tags            = result["tags"]
    if result["image_prompt"]:
        post.image_prompt = result["image_prompt"]
    post.status = PostStatus.DRAFT
    db.commit()
    db.refresh(post)

    return {
        "id": post.id,
        "title": post.title,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "tags": post.tags,
        "content_html": post.content_html,
        "usage": result["usage"],
    }


from pydantic import BaseModel as _Base


class _RewriteBody(_Base):
    instructions: str
    shop_domain: str = ""


@generate_router.get("/article/{post_id}/content")
def get_article_content(post_id: int, db: Session = Depends(get_db)):
    """Get full HTML content of a generated article."""
    from app.models.blog_post import BlogPost
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return {
        "id": post.id,
        "title": post.title,
        "content_html": post.content_html,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "tags": post.tags,
        "status": post.status,
    }


# ── Edit / Delete draft ───────────────────────────────────────────────────────

class _UpdateBody(_Base):
    title: str = ""
    content_html: str = ""
    seo_title: str = ""
    seo_description: str = ""
    tags: list[str] = []


@generate_router.put("/article/{post_id}")
def update_article(post_id: int, body: _UpdateBody, db: Session = Depends(get_db)):
    """Update a draft article's content and metadata."""
    from app.models.blog_post import BlogPost, PostStatus
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.status != PostStatus.DRAFT:
        raise HTTPException(422, "Only draft posts can be edited")

    if body.title:
        post.title = body.title
    if body.content_html:
        post.content_html = body.content_html
    if body.seo_title:
        post.seo_title = body.seo_title
    if body.seo_description:
        post.seo_description = body.seo_description
    post.tags = body.tags

    db.commit()
    db.refresh(post)
    return {
        "id": post.id,
        "title": post.title,
        "content_html": post.content_html,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "tags": post.tags,
        "status": post.status,
    }


@generate_router.delete("/article/{post_id}")
def delete_article(post_id: int, db: Session = Depends(get_db)):
    """Delete a draft article permanently."""
    from app.models.blog_post import BlogPost, PostStatus
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.status != PostStatus.DRAFT:
        raise HTTPException(422, "Only draft posts can be deleted here")
    db.delete(post)
    db.commit()
    return {"deleted": post_id}


# ── Feedback ──────────────────────────────────────────────────────────────────

class FeedbackBody(_Base):
    rating: int                          # 1–5
    feedback_text: str = ""
    improvement_notes: str = ""
    shop_domain: str = ""


@generate_router.post("/article/{post_id}/feedback")
def submit_feedback(
    post_id: int,
    body: FeedbackBody,
    db: Session = Depends(get_db),
):
    from app.models.article_feedback import ArticleFeedback
    from app.models.blog_post import BlogPost
    from app.services.auth_service import get_current_user
    from fastapi import Header
    import re

    if not 1 <= body.rating <= 5:
        raise HTTPException(422, "Rating must be 1–5")
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")

    fb = ArticleFeedback(
        post_id=post_id,
        shop_domain=body.shop_domain or None,
        rating=body.rating,
        feedback_text=body.feedback_text,
        improvement_notes=body.improvement_notes,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return {"id": fb.id, "rating": fb.rating, "created_at": fb.created_at}


@generate_router.get("/article/{post_id}/feedback")
def get_feedback(post_id: int, db: Session = Depends(get_db)):
    from app.models.article_feedback import ArticleFeedback
    items = (
        db.query(ArticleFeedback)
        .filter_by(post_id=post_id)
        .order_by(ArticleFeedback.created_at.desc())
        .all()
    )
    return [
        {
            "id": f.id,
            "rating": f.rating,
            "feedback_text": f.feedback_text,
            "improvement_notes": f.improvement_notes,
            "created_at": f.created_at,
        }
        for f in items
    ]
