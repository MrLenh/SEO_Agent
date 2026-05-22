"""
Topic cluster planner powered by OpenAI.
Turns keyword research data into a pillar + supporting article plan.
"""
import json
import re
from datetime import datetime

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.models.keyword import TopicCluster, TopicClusterStatus
from app.services.keyword_analyzer import KeywordAnalyzer


class TopicPlanner:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.analyzer = KeywordAnalyzer()

    def _build_prompt(
        self, keyword: str, paa: list[str], related: list[str], existing_topics: list[str] = None
    ) -> str:
        existing_ctx = ""
        if existing_topics:
            existing_ctx = "\n\nExisting articles already published (DO NOT duplicate these topics — plan complementary content instead):\n"
            existing_ctx += "\n".join(f"- {t}" for t in existing_topics)

        return f"""You are an SEO strategist. Create a comprehensive topic cluster for the seed keyword.

Seed keyword: {keyword}

People Also Ask:
{chr(10).join(f'- {q}' for q in paa) or '(none)'}

Related searches:
{chr(10).join(f'- {r}' for r in related) or '(none)'}{existing_ctx}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "cluster_name": "short descriptive name",
  "pillar": {{
    "title": "complete pillar article title",
    "slug": "url-friendly-slug",
    "focus_keyword": "{keyword}",
    "meta_description": "155 char meta description",
    "outline": ["H2: Section 1", "H2: Section 2", "H2: Section 3", "H2: FAQ"]
  }},
  "supporting_articles": [
    {{
      "title": "supporting article title",
      "slug": "url-friendly-slug",
      "focus_keyword": "long-tail keyword",
      "target_question": "the PAA question this targets",
      "meta_description": "155 char meta description",
      "outline": ["H2: Section 1", "H2: Section 2", "H2: FAQ"]
    }}
  ]
}}

Generate 5-7 supporting articles targeting the PAA questions and related searches."""

    def _parse_cluster(self, text: str) -> dict:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in response")
        return json.loads(match.group())

    async def plan(
        self,
        seed_keyword: str,
        country: str = "us",
        language: str = "en",
        db: Session = None,
    ) -> dict:
        """Research keyword → plan topic cluster → save to DB."""
        # 1. Keyword research
        research = await self.analyzer.research(seed_keyword, country, language)

        # 2. Load existing KB topics to avoid duplication
        existing_topics = []
        if db:
            try:
                from app.services.knowledge_base import KnowledgeBase
                existing_topics = KnowledgeBase().get_existing_topics(None, db)
            except Exception:
                pass

        # 3. OpenAI generates cluster plan (JSON mode for reliable parsing)
        message = self.client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_tokens=2500,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": self._build_prompt(
                        seed_keyword,
                        research["people_also_ask"],
                        research["related_searches"],
                        existing_topics=existing_topics,
                    ),
                }
            ],
        )

        cluster_data = self._parse_cluster(message.choices[0].message.content)
        cluster_data["seed_keyword"] = seed_keyword
        cluster_data["research"] = research

        # 3. Save to DB
        if db:
            cluster = TopicCluster(
                seed_keyword=seed_keyword,
                cluster_name=cluster_data.get("cluster_name", seed_keyword),
                description=json.dumps(cluster_data.get("pillar", {})),
                questions=research["people_also_ask"],
                status=TopicClusterStatus.PLANNED,
            )
            db.add(cluster)
            db.commit()
            db.refresh(cluster)
            cluster_data["id"] = cluster.id

        return cluster_data
