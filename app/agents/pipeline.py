"""
AgentPipeline: Orchestrates the full SEO article generation pipeline across
all five agents — Research, Learning, Copywrite, Audit, and Planning.
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from app.services.content_writer import ContentWriter

logger = logging.getLogger(__name__)


class AgentPipeline:
    def __init__(self):
        from app.agents.research_agent import ResearchAgent
        from app.agents.copywrite_agent import CopywriteAgent
        from app.agents.audit_agent import AuditAgent
        from app.agents.learning_agent import LearningAgent
        from app.agents.planning_agent import PlanningAgent

        self.research = ResearchAgent()
        self.copywrite = CopywriteAgent()
        self.audit = AuditAgent()
        self.learning = LearningAgent()
        self.planning = PlanningAgent()

    async def run(
        self,
        title: str,
        keyword: str,
        shop_domain: str,
        db,
        brand_profile: Optional[dict] = None,
        outline: Optional[list] = None,
        language: str = "en",
        country: str = "us",
        target_platform: str = "google",
        word_count: int = 1500,
        tone: str = "professional",
        market: str = "us",
        article_type: Optional[str] = None,
        notes: Optional[str] = None,
        max_audit_iterations: int = 2,
    ) -> dict:
        """
        Run the full multi-agent SEO content pipeline.

        Steps:
        1. Research — gather keyword data and SERP insights
        2. Learning (pre) — synthesize lessons from past performance
        3. Copywrite — generate the article
        4. Audit loop — audit and optionally rewrite until ready
        5. Learning (post) — simulate user review and record run
        """
        from app.models.pipeline_run import PipelineRun
        from app.models.blog_post import Platform

        # ── 1. Create PipelineRun record ──────────────────────────────────────
        run = PipelineRun(
            shop_domain=shop_domain,
            keyword=keyword,
            title=title,
            status="running",
            steps=[],
        )
        try:
            db.add(run)
            db.commit()
            db.refresh(run)
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass
            raise HTTPException(500, f"Pipeline failed: {exc}")

        run_id = run.id
        steps = []

        def _update(agent_name: str, status: str, data: Optional[dict] = None):
            step = {"agent": agent_name, "status": status}
            if data:
                step.update(data)
            steps.append(step)
            try:
                run.steps = list(steps)
                db.commit()
            except Exception as exc:
                logger.warning("Pipeline._update commit failed: %s", exc)

        try:
            # ── Step: research ────────────────────────────────────────────────
            _update("research", "running")
            research = await self.research.run(keyword, shop_domain, db, language, country)
            ranked_kws = research.get("ranked_keywords", [])
            paa = research.get("people_also_ask", [])
            _update("research", "done", {
                "keyword_count": len(ranked_kws),
                "paa_count": len(paa),
                "top_keywords": [kw.get("keyword") for kw in ranked_kws[:5]],
            })

            # ── Step: learning_pre ────────────────────────────────────────────
            _update("learning_pre", "running")
            lessons = await self.learning.synthesize_lessons(shop_domain, db)
            _update("learning_pre", "done", {
                "lesson_count": len(lessons),
                "lessons": lessons[:3],
            })

            # ── Step: copywrite ───────────────────────────────────────────────
            _update("copywrite", "running")
            article = await self.copywrite.write(
                title=title,
                research=research,
                lessons=lessons,
                brand_profile=brand_profile or {},
                shop_domain=shop_domain,
                db=db,
                language=language,
                target_platform=target_platform,
                word_count=word_count,
                outline=outline,
                tone=tone,
                market=market,
                audit_feedback=None,
                notes=notes,
                article_type=article_type,
            )

            # Save draft to DB
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            post = ContentWriter().save_draft(
                db=db,
                title=title,
                slug=slug,
                focus_keyword=keyword,
                result=article,
                platform=Platform.SHOPIFY,
                shop_domain=shop_domain,
            )
            post_id = post.id

            # Attach article id so rewrite_with_feedback can update the record
            article["id"] = post_id
            article["title"] = title
            article["focus_keyword"] = keyword

            content_html = article.get("content_html", "")
            article_word_count = len(content_html.split())

            _update("copywrite", "done", {
                "post_id": post_id,
                "word_count": article_word_count,
            })

            # ── Step: audit loop ──────────────────────────────────────────────
            audit_result = {}
            seo_title = article.get("seo_title", title)

            for i in range(max_audit_iterations):
                _update(f"audit_{i + 1}", "running")
                current_html = article.get("content_html", "")
                current_seo_title = article.get("seo_title", title)

                audit_result = await self.audit.audit(
                    article_html=current_html,
                    seo_title=current_seo_title,
                    focus_keyword=keyword,
                    target_keywords=ranked_kws,
                    paa_questions=paa,
                )

                _update(f"audit_{i + 1}", "done", {
                    "iteration": i + 1,
                    "score": audit_result.get("score"),
                    "grade": audit_result.get("grade"),
                    "ready": audit_result.get("ready"),
                    "issues_count": len(audit_result.get("issues", [])),
                })

                # Persist audit findings to KB so Learning Agent accumulates them
                self.learning.record_audit(
                    {**audit_result, "title": title, "focus_keyword": keyword},
                    shop_domain, db,
                )

                if audit_result.get("ready") or i == max_audit_iterations - 1:
                    break

                # Rewrite with audit feedback
                _update(f"copywrite_revision_{i + 1}", "running")
                article = await self.copywrite.rewrite_with_feedback(
                    article=article,
                    audit_feedback=audit_result,
                    lessons=lessons,
                    db=db,
                    brand_profile=brand_profile,
                )
                _update(f"copywrite_revision_{i + 1}", "done", {
                    "revision": i + 1,
                })

            seo_title = article.get("seo_title", title)

            # ── Step: learning_post ───────────────────────────────────────────
            _update("learning_post", "running")
            final_html = article.get("content_html", "")
            simulate = await self.learning.simulate_user_review(
                article_html=final_html,
                focus_keyword=keyword,
                user_style={},
                shop_domain=shop_domain,
                db=db,
            )
            await self.learning.record_pipeline_run(
                run_id=run_id,
                article_id=post_id,
                audit_score=audit_result.get("score"),
                lessons_applied=lessons,
                db=db,
            )
            _update("learning_post", "done", {
                "simulated_rating": simulate.get("simulated_rating"),
                "would_publish": simulate.get("would_publish"),
                "strengths": simulate.get("strengths", [])[:2],
            })

            # ── Mark run as done ──────────────────────────────────────────────
            run.status = "done"
            run.post_id = post_id
            run.completed_at = datetime.utcnow()
            run.steps = list(steps)
            try:
                db.commit()
            except Exception as exc:
                logger.warning("Pipeline: final commit failed: %s", exc)

            return {
                "run_id": run_id,
                "post_id": post_id,
                "status": "done",
                "steps": steps,
                "article": {
                    "id": post_id,
                    "title": title,
                    "seo_title": seo_title,
                    "tags": article.get("tags", []),
                    "image_url": article.get("image_url"),
                    "content_preview": final_html[:300],
                },
                "audit": audit_result,
                "simulated_feedback": simulate,
                "lessons_applied": len(lessons),
            }

        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("AgentPipeline.run failed: %s", exc)
            try:
                run.status = "failed"
                run.error = str(exc)
                run.steps = list(steps)
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            raise HTTPException(500, f"Pipeline failed: {exc}")


pipeline = AgentPipeline()
