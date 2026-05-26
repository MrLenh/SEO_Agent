"""
AuditAgent: Programmatic + AI-powered SEO audit for generated articles.
Combines rule-based checks with an LLM quality review to produce a
composite score, grade, actionable issues, and rewrite instructions.
"""
import json
import logging
import re
from typing import Union

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class AuditAgent:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ── Public entry point ────────────────────────────────────────────────────

    async def audit(
        self,
        article_html: str,
        seo_title: str,
        focus_keyword: str,
        target_keywords: list,
        paa_questions: list,
    ) -> dict:
        prog = self._programmatic_checks(article_html, seo_title, focus_keyword, paa_questions)
        ai = await self._ai_audit(article_html, seo_title, focus_keyword, target_keywords, prog)

        prog_score = prog.get("score", 0)
        ai_score = ai.get("quality_score", 0)
        total = prog_score * 0.4 + ai_score * 0.6
        score = int(round(total))

        combined_issues = prog.get("issues", []) + ai.get("issues", [])
        combined_warnings = prog.get("warnings", []) + ai.get("warnings", [])

        ready = score >= 70 and not prog.get("critical_issues", False)

        return {
            "score": score,
            "grade": self._grade(score),
            "ready": ready,
            "programmatic": prog,
            "ai_feedback": ai,
            "issues": combined_issues,
            "warnings": combined_warnings,
            "feedback_for_rewrite": ai.get("rewrite_instructions", ""),
            "strengths": ai.get("strengths", []),
        }

    # ── Programmatic checks ───────────────────────────────────────────────────

    def _programmatic_checks(
        self,
        html: str,
        seo_title: str,
        focus_kw: str,
        paa: list,
    ) -> dict:
        issues = []
        warnings = []

        # Strip tags for plain-text checks
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        words = [w for w in text.split() if w]
        word_count = len(words)

        # Word count
        if word_count < 800:
            issues.append(f"Content too short ({word_count} words) — minimum 800, aim for 1500+")
        elif word_count < 1200:
            warnings.append(f"Content is {word_count} words — aim for 1200+ for stronger rankings")

        # Keyword in first 100 words
        first_100 = " ".join(words[:100]).lower()
        kw_lower = focus_kw.lower() if focus_kw else ""
        if kw_lower and kw_lower not in first_100:
            issues.append(f"Focus keyword '{focus_kw}' not found in the first 100 words")

        # H2 count
        h2_count = len(re.findall(r"<h2[\s>]", html, re.IGNORECASE))
        if h2_count < 2:
            issues.append(f"Only {h2_count} H2 heading(s) — use at least 2 H2 sections")

        # FAQ section
        has_faq = bool(re.search(r'class=["\']faq["\']|<h[23][^>]*>\s*faq|frequently asked', html, re.IGNORECASE))

        # Keyword in SEO title
        if kw_lower and seo_title and kw_lower not in seo_title.lower():
            issues.append(f"Focus keyword '{focus_kw}' not found in SEO title")

        # Scoring (out of 100)
        score = 0
        if word_count >= 1500:
            score += 30
        elif word_count >= 1200:
            score += 22
        elif word_count >= 800:
            score += 12

        if kw_lower and kw_lower in first_100:
            score += 20

        if h2_count >= 3:
            score += 20
        elif h2_count >= 2:
            score += 14

        if has_faq:
            score += 15

        if kw_lower and seo_title and kw_lower in seo_title.lower():
            score += 15

        critical_issues = word_count < 800 or h2_count < 2

        return {
            "score": score,
            "word_count": word_count,
            "h2_count": h2_count,
            "has_faq": has_faq,
            "issues": issues,
            "warnings": warnings,
            "critical_issues": critical_issues,
        }

    # ── AI audit ──────────────────────────────────────────────────────────────

    async def _ai_audit(
        self,
        html: str,
        seo_title: str,
        focus_kw: str,
        target_keywords: list,
        prog: dict,
    ) -> dict:
        model = getattr(settings, "OPENAI_MODEL", "") or "gpt-4o"

        article_preview = html[:3000]

        # Normalise target_keywords — accept list[str] or list[dict]
        kw_display = []
        for item in (target_keywords or []):
            if isinstance(item, dict):
                kw_display.append(item.get("keyword") or item.get("kw") or str(item))
            else:
                kw_display.append(str(item))

        prog_summary = (
            f"Word count: {prog.get('word_count', 'N/A')}, "
            f"H2 count: {prog.get('h2_count', 'N/A')}, "
            f"Has FAQ: {prog.get('has_faq', False)}, "
            f"Programmatic score: {prog.get('score', 0)}/100"
        )

        system = (
            "You are a senior SEO content auditor. Evaluate the article excerpt and return "
            "a JSON object with your findings. Be specific and actionable."
        )

        user = f"""Audit this SEO article and return a JSON object.

Focus keyword: {focus_kw}
SEO title: {seo_title}
Target keywords: {', '.join(kw_display[:15]) if kw_display else '(none)'}
Programmatic checks summary: {prog_summary}

Article excerpt (first 3000 chars):
{article_preview}

Return a JSON object with exactly these keys:
{{
  "quality_score": <integer 0-100>,
  "keyword_coverage": <integer 0-100, % of target keywords found>,
  "missing_keywords": ["keyword1", "keyword2"],
  "issues": ["specific issue 1", "specific issue 2"],
  "warnings": ["warning 1", "warning 2"],
  "strengths": ["strength 1", "strength 2"],
  "rewrite_instructions": "Detailed paragraph of specific improvements to make",
  "verdict": "pass" | "needs_work" | "fail"
}}

Evaluate: content depth, keyword integration, readability, structure, E-E-A-T signals, uniqueness."""

        response = self.client.chat.completions.create(
            model=model,
            max_tokens=1000,
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
            logger.warning("AuditAgent._ai_audit: failed to parse JSON response")
            result = {}

        # Ensure expected keys exist with safe defaults
        result.setdefault("quality_score", 0)
        result.setdefault("keyword_coverage", 0)
        result.setdefault("missing_keywords", [])
        result.setdefault("issues", [])
        result.setdefault("warnings", [])
        result.setdefault("strengths", [])
        result.setdefault("rewrite_instructions", "")
        result.setdefault("verdict", "needs_work")

        return result

    # ── Grading ───────────────────────────────────────────────────────────────

    def _grade(self, score: int) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"
