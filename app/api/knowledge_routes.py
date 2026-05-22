import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.crawl_job import CrawlJob, CrawlStatus
from app.models.knowledge_item import KnowledgeItem, KnowledgeStatus
from app.services.knowledge_base import KnowledgeBase

knowledge_router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class CrawlRequest(BaseModel):
    url: str
    shop_domain: Optional[str] = None
    crawl_index: bool = False


class AddTextRequest(BaseModel):
    title: str
    content: str
    source_type: str = "manual"
    source_url: Optional[str] = None
    shop_domain: Optional[str] = None


class StatusUpdate(BaseModel):
    status: str


class TrendRequest(BaseModel):
    seed_keyword: str
    shop_domain: Optional[str] = None
    country: str = "us"
    language: str = "en"


class SearchRequest(BaseModel):
    query: str
    shop_domain: Optional[str] = None
    top_k: int = 10


class AnalyzeRankingsRequest(BaseModel):
    rankings: list[dict]
    shop_domain: Optional[str] = None


# ── Crawl ─────────────────────────────────────────────────────────────────────

@knowledge_router.post("/crawl")
async def crawl_url(
    body: CrawlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job = CrawlJob(
        shop_domain=body.shop_domain,
        url=body.url,
        job_type="sitemap" if body.crawl_index else "single",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(
        _run_crawl, job.id, body.url, body.shop_domain, body.crawl_index
    )
    return {"job_id": job.id, "status": job.status, "message": "Crawl started"}


def _run_crawl(
    job_id: int, url: str, shop_domain: Optional[str], crawl_index: bool
):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter_by(id=job_id).first()
        job.status = CrawlStatus.RUNNING
        db.commit()

        kb = KnowledgeBase()
        urls = kb.crawler.crawl_blog_index(url) if crawl_index else [url]

        count = 0
        for u in urls:
            try:
                kb.add_from_url(u, shop_domain, db)
                count += 1
            except Exception:
                pass

        job.status = CrawlStatus.DONE
        job.items_found = count
        job.completed_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        job = db.query(CrawlJob).filter_by(id=job_id).first()
        if job:
            job.status = CrawlStatus.FAILED
            job.error = str(e)
            db.commit()
    finally:
        db.close()


@knowledge_router.get("/jobs")
def list_crawl_jobs(
    shop_domain: Optional[str] = None, db: Session = Depends(get_db)
):
    q = db.query(CrawlJob)
    if shop_domain:
        q = q.filter_by(shop_domain=shop_domain)
    return q.order_by(CrawlJob.created_at.desc()).limit(20).all()


# ── Items CRUD ────────────────────────────────────────────────────────────────

@knowledge_router.post("/add")
def add_text(body: AddTextRequest, db: Session = Depends(get_db)):
    import html2text as _h2t

    h = _h2t.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    content_md = (
        h.handle(body.content) if body.content.lstrip().startswith("<") else body.content
    )
    kb = KnowledgeBase()
    item = kb.add_from_text(
        title=body.title,
        content_text=body.content,
        content_md=content_md,
        source_type=body.source_type,
        shop_domain=body.shop_domain,
        db=db,
        source_url=body.source_url,
    )
    return {"id": item.id, "title": item.title, "status": item.status}


@knowledge_router.get("/items")
def list_items(
    shop_domain: Optional[str] = None,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(KnowledgeItem)
    if shop_domain:
        query = query.filter(KnowledgeItem.shop_domain == shop_domain)
    if status:
        query = query.filter(KnowledgeItem.status == status)
    if source_type:
        query = query.filter(KnowledgeItem.source_type == source_type)
    if q:
        query = query.filter(KnowledgeItem.title.ilike(f"%{q}%"))

    total = query.count()
    items = (
        query.order_by(KnowledgeItem.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "items": [
            {
                "id": i.id,
                "title": i.title,
                "source_url": i.source_url,
                "source_type": i.source_type,
                "status": i.status,
                "word_count": (i.extra_meta or {}).get("word_count", 0),
                "created_at": i.created_at,
            }
            for i in items
        ],
    }


@knowledge_router.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(KnowledgeItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Not found")
    return {
        "id": item.id,
        "title": item.title,
        "source_url": item.source_url,
        "source_type": item.source_type,
        "status": item.status,
        "content_md": item.content_md,
        "extra_meta": item.extra_meta,
        "created_at": item.created_at,
    }


@knowledge_router.put("/items/{item_id}/status")
def update_item_status(
    item_id: int, body: StatusUpdate, db: Session = Depends(get_db)
):
    valid = {KnowledgeStatus.PENDING, KnowledgeStatus.APPROVED, KnowledgeStatus.REJECTED}
    if body.status not in valid:
        raise HTTPException(422, "Invalid status")
    item = db.query(KnowledgeItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Not found")
    item.status = body.status
    if body.status == KnowledgeStatus.APPROVED:
        item.approved_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "status": item.status}


@knowledge_router.put("/items/approve-all")
def approve_all_pending(
    shop_domain: Optional[str] = None, db: Session = Depends(get_db)
):
    q = db.query(KnowledgeItem).filter(KnowledgeItem.status == KnowledgeStatus.PENDING)
    if shop_domain:
        q = q.filter(KnowledgeItem.shop_domain == shop_domain)
    count = q.count()
    q.update({"status": KnowledgeStatus.APPROVED, "approved_at": datetime.utcnow()})
    db.commit()
    return {"approved": count}


@knowledge_router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(KnowledgeItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(404, "Not found")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}


# ── Search ────────────────────────────────────────────────────────────────────

@knowledge_router.post("/search")
def search_kb(body: SearchRequest, db: Session = Depends(get_db)):
    if not body.query:
        raise HTTPException(422, "query required")
    kb = KnowledgeBase()
    results = kb.search(body.query, body.shop_domain, db, top_k=body.top_k)
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "source_url": r["source_url"],
            "source_type": r["source_type"],
            "score": round(r["_score"], 3),
        }
        for r in results
    ]


# ── Stats ─────────────────────────────────────────────────────────────────────

@knowledge_router.get("/stats")
def kb_stats(shop_domain: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(KnowledgeItem)
    if shop_domain:
        q = q.filter(KnowledgeItem.shop_domain == shop_domain)
    total = q.count()
    approved = q.filter(KnowledgeItem.status == KnowledgeStatus.APPROVED).count()
    pending = q.filter(KnowledgeItem.status == KnowledgeStatus.PENDING).count()
    types_q = db.query(KnowledgeItem.source_type, func.count(KnowledgeItem.id)).group_by(
        KnowledgeItem.source_type
    )
    if shop_domain:
        types_q = types_q.filter(KnowledgeItem.shop_domain == shop_domain)
    return {
        "total": total,
        "approved": approved,
        "pending": pending,
        "rejected": total - approved - pending,
        "by_type": {t: c for t, c in types_q.all()},
    }


# ── Trend research ────────────────────────────────────────────────────────────

@knowledge_router.post("/trend")
async def research_trend(body: TrendRequest, db: Session = Depends(get_db)):
    from app.config import settings
    from app.services.keyword_analyzer import KeywordAnalyzer
    from openai import OpenAI

    analyzer = KeywordAnalyzer()
    research = await analyzer.research(body.seed_keyword, body.country, body.language)

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    prompt = f"""You are an SEO market researcher. Analyze keyword research data and extract actionable trend insights.

Keyword: {body.seed_keyword}
People Also Ask: {json.dumps(research.get('people_also_ask', [])[:10])}
Related searches: {json.dumps(research.get('related_searches', [])[:10])}
Top result titles: {json.dumps([r.get('title','') for r in research.get('top_results', [])[:5]])}

Return JSON only:
{{
  "trend_summary": "2-3 sentences on market trend",
  "new_keywords": ["kw1","kw2","kw3","kw4","kw5"],
  "content_gaps": ["topic not well covered 1","topic not well covered 2"],
  "seasonal_notes": "seasonal trend notes or empty string",
  "recommended_titles": ["article title idea 1","article title idea 2","article title idea 3"]
}}"""

    resp = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        max_tokens=800,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    insights = json.loads(resp.choices[0].message.content)

    content_md = f"""# Market Trend: {body.seed_keyword}

## Summary
{insights.get('trend_summary', '')}

## New Keyword Opportunities
{chr(10).join(f'- {k}' for k in insights.get('new_keywords', []))}

## Content Gaps
{chr(10).join(f'- {g}' for g in insights.get('content_gaps', []))}

## Seasonal Notes
{insights.get('seasonal_notes', 'None')}

## Recommended Article Ideas
{chr(10).join(f'- {t}' for t in insights.get('recommended_titles', []))}
"""
    kb = KnowledgeBase()
    item = kb.add_from_text(
        title=f"Trend Research: {body.seed_keyword}",
        content_text=content_md,
        content_md=content_md,
        source_type="trend",
        shop_domain=body.shop_domain,
        db=db,
        auto_approve=True,
    )
    return {"item_id": item.id, "insights": insights}


# ── Ranking analysis ──────────────────────────────────────────────────────────

@knowledge_router.post("/analyze-rankings")
async def analyze_rankings(
    body: AnalyzeRankingsRequest, db: Session = Depends(get_db)
):
    from app.config import settings
    from app.models.blog_post import BlogPost
    from openai import OpenAI

    if not body.rankings:
        raise HTTPException(422, "rankings required")

    posts = db.query(BlogPost).filter(BlogPost.focus_keyword.isnot(None)).limit(50).all()
    posts_map = {p.focus_keyword.lower(): p for p in posts if p.focus_keyword}

    enriched = []
    for r in body.rankings[:20]:
        kw = (r.get("keyword") or "").lower()
        post = posts_map.get(kw)
        enriched.append({
            "keyword": r.get("keyword"),
            "position": r.get("position"),
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "post_title": post.title if post else None,
            "has_faq": "faq" in (post.content_html or "").lower() if post else None,
            "approx_word_count": len((post.content_html or "").split()) if post else None,
        })

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    prompt = f"""You are an SEO analyst. Analyze keyword ranking data to extract content performance patterns.

Data:
{json.dumps(enriched, indent=2)}

Return JSON only:
{{
  "performance_summary": "2-3 sentence summary of what's working",
  "writing_lessons": ["lesson 1 about structure/length/format","lesson 2","lesson 3"],
  "keyword_groups": [
    {{"group": "group name","keywords": ["kw1","kw2"],"avg_position": 5.2,"strategy": "what works"}}
  ],
  "improvement_priorities": ["action 1","action 2","action 3"]
}}"""

    resp = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        max_tokens=1000,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    analysis = json.loads(resp.choices[0].message.content)

    content_md = f"""# Ranking Performance Analysis

## Summary
{analysis.get('performance_summary', '')}

## Writing Lessons
{chr(10).join(f'- {l}' for l in analysis.get('writing_lessons', []))}

## Keyword Groups
{chr(10).join(f"- **{g['group']}**: {g.get('strategy','')}" for g in analysis.get('keyword_groups', []))}

## Improvement Priorities
{chr(10).join(f'- {p}' for p in analysis.get('improvement_priorities', []))}
"""
    kb = KnowledgeBase()
    item = kb.add_from_text(
        title="Ranking Performance Analysis",
        content_text=content_md,
        content_md=content_md,
        source_type="analysis",
        shop_domain=body.shop_domain,
        db=db,
        auto_approve=True,
    )
    return {"item_id": item.id, "analysis": analysis}
