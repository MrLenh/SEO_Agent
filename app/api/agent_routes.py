"""
Multi-Agent SEO Pipeline API.

POST /api/v1/pipeline/run          — run full 5-agent pipeline
GET  /api/v1/pipeline/runs         — list recent runs
GET  /api/v1/pipeline/runs/{id}    — get run status/details
POST /api/v1/pipeline/research     — Research Agent only
POST /api/v1/pipeline/plan         — Planning Agent only
GET  /api/v1/pipeline/lessons      — Learning Agent lessons
POST /api/v1/pipeline/learn        — trigger Learning Agent synthesis
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)
agent_router = APIRouter(prefix="/api/v1/pipeline", tags=["agents"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    title: str
    keyword: str
    shop_domain: Optional[str] = None
    language: str = "en"
    country: str = "us"
    outline: Optional[list] = None
    word_count: int = 1500
    tone: str = "professional"
    market: str = "us"
    target_platform: str = "google"
    article_type: Optional[str] = None
    notes: Optional[str] = None
    max_audit_iterations: int = 2


class ResearchRequest(BaseModel):
    keyword: str
    shop_domain: Optional[str] = None
    language: str = "en"
    country: str = "us"


class PlanRequest(BaseModel):
    shop_domain: str
    site_url: Optional[str] = None


class LearnRequest(BaseModel):
    shop_domain: str


# ── Routes ────────────────────────────────────────────────────────────────────

@agent_router.post("/run")
async def run_pipeline(body: PipelineRunRequest, db: Session = Depends(get_db)):
    """Run the full 5-agent SEO pipeline to generate an article."""
    from app.agents.pipeline import AgentPipeline

    # Load brand profile
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
                "writing_notes": getattr(bp, "writing_notes", None),
            }
    except Exception:
        pass

    pipeline = AgentPipeline()
    result = await pipeline.run(
        title=body.title,
        keyword=body.keyword,
        shop_domain=body.shop_domain,
        db=db,
        brand_profile=brand_profile,
        outline=body.outline,
        language=body.language,
        country=body.country,
        target_platform=body.target_platform,
        word_count=body.word_count,
        tone=body.tone,
        market=body.market,
        article_type=body.article_type,
        notes=body.notes,
        max_audit_iterations=body.max_audit_iterations,
    )
    return result


@agent_router.get("/runs")
def list_runs(
    shop_domain: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """List recent pipeline runs."""
    q = db.query(PipelineRun)
    if shop_domain:
        q = q.filter(PipelineRun.shop_domain == shop_domain)
    runs = q.order_by(PipelineRun.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "keyword": r.keyword,
            "title": r.title,
            "status": r.status,
            "post_id": r.post_id,
            "step_count": len(r.steps or []),
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]


@agent_router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    """Get full details of a pipeline run including all agent steps."""
    run = db.query(PipelineRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(404, "Pipeline run not found")
    return {
        "id": run.id,
        "keyword": run.keyword,
        "title": run.title,
        "status": run.status,
        "post_id": run.post_id,
        "steps": run.steps or [],
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


@agent_router.post("/research")
async def run_research(body: ResearchRequest, db: Session = Depends(get_db)):
    """Run just the Research Agent."""
    from app.agents.research_agent import ResearchAgent
    agent = ResearchAgent()
    result = await agent.run(body.keyword, body.shop_domain, db, body.language, body.country)
    return result


@agent_router.post("/plan")
async def run_planning(body: PlanRequest, db: Session = Depends(get_db)):
    """Run just the Planning Agent."""
    from app.agents.planning_agent import PlanningAgent
    agent = PlanningAgent()
    result = await agent.analyze(body.shop_domain, db, body.site_url)
    return result


@agent_router.get("/lessons")
async def get_lessons(
    shop_domain: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Get synthesized lessons from the Learning Agent knowledge base."""
    from app.models.knowledge_item import KnowledgeItem
    items = (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.source_type == "lesson",
            KnowledgeItem.shop_domain == shop_domain,
        )
        .order_by(KnowledgeItem.created_at.desc())
        .limit(10)
        .all()
    )
    lessons = []
    for item in items:
        text = item.content_text or ""
        lines = [l.strip("- ").strip() for l in text.splitlines() if l.strip().startswith("-")]
        lessons.extend(lines)
    return {"lessons": lessons, "source_count": len(items)}


@agent_router.post("/learn")
async def trigger_learning(body: LearnRequest, db: Session = Depends(get_db)):
    """Trigger the Learning Agent to synthesize lessons from all data sources."""
    from app.agents.learning_agent import LearningAgent
    agent = LearningAgent()
    lessons = await agent.synthesize_lessons(body.shop_domain, db)
    return {"lessons": lessons, "count": len(lessons)}
