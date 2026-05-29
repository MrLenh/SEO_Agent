"""
CopywriteAgent: Orchestrates article writing and rewriting by combining research
context, brand profile, audit feedback, and ContentWriter.
"""
import logging
from types import SimpleNamespace
from typing import Optional

from app.services.content_writer import ContentWriter

logger = logging.getLogger(__name__)


class CopywriteAgent:

    async def write(
        self,
        title: str,
        research: dict,
        lessons: list,
        brand_profile: Optional[dict],
        shop_domain: str,
        db,
        language: str = "en",
        target_platform: str = "google",
        word_count: int = 1500,
        outline: Optional[list] = None,
        tone: str = "professional",
        market: str = "us",
        audit_feedback: Optional[str] = None,
        notes: Optional[str] = None,
        article_type: Optional[str] = None,
    ) -> dict:
        focus_keyword = research.get("primary_keyword", "")
        paa = research.get("people_also_ask", [])
        top_results = research.get("top_results", [])

        # Build external refs from top SERP results
        external_refs = [
            {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")}
            for r in top_results
            if r.get("url")
        ]

        # Resolve outline
        if outline is None:
            outline = research.get("suggested_outline", [
                f"What Is {focus_keyword}?",
                f"Benefits of {focus_keyword}",
                f"How to Get Started with {focus_keyword}",
                "Tips and Best Practices",
                "Common Mistakes to Avoid",
                "Frequently Asked Questions",
            ])

        # Build semantic keyword context from ranked keywords
        ranked_keywords = research.get("ranked_keywords", [])[:10]
        semantic_terms = [kw.get("keyword", "") for kw in ranked_keywords if kw.get("keyword")]

        # Compose enhanced notes
        notes_parts = []

        if audit_feedback and audit_feedback.strip():
            notes_parts.append(
                f"AUDIT FEEDBACK - apply these corrections:\n{audit_feedback.strip()}"
            )

        if notes and notes.strip():
            notes_parts.append(notes.strip())

        if semantic_terms:
            notes_parts.append(
                "Semantic keywords to integrate naturally (cover these related terms for topic authority):\n"
                + ", ".join(semantic_terms)
            )

        # Append kb_context hint about internal links if present
        kb_context_raw = research.get("kb_context", "")
        if kb_context_raw and kb_context_raw.strip():
            notes_parts.append(
                "Knowledge base context (use for internal linking, avoid duplicating):\n"
                + kb_context_raw.strip()
            )

        enhanced_notes = "\n\n".join(notes_parts) if notes_parts else None

        result = await ContentWriter().write(
            title=title,
            focus_keyword=focus_keyword,
            outline=outline,
            paa_questions=paa,
            external_refs=external_refs,
            language=language,
            tone=tone,
            word_count=word_count,
            db=db,
            brand_profile=brand_profile,
            feedback_lessons=lessons,
            shop_domain=shop_domain,
            notes=enhanced_notes,
            market=market,
            article_type=article_type,
            target_platform=target_platform,
        )

        return result

    async def rewrite_with_feedback(
        self,
        article: dict,
        audit_feedback: dict,
        lessons: list,
        db,
        brand_profile: Optional[dict] = None,
    ) -> dict:
        # Build rewrite instructions from structured audit feedback
        instruction_parts = []

        rewrite_instructions = audit_feedback.get("feedback_for_rewrite", "")
        if rewrite_instructions and rewrite_instructions.strip():
            instruction_parts.append(rewrite_instructions.strip())
        else:
            issues = audit_feedback.get("issues", [])
            warnings = audit_feedback.get("warnings", [])
            if issues:
                instruction_parts.append(
                    "Fix the following issues:\n" + "\n".join(f"- {i}" for i in issues)
                )
            if warnings:
                instruction_parts.append(
                    "Address the following warnings:\n" + "\n".join(f"- {w}" for w in warnings)
                )

        # Append lessons from past feedback
        if lessons:
            instruction_parts.append(
                "Apply these lessons from past feedback:\n"
                + "\n".join(f"- {l}" for l in lessons)
            )

        instructions = "\n\n".join(instruction_parts) if instruction_parts else "Improve the article quality and SEO."

        # Build a BlogPost-like object so ContentWriter.rewrite() can consume it
        post = SimpleNamespace(
            content_html=article.get("content_html", ""),
            title=article.get("title", ""),
            seo_title=article.get("seo_title", ""),
            focus_keyword=article.get("focus_keyword", ""),
            seo_description=article.get("seo_description", ""),
            tags=article.get("tags", []),
            image_prompt=article.get("image_prompt", ""),
        )

        rewrite_result = await ContentWriter().rewrite(
            post=post,
            instructions=instructions,
            brand_profile=brand_profile,
            feedback_lessons=lessons,
        )

        # Persist to DB if the article has an ID
        article_id = article.get("id")
        if article_id and db is not None:
            try:
                from app.models.blog_post import BlogPost
                db_post = db.query(BlogPost).filter(BlogPost.id == article_id).first()
                if db_post:
                    db_post.content_html = rewrite_result.get("content_html", db_post.content_html)
                    db_post.seo_title = rewrite_result.get("seo_title", db_post.seo_title)
                    db_post.seo_description = rewrite_result.get("seo_description", db_post.seo_description)
                    db_post.tags = rewrite_result.get("tags", db_post.tags)
                    if rewrite_result.get("image_prompt"):
                        db_post.image_prompt = rewrite_result["image_prompt"]
                    db.commit()
                    db.refresh(db_post)
            except Exception as exc:
                logger.warning("CopywriteAgent: failed to update DB record %s: %s", article_id, exc)

        return {**article, **rewrite_result}

    async def surgical_fix(
        self,
        article: dict,
        audit_result: dict,
        target_word_count: int,
        db,
        brand_profile: Optional[dict] = None,
    ) -> dict:
        """Apply precise, targeted fixes for specific programmatic audit failures.

        Unlike a general rewrite, this method builds an explicit instruction for
        each failing check so the model applies only the minimal changes needed to
        push the article over the ready threshold.
        """
        prog = audit_result.get("programmatic", {})
        current_words = prog.get("word_count", 0)
        keyword = article.get("focus_keyword", "")
        issues = audit_result.get("issues", [])

        fixes: list[str] = []

        # Word count
        if current_words < int(target_word_count * 0.90):
            deficit = target_word_count - current_words
            fixes.append(
                f"WORD COUNT — CRITICAL: Article has {current_words} words but must reach "
                f"{target_word_count}. Add exactly {deficit} words by expanding existing H2 "
                "sections (each under 200 words) and lengthening FAQ answers to ≥80 words each. "
                "Do NOT change headings, links, or structure."
            )

        # Keyword not in first 100 words
        if any("first 100 words" in i or "first 100" in i for i in issues):
            fixes.append(
                f"INTRO — CRITICAL: The focus keyword '{keyword}' must appear within the very "
                "first 1-2 sentences of the article. Rewrite only the opening paragraph to "
                "include it naturally — do not alter anything else."
            )

        # Keyword density too low
        density = prog.get("keyword_density", 0)
        if density < 1.0 and keyword:
            target_count = max(10, int(target_word_count * 0.015))
            fixes.append(
                f"KEYWORD DENSITY — CRITICAL: '{keyword}' appears only {density:.1f}% of the time. "
                f"Increase to at least 1.5% by using the keyword approximately {target_count} times "
                "throughout the article — in headings, body paragraphs, and FAQ answers."
            )

        # No images
        if prog.get("img_count", 0) == 0:
            fixes.append(
                "IMAGES — CRITICAL: Add at least 1 relevant image with descriptive alt text. "
                "Insert an <img> tag with a meaningful alt attribute in the article body."
            )

        # No internal links
        if prog.get("internal_link_count", 0) == 0:
            fixes.append(
                "INTERNAL LINKS — CRITICAL: Add 2 internal links to related content. "
                "Use patterns like: "
                f'<a href="/blogs/news/{keyword.lower().replace(" ", "-")}-guide">related guide</a> '
                'or <a href="/collections/all">shop our products</a>. '
                "Place them naturally on navigational phrases in the body."
            )

        # FAQ missing
        if not prog.get("has_faq"):
            fixes.append(
                f"FAQ — CRITICAL: Add a <section class=\"faq\"> block at the end of the article "
                f"(before any closing paragraph) containing 4-5 questions and detailed answers "
                f"about '{keyword}'. Each answer must be at least 60 words."
            )

        # H2 count
        if prog.get("h2_count", 0) < 2:
            fixes.append(
                "STRUCTURE — CRITICAL: Article has fewer than 2 H2 headings. Add H2 section "
                "headings to break the content into clear sections. Each section needs a "
                "descriptive <h2> tag."
            )

        # Keyword not in SEO title
        if any("SEO title" in i for i in issues):
            fixes.append(
                f"SEO TITLE — CRITICAL: Update the SEO title so it naturally contains "
                f"'{keyword}'. Keep it 50-60 characters."
            )

        if not fixes:
            return article

        instructions = (
            "SURGICAL FIX — Apply ONLY the following targeted changes. "
            "Do NOT rewrite unaffected sections, do NOT change style or tone.\n\n"
            + "\n\n".join(fixes)
        )

        post = SimpleNamespace(
            content_html=article.get("content_html", ""),
            title=article.get("title", ""),
            seo_title=article.get("seo_title", ""),
            focus_keyword=keyword,
            seo_description=article.get("seo_description", ""),
            tags=article.get("tags", []),
            image_prompt=article.get("image_prompt", ""),
        )

        effective_wc = target_word_count if current_words < int(target_word_count * 0.90) else None
        rewrite_result = await ContentWriter().rewrite(
            post=post,
            instructions=instructions,
            brand_profile=brand_profile,
            target_word_count=effective_wc,
        )

        # Persist to DB
        article_id = article.get("id")
        if article_id and db is not None:
            try:
                from app.models.blog_post import BlogPost
                db_post = db.query(BlogPost).filter(BlogPost.id == article_id).first()
                if db_post:
                    db_post.content_html = rewrite_result.get("content_html", db_post.content_html)
                    db_post.seo_title    = rewrite_result.get("seo_title",    db_post.seo_title)
                    db_post.seo_description = rewrite_result.get("seo_description", db_post.seo_description)
                    db_post.tags         = rewrite_result.get("tags",         db_post.tags)
                    db.commit()
            except Exception as exc:
                logger.warning("CopywriteAgent.surgical_fix: DB update failed: %s", exc)

        return {**article, **rewrite_result}
