import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.keyword import TopicCluster
from app.schemas.content import (
    GenerateArticleRequest,
    GeneratedArticleOut,
    KeywordResearchOut,
    KeywordResearchRequest,
    TitleSuggestionRequest,
    TopicPlanRequest,
)
from app.services.auth_service import check_store_scope, get_current_user
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
    result = {
        "id": cluster.id,
        "seed_keyword": cluster.seed_keyword,
        "cluster_name": cluster.cluster_name,
        "status": cluster.status,
        "created_at": cluster.created_at,
        "questions": cluster.questions or [],
    }
    if cluster.plan_json:
        result.update(cluster.plan_json)
        result["id"] = cluster.id  # ensure id not overwritten
    else:
        import json as _json
        try:
            result["pillar"] = _json.loads(cluster.description or "{}")
        except Exception:
            result["pillar"] = {}
        result["supporting_articles"] = []
    return result


from pydantic import BaseModel as _PydBase


class _AddKeywordBody(_PydBase):
    keyword: str
    country: str = "vn"
    language: str = "vi"


@topics_router.get("/{cluster_id}/keywords")
def list_cluster_keywords(cluster_id: int, db: Session = Depends(get_db)):
    from app.models.keyword import Keyword
    if not db.query(TopicCluster).filter(TopicCluster.id == cluster_id).first():
        raise HTTPException(404, "Cluster not found")
    kws = db.query(Keyword).filter(Keyword.topic_cluster_id == cluster_id).all()
    return [
        {
            "id": k.id,
            "keyword": k.keyword,
            "current_rank": k.current_rank,
            "prev_rank": k.prev_rank,
            "volume": k.volume,
            "difficulty": k.difficulty,
            "cpc": k.cpc,
            "country": k.country,
            "language": k.language,
            "status": k.status,
            "last_checked": k.last_checked,
        }
        for k in kws
    ]


@topics_router.post("/{cluster_id}/keywords")
async def add_cluster_keyword(cluster_id: int, body: _AddKeywordBody, db: Session = Depends(get_db)):
    from app.models.keyword import Keyword, KeywordStatus
    if not db.query(TopicCluster).filter(TopicCluster.id == cluster_id).first():
        raise HTTPException(404, "Cluster not found")
    kw_text = body.keyword.strip()
    existing = db.query(Keyword).filter_by(topic_cluster_id=cluster_id, keyword=kw_text).first()
    if existing:
        return {"id": existing.id, "keyword": existing.keyword, "volume": existing.volume,
                "cpc": existing.cpc, "already_exists": True}
    kw = Keyword(
        keyword=kw_text,
        topic_cluster_id=cluster_id,
        country=body.country,
        language=body.language,
        status=KeywordStatus.TRACKED,
    )
    db.add(kw)
    db.commit()
    db.refresh(kw)

    # Auto-fetch search volume from DataForSEO if configured
    from app.services.volume_service import get_search_volumes
    lang = (body.language or "en")[:2].lower()
    vol_data = await get_search_volumes([kw_text], language_code=lang)
    if vol_data.get(kw_text):
        v = vol_data[kw_text]
        kw.volume = v.get("volume")
        kw.difficulty = round((v.get("competition") or 0) * 100, 1)  # 0–100 scale
        kw.cpc = v.get("cpc")
        db.commit()
        db.refresh(kw)

    return {
        "id": kw.id, "keyword": kw.keyword,
        "country": kw.country, "language": kw.language,
        "volume": kw.volume, "difficulty": kw.difficulty, "cpc": kw.cpc,
    }


class _PatchKeywordBody(_PydBase):
    volume: Optional[int] = None
    difficulty: Optional[float] = None
    cpc: Optional[float] = None
    current_rank: Optional[int] = None


@topics_router.patch("/{cluster_id}/keywords/{kw_id}")
def update_cluster_keyword(cluster_id: int, kw_id: int, body: _PatchKeywordBody, db: Session = Depends(get_db)):
    from app.models.keyword import Keyword
    kw = db.query(Keyword).filter_by(id=kw_id, topic_cluster_id=cluster_id).first()
    if not kw:
        raise HTTPException(404, "Keyword not found")
    if body.volume is not None:
        kw.volume = body.volume
    if body.difficulty is not None:
        kw.difficulty = body.difficulty
    if body.cpc is not None:
        kw.cpc = body.cpc
    if body.current_rank is not None:
        kw.prev_rank = kw.current_rank
        kw.current_rank = body.current_rank
    db.commit()
    return {"id": kw.id, "keyword": kw.keyword, "volume": kw.volume, "cpc": kw.cpc, "difficulty": kw.difficulty}


@topics_router.delete("/{cluster_id}/keywords/{kw_id}")
def remove_cluster_keyword(cluster_id: int, kw_id: int, db: Session = Depends(get_db)):
    from app.models.keyword import Keyword
    kw = db.query(Keyword).filter_by(id=kw_id, topic_cluster_id=cluster_id).first()
    if not kw:
        raise HTTPException(404, "Keyword not found")
    db.delete(kw)
    db.commit()
    return {"deleted": kw_id}


# ── Content Generation ────────────────────────────────────────────────────────

@generate_router.post("/title")
def suggest_titles(body: TitleSuggestionRequest, user=Depends(get_current_user)):
    """Generate SEO-friendly title suggestions for a focus keyword."""
    if not body.focus_keyword.strip():
        raise HTTPException(422, "focus_keyword is required")
    titles = ContentWriter().suggest_titles(
        focus_keyword=body.focus_keyword.strip(),
        language=body.language,
        market=body.market,
        article_type=body.article_type,
        notes=body.notes,
        count=max(1, min(body.count, 10)),
    )
    if not titles:
        raise HTTPException(502, "Title suggestion failed — model returned no parseable output")
    return {"titles": titles}


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
        shop_domain=body.shop_domain or None,
        notes=body.notes,
        market=body.market,
        article_type=body.article_type,
        target_platform=body.target_platform or "google",
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
        shop_domain=body.shop_domain or None,
    )

    return {
        "id": post.id,
        "title": post.title,
        "slug": post.slug,
        "seo_title": post.seo_title,
        "seo_description": post.seo_description,
        "tags": post.tags,
        "image_prompt": result["image_prompt"],
        "image_url": result.get("image_url"),
        "platform_url": post.platform_url,
        "status": post.status,
        "source": post.source,
        "internal_links_count": len(result["internal_links"]),
        "usage": result["usage"],
        "content_preview": result["content_html"][:500] + "...",
    }


@generate_router.post("/article/{post_id}/regenerate-image")
async def regenerate_image(
    post_id: int,
    body: "_RegenerateImageBody",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a new image for a draft post. slot='featured' or a custom label."""
    from app.models.blog_post import BlogPost
    from app.services.image_generator import ImageGenerator

    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "write", db)

    prompt = body.prompt or post.image_prompt or f"Professional blog banner for: {post.title}"
    size = body.size if body.size in {"1024x1024", "1536x1024", "1024x1536"} else "1536x1024"

    img = ImageGenerator().generate(prompt, size=size)
    url = img.get("url")

    if not body.slot or body.slot == "featured":
        post.featured_image_url = url
        post.featured_image_alt = post.title
        post.image_prompt = prompt
    else:
        extra = list(post.extra_images or [])
        existing = next((e for e in extra if e.get("label") == body.slot), None)
        if existing:
            existing["url"] = url
            existing["prompt"] = prompt
        else:
            extra.append({"label": body.slot, "prompt": prompt, "url": url})
        post.extra_images = extra

    db.commit()
    return {"image_url": url, "slot": body.slot or "featured", "prompt": prompt}


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


class _RegenerateImageBody(_Base):
    slot: str = "featured"          # 'featured' or a custom section label
    prompt: Optional[str] = None    # override; falls back to post.image_prompt
    size: str = "1536x1024"


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
        "image_prompt": post.image_prompt,
        "featured_image_url": post.featured_image_url,
        "extra_images": post.extra_images or [],
    }


# ── Edit / Delete draft ───────────────────────────────────────────────────────

class _UpdateBody(_Base):
    title: str = ""
    content_html: str = ""
    seo_title: str = ""
    seo_description: str = ""
    tags: list[str] = []


@generate_router.put("/article/{post_id}")
def update_article(
    post_id: int,
    body: _UpdateBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a draft article's content and metadata. Records an edit history entry."""
    import difflib

    from app.models.article_edit_history import ArticleEditHistory
    from app.models.blog_post import BlogPost, PostStatus

    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "write", db)
    if post.status != PostStatus.DRAFT:
        raise HTTPException(422, "Only draft posts can be edited")

    # Snapshot before applying
    before = {
        "title":           post.title,
        "content_html":    post.content_html or "",
        "seo_title":       post.seo_title,
        "seo_description": post.seo_description,
        "tags":            list(post.tags or []),
    }

    if body.title:
        post.title = body.title
    if body.content_html:
        post.content_html = body.content_html
    if body.seo_title:
        post.seo_title = body.seo_title
    if body.seo_description:
        post.seo_description = body.seo_description
    post.tags = body.tags

    after = {
        "title":           post.title,
        "content_html":    post.content_html or "",
        "seo_title":       post.seo_title,
        "seo_description": post.seo_description,
        "tags":            list(post.tags or []),
    }

    diff_lines = list(difflib.unified_diff(
        before["content_html"].splitlines(),
        after["content_html"].splitlines(),
        lineterm="",
        n=2,
    ))
    added   = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    changed_fields = [k for k in ("title", "seo_title", "seo_description") if before[k] != after[k]]
    if before["tags"] != after["tags"]:
        changed_fields.append("tags")
    content_changed = bool(diff_lines)

    if changed_fields or content_changed:
        parts = list(changed_fields)
        if content_changed:
            parts.append(f"content +{added}/-{removed}")
        history = ArticleEditHistory(
            post_id=post.id,
            user_id=user.id,
            shop_domain=post.shop_domain,
            title_before=          before["title"]           if "title" in changed_fields else None,
            title_after=           after["title"]            if "title" in changed_fields else None,
            seo_title_before=      before["seo_title"]       if "seo_title" in changed_fields else None,
            seo_title_after=       after["seo_title"]        if "seo_title" in changed_fields else None,
            seo_description_before=before["seo_description"] if "seo_description" in changed_fields else None,
            seo_description_after= after["seo_description"]  if "seo_description" in changed_fields else None,
            tags_before=           before["tags"]            if "tags" in changed_fields else None,
            tags_after=            after["tags"]             if "tags" in changed_fields else None,
            content_diff="\n".join(diff_lines) if content_changed else None,
            lines_added=added,
            lines_removed=removed,
            summary="; ".join(parts)[:500],
        )
        db.add(history)

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
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.article_feedback import ArticleFeedback
    from app.models.blog_post import BlogPost

    if not 1 <= body.rating <= 5:
        raise HTTPException(422, "Rating must be 1–5")
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "read", db)

    fb = ArticleFeedback(
        post_id=post_id,
        user_id=user.id,
        shop_domain=body.shop_domain or post.shop_domain,
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


@generate_router.get("/article/{post_id}/history")
def get_article_history(
    post_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Combined timeline of user edits + feedback for a post, newest first."""
    from app.models.article_edit_history import ArticleEditHistory
    from app.models.article_feedback import ArticleFeedback
    from app.models.blog_post import BlogPost
    from app.models.user import User

    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    if post.shop_domain:
        check_store_scope(user, post.shop_domain, "read", db)

    edits = (
        db.query(ArticleEditHistory)
        .filter_by(post_id=post_id)
        .order_by(ArticleEditHistory.created_at.desc())
        .all()
    )
    feedbacks = (
        db.query(ArticleFeedback)
        .filter_by(post_id=post_id)
        .order_by(ArticleFeedback.created_at.desc())
        .all()
    )

    user_ids = {e.user_id for e in edits if e.user_id} | {f.user_id for f in feedbacks if f.user_id}
    names = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            names[u.id] = u.name or u.email

    timeline = []
    for e in edits:
        timeline.append({
            "type": "edit",
            "id": e.id,
            "created_at": e.created_at,
            "user": names.get(e.user_id) if e.user_id else None,
            "summary": e.summary,
            "lines_added": e.lines_added,
            "lines_removed": e.lines_removed,
            "title_before": e.title_before,
            "title_after": e.title_after,
            "seo_title_before": e.seo_title_before,
            "seo_title_after": e.seo_title_after,
            "seo_description_before": e.seo_description_before,
            "seo_description_after": e.seo_description_after,
            "tags_before": e.tags_before,
            "tags_after": e.tags_after,
            "content_diff": e.content_diff,
        })
    for f in feedbacks:
        timeline.append({
            "type": "feedback",
            "id": f.id,
            "created_at": f.created_at,
            "user": names.get(f.user_id) if f.user_id else None,
            "rating": f.rating,
            "feedback_text": f.feedback_text,
            "improvement_notes": f.improvement_notes,
        })

    timeline.sort(key=lambda x: x["created_at"], reverse=True)
    return timeline
