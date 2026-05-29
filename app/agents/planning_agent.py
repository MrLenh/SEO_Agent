"""
PlanningAgent: Analyzes SEO coverage, ranking trends, and GSC data to generate
strategic content recommendations and priority keyword lists.
"""
import json
import logging
from datetime import date
from typing import Optional

from app.agents.base import get_client, get_model

logger = logging.getLogger(__name__)


class PlanningAgent:
    def __init__(self):
        self.client = get_client()

    # ── Main analysis ─────────────────────────────────────────────────────────

    async def analyze(self, shop_domain: str, db, site_url: str = None) -> dict:
        """Run a full SEO analysis and return strategic recommendations."""
        tracking = self._get_tracking_summary(shop_domain, db)
        gsc_data = await self._get_gsc_data(shop_domain, db, site_url)
        coverage = self._analyze_coverage(shop_domain, db)
        strategy = await self._generate_strategy(tracking, gsc_data, coverage, shop_domain)

        return {
            "tracking_summary": tracking,
            "gsc_summary": gsc_data,
            "coverage": coverage,
            "strategy": strategy,
            "priority_keywords": strategy.get("priority_keywords", []),
        }

    # ── Tracking summary ──────────────────────────────────────────────────────

    def _get_tracking_summary(self, shop_domain: str, db) -> dict:
        """Summarize keyword tracking performance."""
        try:
            from app.models.keyword_follow import KeywordFollow, KeywordHistory

            follows = (
                db.query(KeywordFollow)
                .filter(
                    KeywordFollow.is_active == True,
                    KeywordFollow.shop_domain == shop_domain,
                )
                .limit(50)
                .all()
            )

            total_tracked = len(follows)
            top_3 = 0
            top_10 = 0
            improving = 0
            declining = 0
            keyword_details = []

            for follow in follows:
                history = (
                    db.query(KeywordHistory)
                    .filter(KeywordHistory.follow_id == follow.id)
                    .order_by(KeywordHistory.date.desc())
                    .limit(2)
                    .all()
                )

                current_pos = history[0].position if history else None
                prev_pos = history[1].position if len(history) >= 2 else None

                if current_pos is not None:
                    if current_pos <= 3:
                        top_3 += 1
                    if current_pos <= 10:
                        top_10 += 1
                    if prev_pos is not None:
                        if current_pos < prev_pos:
                            improving += 1
                        elif current_pos > prev_pos:
                            declining += 1

                keyword_details.append({
                    "keyword": follow.keyword,
                    "position": current_pos,
                    "prev_position": prev_pos,
                })

            # Sort by position (tracked first, then untracked)
            keyword_details.sort(
                key=lambda x: (x["position"] is None, x["position"] or 9999)
            )

            return {
                "total_tracked": total_tracked,
                "top_3": top_3,
                "top_10": top_10,
                "improving": improving,
                "declining": declining,
                "keywords": keyword_details[:20],
            }
        except Exception as exc:
            logger.warning("PlanningAgent._get_tracking_summary failed: %s", exc)
            return {
                "total_tracked": 0,
                "top_3": 0,
                "top_10": 0,
                "improving": 0,
                "declining": 0,
                "keywords": [],
            }

    # ── GSC data ──────────────────────────────────────────────────────────────

    async def _get_gsc_data(self, shop_domain: str, db, site_url: str = None) -> dict:
        """Fetch 28-day performance summary from Google Search Console."""
        try:
            from app.services.gsc_client import get_client_for_brand
            gsc = get_client_for_brand(shop_domain, db)
            if not gsc:
                return {}

            perf = gsc.get_overview(days=28)

            return {
                "total_clicks": perf.get("clicks", 0),
                "total_impressions": perf.get("impressions", 0),
                "avg_ctr": perf.get("ctr", 0.0),
                "avg_position": perf.get("position", 0.0),
            }
        except Exception as exc:
            logger.warning("PlanningAgent._get_gsc_data failed: %s", exc)
            return {}

    # ── Coverage analysis ─────────────────────────────────────────────────────

    def _analyze_coverage(self, shop_domain: str, db) -> dict:
        """Analyse content coverage vs planned topic clusters."""
        try:
            from app.models.blog_post import BlogPost, PostStatus
            from app.models.keyword import TopicCluster

            posts = (
                db.query(BlogPost)
                .filter(
                    BlogPost.shop_domain == shop_domain,
                    BlogPost.focus_keyword.isnot(None),
                )
                .all()
            )

            published_count = sum(
                1 for p in posts if p.status == PostStatus.PUBLISHED
            )
            draft_count = sum(
                1 for p in posts if p.status == PostStatus.DRAFT
            )
            covered_keywords = list({
                p.focus_keyword for p in posts if p.focus_keyword
            })

            clusters = db.query(TopicCluster).all()

            planned_not_written = [
                c.seed_keyword
                for c in clusters
                if c.seed_keyword and c.seed_keyword not in covered_keywords
            ]

            return {
                "published_count": published_count,
                "draft_count": draft_count,
                "covered_keywords": covered_keywords[:20],
                "planned_not_written": planned_not_written[:10],
            }
        except Exception as exc:
            logger.warning("PlanningAgent._analyze_coverage failed: %s", exc)
            return {
                "published_count": 0,
                "draft_count": 0,
                "covered_keywords": [],
                "planned_not_written": [],
            }

    # ── Strategy generation ───────────────────────────────────────────────────

    async def _generate_strategy(
        self,
        tracking: dict,
        gsc: dict,
        coverage: dict,
        shop_domain: str,
    ) -> dict:
        """Use AI to generate strategic SEO recommendations."""
        model = get_model("planning")

        has_tracking = tracking.get("total_tracked", 0) > 0
        has_gsc = bool(gsc)
        has_coverage = coverage.get("published_count", 0) > 0 or coverage.get("planned_not_written")

        if not has_tracking and not has_gsc and not has_coverage:
            return {
                "priority_keywords": [],
                "recommendations": [],
                "summary": "No data available yet",
            }

        tracking_text = json.dumps(tracking, indent=2)[:800]
        gsc_text = json.dumps(gsc, indent=2)[:800] if gsc else "(no GSC data)"
        coverage_text = json.dumps(coverage, indent=2)[:800]

        system = (
            "You are an expert SEO strategist. Analyze the provided SEO data and "
            "generate actionable strategic recommendations."
        )
        user = (
            f"Analyze the SEO data for shop: {shop_domain}\n\n"
            f"Keyword Tracking:\n{tracking_text}\n\n"
            f"Google Search Console (28 days):\n{gsc_text}\n\n"
            f"Content Coverage:\n{coverage_text}\n\n"
            "Return a JSON object with exactly these keys:\n"
            "- summary: string overview of the SEO situation\n"
            "- priority_keywords: list of objects, each with: keyword, reason, opportunity, action\n"
            "- recommendations: list of strategic recommendation strings\n"
            "- quick_wins: list of quick-win opportunity strings\n"
            "- coverage_score: integer 0-100 estimating content coverage completeness"
        )

        try:
            response = self.client.chat.completions.create(
                model=model,
                max_tokens=1200,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            result = json.loads(raw)
            return {
                "summary": str(result.get("summary", "")),
                "priority_keywords": list(result.get("priority_keywords", [])),
                "recommendations": list(result.get("recommendations", [])),
                "quick_wins": list(result.get("quick_wins", [])),
                "coverage_score": int(result.get("coverage_score", 0)),
            }
        except Exception as exc:
            logger.warning("PlanningAgent._generate_strategy failed: %s", exc)
            return {
                "priority_keywords": [],
                "recommendations": [],
                "quick_wins": [],
                "summary": "Strategy generation failed",
                "coverage_score": 0,
            }

    # ── Priority keywords shortcut ────────────────────────────────────────────

    async def get_priority_keywords(self, shop_domain: str, db) -> list[dict]:
        """Return just the priority keywords from a full analysis."""
        result = await self.analyze(shop_domain, db)
        return result.get("priority_keywords", [])
