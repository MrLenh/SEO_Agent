"""
ResearchAgent: Orchestrates keyword research, volume data, knowledge base context,
and AI-powered keyword ranking/outline generation.
"""
import json
import logging
import time
from typing import Optional

from app.agents.base import get_client, get_model
from app.services.keyword_analyzer import KeywordAnalyzer
from app.services.volume_service import get_search_volumes, is_configured as dataforseo_configured
from app.api.trends_routes import LOCATION_CODES

logger = logging.getLogger(__name__)


class ResearchAgent:
    def __init__(self):
        self.client = get_client()

    async def run(
        self,
        keyword: str,
        shop_domain: str,
        db,
        language: str = "en",
        country: str = "us",
    ) -> dict:
        start_ms = time.time()

        # SERP research
        serp_data = await KeywordAnalyzer().research(keyword, country, language)
        related_searches = serp_data.get("related_searches", [])
        people_also_ask = serp_data.get("people_also_ask", [])
        top_results = serp_data.get("top_results", [])

        # Build candidate keyword list
        candidates = [keyword] + related_searches[:19]

        # Search volumes (only if DataForSEO is configured)
        volumes: dict = {}
        if dataforseo_configured():
            location_code = LOCATION_CODES.get(country.lower(), 2840)
            try:
                volumes = await get_search_volumes(candidates, language, location_code)
            except Exception as exc:
                logger.warning("Volume fetch failed: %s", exc)

        # Knowledge base context — lazy import to avoid circular dependency
        kb_context = ""
        try:
            from app.agents.learning_agent import LearningAgent
            kb_context = await LearningAgent().get_keyword_context(keyword, shop_domain, db)
        except Exception as exc:
            logger.warning("LearningAgent kb_context failed: %s", exc)

        # AI ranking and analysis
        analysis = await self._rank_and_analyze(
            keyword, candidates, volumes, serp_data, kb_context
        )

        duration_ms = int((time.time() - start_ms) * 1000)

        return {
            "primary_keyword": keyword,
            "language": language,
            "country": country,
            "people_also_ask": people_also_ask,
            "top_results": top_results,
            "ranked_keywords": analysis.get("keywords", []),
            "suggested_outline": analysis.get("suggested_outline", []),
            "anchor_text_map": analysis.get("anchor_text_map", {}),
            "kb_context": kb_context,
            "duration_ms": duration_ms,
        }

    async def _rank_and_analyze(
        self,
        primary_kw: str,
        candidates: list[str],
        volumes: dict,
        serp_data: dict,
        kb_context: str,
    ) -> dict:
        model = get_model("research")

        # Build keyword volume table
        kw_lines = []
        for kw in candidates:
            vol_data = volumes.get(kw, {})
            vol = vol_data.get("volume", "N/A") if vol_data else "N/A"
            comp = vol_data.get("competition", "N/A") if vol_data else "N/A"
            kw_lines.append(f"  - {kw} | volume: {vol} | competition: {comp}")
        kw_table = "\n".join(kw_lines) if kw_lines else "  (no volume data)"

        paa = serp_data.get("people_also_ask", [])
        paa_text = "\n".join(f"  - {q}" for q in paa[:8]) if paa else "  (none)"

        kb_section = f"\nKnowledge base context:\n{kb_context}" if kb_context else ""

        system = (
            "You are an SEO strategist. Analyze the provided keywords and return a JSON object "
            "with ranked keywords, anchor text suggestions, and a suggested article outline."
        )

        user = f"""Analyze these keywords for a blog article about: "{primary_kw}"

Keywords with search data:
{kw_table}

People Also Ask:
{paa_text}
{kb_section}

Return a JSON object with exactly these keys:
{{
  "keywords": [
    {{
      "keyword": "string",
      "volume": number or null,
      "relevance_score": number 1-10,
      "difficulty": "low" | "medium" | "high",
      "usage_location": "title" | "h2" | "body" | "faq" | "meta",
      "anchor_text": "suggested anchor text phrase"
    }}
  ],
  "anchor_text_map": {{
    "keyword": "anchor text phrase"
  }},
  "suggested_outline": ["H2 section title 1", "H2 section title 2", "...up to 8 H2s"]
}}

Rules:
- Include all candidate keywords, ranked by relevance_score descending
- suggested_outline should be 5-8 H2 headings that naturally cover the topic
- anchor_text should be a short natural phrase suitable for hyperlinking
- difficulty: low (<30 competition), medium (30-70), high (>70)"""

        response = self.client.chat.completions.create(
            model=model,
            max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        raw = response.choices[0].message.content or "{}"
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("ResearchAgent._rank_and_analyze: failed to parse JSON response")
            result = {}

        # Normalise — ensure expected keys exist
        if "keywords" not in result:
            result["keywords"] = []
        if "anchor_text_map" not in result:
            result["anchor_text_map"] = {}
        if "suggested_outline" not in result:
            result["suggested_outline"] = []

        return result
