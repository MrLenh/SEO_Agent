"""
LearningAgent: Synthesizes SEO lessons from feedback, rankings, and audit data.
Simulates user review of generated articles and records pipeline outcomes.
"""
import json
import logging
from datetime import date, datetime
from typing import Optional

from openai import OpenAI

from app.config import settings
from app.services.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class LearningAgent:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ── Keyword context ───────────────────────────────────────────────────────

    def get_keyword_context(self, keyword: str, shop_domain: str, db) -> str:
        """Return knowledge-base context relevant to the keyword."""
        try:
            return KnowledgeBase().get_context_for_article(keyword, keyword, shop_domain, db)
        except Exception as exc:
            logger.warning("LearningAgent.get_keyword_context failed: %s", exc)
            return ""

    # ── Lesson synthesis ──────────────────────────────────────────────────────

    async def synthesize_lessons(self, shop_domain: str, db, limit: int = 10) -> list[str]:
        """Derive actionable writing lessons from performance data."""
        feedback_data = self._get_feedback_data(shop_domain, db)
        ranking_data = self._get_ranking_data(shop_domain, db)
        audit_data = self._get_audit_data(shop_domain, db)

        if not feedback_data and not ranking_data and not audit_data:
            return []

        lessons = await self._synthesize(feedback_data, ranking_data, audit_data)
        self._save_lessons(lessons, shop_domain, db)
        return lessons

    # ── Data collectors ───────────────────────────────────────────────────────

    def _get_feedback_data(self, shop_domain: str, db) -> str:
        """Return formatted feedback from ArticleFeedback records."""
        try:
            from app.models.article_feedback import ArticleFeedback
            records = (
                db.query(ArticleFeedback)
                .filter(
                    ArticleFeedback.shop_domain == shop_domain,
                    ArticleFeedback.improvement_notes.isnot(None),
                )
                .order_by(ArticleFeedback.created_at.desc())
                .limit(20)
                .all()
            )
            if not records:
                return ""
            lines = [
                f"Article rated {f.rating}/5: {f.improvement_notes.strip()}"
                for f in records
                if f.improvement_notes and f.improvement_notes.strip()
            ]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("LearningAgent._get_feedback_data failed: %s", exc)
            return ""

    def _get_ranking_data(self, shop_domain: str, db) -> str:
        """Return keyword ranking trends for tracked keywords."""
        try:
            from app.models.keyword_follow import KeywordFollow, KeywordHistory
            follows = (
                db.query(KeywordFollow)
                .filter(
                    KeywordFollow.is_active == True,
                    KeywordFollow.shop_domain == shop_domain,
                )
                .limit(20)
                .all()
            )
            if not follows:
                return ""

            lines = []
            for follow in follows:
                history = (
                    db.query(KeywordHistory)
                    .filter(KeywordHistory.follow_id == follow.id)
                    .order_by(KeywordHistory.date.desc())
                    .limit(2)
                    .all()
                )
                if len(history) >= 2 and history[0].position and history[1].position:
                    current_pos = history[0].position
                    prev_pos = history[1].position
                    change = prev_pos - current_pos
                    if change > 0:
                        trend = f"+{change:.1f}"
                    elif change < 0:
                        trend = f"{change:.1f}"
                    else:
                        trend = "no change"
                    lines.append(
                        f"{follow.keyword}: pos {current_pos:.1f} ({trend} vs prev)"
                    )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("LearningAgent._get_ranking_data failed: %s", exc)
            return ""

    def _get_audit_data(self, shop_domain: str, db) -> str:
        """Return brief summary of recent generated blog posts."""
        try:
            from app.models.blog_post import BlogPost
            from bs4 import BeautifulSoup
            posts = (
                db.query(BlogPost)
                .filter(
                    BlogPost.shop_domain == shop_domain,
                    BlogPost.source == "generated",
                )
                .order_by(BlogPost.created_at.desc())
                .limit(10)
                .all()
            )
            if not posts:
                return ""
            lines = []
            for post in posts:
                word_count = 0
                if post.content_html:
                    try:
                        text = BeautifulSoup(post.content_html, "lxml").get_text(" ", strip=True)
                        word_count = len(text.split())
                    except Exception:
                        word_count = len(post.content_html.split())
                lines.append(f"'{post.title}' ({word_count} words)")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("LearningAgent._get_audit_data failed: %s", exc)
            return ""

    # ── AI synthesis ──────────────────────────────────────────────────────────

    async def _synthesize(self, feedback: str, rankings: str, audit_data: str) -> list[str]:
        """Use AI to extract actionable writing lessons from performance data."""
        model = (
            getattr(settings, "OPENAI_MODEL_SMART", "") or
            settings.OPENAI_MODEL or
            "gpt-4o"
        )

        sections = []
        if feedback:
            sections.append(f"User Feedback:\n{feedback}")
        if rankings:
            sections.append(f"Keyword Rankings:\n{rankings}")
        if audit_data:
            sections.append(f"Recent Articles:\n{audit_data}")

        combined = "\n\n".join(sections)

        system = (
            "You are an elite SEO expert. Extract 5-8 actionable writing lessons "
            "from this performance data."
        )
        user = (
            f"Analyze this SEO performance data and extract lessons:\n\n{combined}\n\n"
            "Return a JSON object with key 'lessons' containing a list of 5-8 strings. "
            "Each lesson must start with either '✅ Do:' or '⚠️ Avoid:' followed by "
            "a concise, actionable instruction."
        )

        try:
            response = self.client.chat.completions.create(
                model=model,
                max_tokens=800,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
            lessons = data.get("lessons", [])
            return [str(l) for l in lessons if isinstance(l, str) and l.strip()]
        except Exception as exc:
            logger.warning("LearningAgent._synthesize failed: %s", exc)
            return []

    # ── Lesson persistence ────────────────────────────────────────────────────

    def _save_lessons(self, lessons: list[str], shop_domain: str, db) -> None:
        """Persist synthesized lessons to the knowledge base."""
        if not lessons:
            return
        try:
            today = date.today().isoformat()
            title = f"AI Lessons — {today}"
            content_text = "\n".join(lessons)
            content_md = "\n".join(f"- {l}" for l in lessons)
            KnowledgeBase().add_from_text(
                title=title,
                content_text=content_text,
                content_md=content_md,
                source_type="lesson",
                shop_domain=shop_domain,
                db=db,
            )
        except Exception as exc:
            logger.warning("LearningAgent._save_lessons failed: %s", exc)

    # ── Simulated user review ─────────────────────────────────────────────────

    async def simulate_user_review(
        self,
        article_html: str,
        focus_keyword: str,
        user_style: dict,
        shop_domain: str,
        db,
    ) -> dict:
        """Simulate an expert user reviewing the article based on past feedback patterns."""
        model = (
            getattr(settings, "OPENAI_MODEL_SMART", "") or
            settings.OPENAI_MODEL or
            "gpt-4o"
        )

        feedback_patterns = self._get_feedback_data(shop_domain, db)
        article_preview = article_html[:2000]

        patterns_section = (
            f"\nPast feedback patterns from this store:\n{feedback_patterns}"
            if feedback_patterns
            else ""
        )

        system = (
            "You are simulating an expert content reviewer evaluating a newly generated "
            "SEO blog article. Base your evaluation on the article quality and any past "
            "feedback patterns provided."
        )
        user = (
            f"Review this article excerpt for focus keyword '{focus_keyword}'.\n\n"
            f"Article preview:\n{article_preview}{patterns_section}\n\n"
            "Return a JSON object with exactly these keys:\n"
            "- simulated_rating: integer 1-5\n"
            "- simulated_feedback: string with overall assessment\n"
            "- strengths: list of strength strings\n"
            "- weaknesses: list of weakness strings\n"
            "- would_publish: boolean"
        )

        try:
            response = self.client.chat.completions.create(
                model=model,
                max_tokens=600,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            result = json.loads(raw)
            return {
                "simulated_rating": int(result.get("simulated_rating", 3)),
                "simulated_feedback": str(result.get("simulated_feedback", "")),
                "strengths": list(result.get("strengths", [])),
                "weaknesses": list(result.get("weaknesses", [])),
                "would_publish": bool(result.get("would_publish", False)),
            }
        except Exception as exc:
            logger.warning("LearningAgent.simulate_user_review failed: %s", exc)
            return {
                "simulated_rating": 3,
                "simulated_feedback": "",
                "strengths": [],
                "weaknesses": [],
                "would_publish": False,
            }

    # ── Pipeline run recording ────────────────────────────────────────────────

    async def record_pipeline_run(
        self,
        run_id: int,
        article_id: Optional[int],
        audit_score: Optional[int],
        lessons_applied: list,
        db,
    ) -> None:
        """Update a PipelineRun record with the final article and completion time."""
        try:
            from app.models.pipeline_run import PipelineRun
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if run:
                if article_id is not None:
                    run.post_id = article_id
                run.completed_at = datetime.utcnow()
                db.commit()
        except Exception as exc:
            logger.warning("LearningAgent.record_pipeline_run failed: %s", exc)
