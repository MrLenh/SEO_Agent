import logging
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from app.models.blog_post import BlogPost

logger = logging.getLogger(__name__)

_CTA_PHRASES = re.compile(
    r'\b(learn more|read more|discover|explore|find out|check out|see more|click here|view more|get more)\b',
    re.IGNORECASE,
)


class SeoAuditor:
    def audit_post(self, post: BlogPost) -> dict:
        content = post.content_html or ""
        soup = BeautifulSoup(content, "lxml")
        text = soup.get_text(" ", strip=True)
        words = [w for w in text.split() if w]
        word_count = len(words)
        title = post.title or ""
        keyword = post.focus_keyword or ""

        issues = []
        warnings = []   # non-blocking, informational
        score = 0

        # ── Word count (20 pts) ────────────────────────────────────────────────
        if word_count >= 1500:
            score += 20
        elif word_count >= 800:
            score += 12
            issues.append(f"Content only {word_count} words — aim for 1500+")
        else:
            issues.append(f"Content too short ({word_count} words) — aim for 1500+")

        # ── Title length (15 pts) ──────────────────────────────────────────────
        title_len = len(title)
        if 50 <= title_len <= 60:
            score += 15
        elif 40 <= title_len <= 70:
            score += 10
            issues.append(f"Title is {title_len} chars — ideal is 50-60")
        else:
            issues.append(f"Title length {title_len} chars — ideal is 50-60")

        # ── Focus keyword (30 pts) ─────────────────────────────────────────────
        if keyword:
            score += 15
            if keyword.lower() in title.lower():
                score += 10
            else:
                issues.append(f"Focus keyword '{keyword}' not found in title")
            if word_count > 0:
                density = text.lower().count(keyword.lower()) / word_count * 100
                if 1 <= density <= 3:
                    score += 5
                elif density < 1:
                    issues.append(f"Keyword density {density:.1f}% — too low, aim for 1-3%")
                else:
                    issues.append(f"Keyword density {density:.1f}% — too high, may look spammy")
        else:
            issues.append("No focus keyword set")

        # ── Headings (15 pts) ─────────────────────────────────────────────────
        h2s = soup.find_all("h2")
        h3s = soup.find_all("h3")
        if len(h2s) >= 3:
            score += 15
        elif len(h2s) >= 1:
            score += 8
            issues.append(f"Only {len(h2s)} H2 heading(s) — aim for 3+")
        else:
            issues.append("No H2 headings — structure content with section headings")

        # ── Images (10 pts) ───────────────────────────────────────────────────
        images = soup.find_all("img")
        missing_alt = [img for img in images if not img.get("alt")]
        if images:
            if not missing_alt:
                score += 10
            else:
                score += 5
                issues.append(f"{len(missing_alt)} image(s) missing alt text")
        else:
            issues.append("No images in content — add relevant images")

        # ── Featured image (5 pts) ─────────────────────────────────────────────
        if post.featured_image_url:
            score += 5
        else:
            issues.append("No featured image")

        # ── Link quality (15 pts) ─────────────────────────────────────────────
        all_links = soup.find_all("a", href=True)
        internal_links = [a for a in all_links if not a["href"].startswith("http")]
        external_links = [a for a in all_links if a["href"].startswith("http")]

        # Check for external links on CTA phrases (should be internal)
        cta_external_leaks = []
        for a in external_links:
            anchor_text = a.get_text(strip=True)
            if _CTA_PHRASES.search(anchor_text):
                cta_external_leaks.append(anchor_text)

        if not cta_external_leaks:
            score += 8
        else:
            issues.append(
                f"External links on CTA phrases (should be internal): "
                + ", ".join(f'"{t}"' for t in cta_external_leaks[:3])
            )

        if len(internal_links) >= 2:
            score += 7
        elif len(internal_links) == 1:
            score += 3
            warnings.append("Only 1 internal link — aim for 2-4 internal links per article")
        else:
            issues.append("No internal links — add links to related content on the site")

        if external_links and not any(a.get("rel") for a in external_links):
            warnings.append('External links missing rel="noopener noreferrer"')

        # ── Semantic keywords coverage (5 pts) ────────────────────────────────
        semantic_kws = post.semantic_keywords or []
        if semantic_kws:
            covered = sum(1 for kw in semantic_kws if kw.lower() in text.lower())
            coverage_pct = covered / len(semantic_kws) * 100
            if coverage_pct >= 70:
                score += 5
            else:
                warnings.append(
                    f"Semantic keywords coverage {coverage_pct:.0f}% ({covered}/{len(semantic_kws)}) — "
                    "consider weaving more related terms into the content"
                )
        else:
            warnings.append("No semantic keyword set — regenerate article to build topic authority keywords")

        # Total possible raw points: 20+15+30+15+10+5+15+5 = 115
        # Normalize to 100 and cap so score never exceeds max
        MAX_RAW = 115
        normalized = min(100, round(score * 100 / MAX_RAW))

        return {
            "post_id": post.id,
            "title": title,
            "url": post.platform_url,
            "focus_keyword": keyword,
            "word_count": word_count,
            "title_length": title_len,
            "h2_count": len(h2s),
            "h3_count": len(h3s),
            "image_count": len(images),
            "images_missing_alt": len(missing_alt),
            "internal_link_count": len(internal_links),
            "external_link_count": len(external_links),
            "cta_external_leaks": cta_external_leaks,
            "semantic_keywords": semantic_kws,
            "score": normalized,
            "max_score": 100,
            "grade": "A" if normalized >= 85 else "B" if normalized >= 70 else "C" if normalized >= 55 else "D" if normalized >= 40 else "F",
            "issues": issues,
            "warnings": warnings,
        }

    # ── KB persistence ────────────────────────────────────────────────────────

    def save_to_kb(self, audit: dict, shop_domain: Optional[str], db) -> None:
        """
        Save audit issues/warnings to the Knowledge Base so the Learning Agent
        can include them when synthesizing writing lessons.
        No-ops silently when KB is unavailable or audit has no findings.
        """
        if not (audit.get("issues") or audit.get("warnings")):
            return
        try:
            from app.services.knowledge_base import KnowledgeBase

            article_title = (audit.get("title") or "Unknown article")[:60]
            kw            = audit.get("focus_keyword", "")
            date_str      = datetime.utcnow().strftime("%Y-%m-%d")

            issue_lines   = "\n".join(f"- ISSUE: {i}"   for i in audit.get("issues",   []))
            warning_lines = "\n".join(f"- WARNING: {w}" for w in audit.get("warnings", []))

            summary = (
                f"SEO Audit — {article_title}\n"
                f"Keyword: {kw} | Score: {audit.get('grade','?')} {audit.get('score',0)}/100 | "
                f"Words: {audit.get('word_count', 0)} | "
                f"H2: {audit.get('h2_count', 0)} | "
                f"Int.links: {audit.get('internal_link_count', 0)}"
            )
            if issue_lines:
                summary += f"\n{issue_lines}"
            if warning_lines:
                summary += f"\n{warning_lines}"

            KnowledgeBase().add_from_text(
                title=f"SEO Audit Issues — {article_title} ({date_str})",
                content_text=summary,
                content_md=f"# SEO Audit Issues\n\n```\n{summary}\n```",
                source_type="audit_result",
                shop_domain=shop_domain,
                db=db,
                auto_approve=True,
            )
        except Exception as exc:
            logger.warning("SeoAuditor.save_to_kb failed: %s", exc)
