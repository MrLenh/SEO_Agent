"""
SEO content writer powered by Claude.
Writes full HTML articles with internal + external links.
"""
import json
import re
from datetime import datetime
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.models.blog_post import BlogPost, Platform, PostStatus


class ContentWriter:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ── Internal link helpers ─────────────────────────────────────────────────

    def _find_related_posts(
        self, db: Session, focus_keyword: str, tags: list[str], exclude_slug: str = None
    ) -> list[BlogPost]:
        """Find existing posts relevant for internal linking."""
        posts = (
            db.query(BlogPost)
            .filter(BlogPost.platform_url.isnot(None))
            .order_by(BlogPost.published_at.desc())
            .limit(100)
            .all()
        )

        kw_lower = focus_keyword.lower()
        scored = []
        for post in posts:
            if exclude_slug and post.slug == exclude_slug:
                continue
            post_tags = post.tags or []
            title_lower = (post.title or "").lower()

            score = 0
            if kw_lower in title_lower:
                score += 3
            if any(t.lower() in kw_lower or kw_lower in t.lower() for t in post_tags):
                score += 2
            if any(t in tags for t in post_tags):
                score += 1
            if score > 0:
                scored.append((score, post))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:5]]

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        title: str,
        focus_keyword: str,
        outline: list[str],
        paa_questions: list[str],
        external_refs: list[dict],
        internal_posts: list[BlogPost],
        language: str,
        tone: str,
        word_count: int,
        brand_profile: Optional[dict] = None,
        feedback_lessons: Optional[list] = None,
        kb_context: str = "",
    ) -> tuple[str, str]:
        """Returns (system_prompt, user_prompt)."""

        internal_ctx = ""
        if internal_posts:
            internal_ctx = "\n\nInsert 2-3 of these internal links naturally in the body:\n"
            for p in internal_posts:
                url = p.platform_url or f"/blogs/news/{p.slug}"
                internal_ctx += f'- <a href="{url}">{p.title}</a>\n'

        external_ctx = ""
        if external_refs:
            external_ctx = "\n\nInsert 2-3 of these external authority links naturally:\n"
            for r in external_refs[:3]:
                external_ctx += f'- <a href="{r["url"]}" target="_blank" rel="noopener">{r["title"]}</a>: {r.get("snippet", "")}\n'

        brand_ctx = ""
        bp = brand_profile or {}
        if bp.get("brand_name"):
            brand_ctx += f"\nBrand: {bp['brand_name']}"
        if bp.get("brand_description"):
            brand_ctx += f"\nBrand description: {bp['brand_description']}"
        if bp.get("brand_style"):
            brand_ctx += f"\nBrand style: {bp['brand_style']}"
        if bp.get("tone_of_voice"):
            brand_ctx += f"\nTone of voice: {bp['tone_of_voice']}"
        if bp.get("output_requirements"):
            brand_ctx += f"\n\nOutput requirements:\n{bp['output_requirements']}"

        tone_instruction = tone
        if bp.get("tone_of_voice"):
            tone_instruction = bp["tone_of_voice"]

        lessons_ctx = ""
        if feedback_lessons:
            lessons_ctx = "\n\nLessons from feedback on previous articles (apply these):\n"
            lessons_ctx += "\n".join(f"- {l}" for l in feedback_lessons)

        system = f"""You are a professional SEO content writer.{brand_ctx}

Writing rules:
- Write in {language}, tone: {tone_instruction}
- Target {word_count}+ words
- Use focus keyword in: H1, first 100 words, at least 2 H2s, naturally throughout (2-3% density)
- Structure: intro → H2 sections → FAQ (from PAA) → conclusion
- Use proper HTML tags: <h2>, <h3>, <p>, <ul>, <li>, <strong>
- Never use <html>, <head>, <body> tags
- End with a <section class="faq"> containing PAA questions as <h3> + <p> answers{lessons_ctx}{kb_context}"""

        user = f"""Write a complete SEO article:

**Title (H1):** {title}
**Focus keyword:** {focus_keyword}

**Outline:**
{chr(10).join(f'- {s}' for s in outline)}

**FAQ questions (People Also Ask):**
{chr(10).join(f'- {q}' for q in paa_questions[:6]) or '(none)'}
{internal_ctx}
{external_ctx}

Respond in this exact format:

<article>
[full HTML article content]
</article>
<meta>
{{"seo_title": "60-char SEO title", "meta_description": "155-char description with keyword", "tags": ["tag1","tag2","tag3","tag4","tag5"], "image_prompt": "DALL-E prompt for a professional blog banner"}}
</meta>"""

        return system, user

    # ── Main write method ─────────────────────────────────────────────────────

    async def write(
        self,
        title: str,
        focus_keyword: str,
        outline: list[str],
        paa_questions: list[str] = None,
        external_refs: list[dict] = None,
        language: str = "en",
        tone: str = "professional",
        word_count: int = 1500,
        db: Session = None,
        exclude_slug: str = None,
        brand_profile: Optional[dict] = None,
        feedback_lessons: Optional[list] = None,
        shop_domain: Optional[str] = None,
    ) -> dict:
        """Generate SEO article with internal + external links."""

        paa_questions = paa_questions or []
        external_refs = external_refs or []

        # Find related posts for internal linking
        internal_posts = []
        if db:
            internal_posts = self._find_related_posts(
                db, focus_keyword, [], exclude_slug
            )

        # Knowledge base context (avoid duplicates, guide internal links)
        kb_context = ""
        if db and shop_domain:
            try:
                from app.services.knowledge_base import KnowledgeBase
                kb_context = KnowledgeBase().get_context_for_article(
                    focus_keyword, title, shop_domain, db
                )
            except Exception:
                pass

        system, user = self._build_prompt(
            title, focus_keyword, outline, paa_questions,
            external_refs, internal_posts, language, tone, word_count,
            brand_profile=brand_profile,
            feedback_lessons=feedback_lessons,
            kb_context=kb_context,
        )

        message = self.client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_tokens=5000,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        raw = message.choices[0].message.content

        # Parse response
        article_match = re.search(r"<article>(.*?)</article>", raw, re.DOTALL)
        meta_match = re.search(r"<meta>\s*(\{.*?\})\s*</meta>", raw, re.DOTALL)

        content_html = article_match.group(1).strip() if article_match else raw

        meta = {}
        if meta_match:
            try:
                meta = json.loads(meta_match.group(1))
            except json.JSONDecodeError:
                pass

        # Inject internal links used
        internal_links_used = [p.id for p in internal_posts]

        return {
            "content_html": content_html,
            "seo_title": meta.get("seo_title", title[:60]),
            "seo_description": meta.get("meta_description", ""),
            "tags": meta.get("tags", []),
            "image_prompt": meta.get("image_prompt", f"Professional blog banner about {focus_keyword}"),
            "internal_links": internal_links_used,
            "usage": {
                "input_tokens": message.usage.prompt_tokens,
                "output_tokens": message.usage.completion_tokens,
            },
        }

    # ── Rewrite ───────────────────────────────────────────────────────────────

    async def rewrite(
        self,
        post: BlogPost,
        instructions: str,
        brand_profile: Optional[dict] = None,
        feedback_lessons: Optional[list] = None,
    ) -> dict:
        """Rewrite an existing draft based on user instructions."""
        bp = brand_profile or {}

        brand_ctx = ""
        if bp.get("brand_name"):
            brand_ctx += f"\nBrand: {bp['brand_name']}"
        if bp.get("tone_of_voice"):
            brand_ctx += f"\nTone of voice: {bp['tone_of_voice']}"
        if bp.get("output_requirements"):
            brand_ctx += f"\n\nOutput requirements:\n{bp['output_requirements']}"

        lessons_ctx = ""
        if feedback_lessons:
            lessons_ctx = "\n\nLessons from past feedback (keep these in mind):\n"
            lessons_ctx += "\n".join(f"- {l}" for l in feedback_lessons)

        system = f"""You are an expert SEO content editor.{brand_ctx}{lessons_ctx}

Your job is to rewrite the given article based on the user's instructions.
Rules:
- Apply ALL the rewrite instructions precisely
- Keep the focus keyword and SEO structure intact
- Preserve internal/external links already in the article unless explicitly told to remove them
- Use proper HTML: <h2>, <h3>, <p>, <ul>, <li>, <strong> — never <html>/<head>/<body>
- Keep a <section class="faq"> if the original has one (update it if relevant)"""

        user = f"""Rewrite this article based on the instructions below.

**Rewrite instructions:**
{instructions}

**Original article:**
{post.content_html or '(empty)'}

**Current metadata:**
- Title: {post.title}
- Focus keyword: {post.focus_keyword or '(none)'}
- SEO title: {post.seo_title or '(none)'}

Respond in this exact format:

<article>
[full rewritten HTML content]
</article>
<meta>
{{"seo_title": "60-char SEO title", "meta_description": "155-char description with keyword", "tags": ["tag1","tag2","tag3"], "image_prompt": "DALL-E prompt for a professional blog banner"}}
</meta>"""

        message = self.client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_tokens=5000,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        raw = message.choices[0].message.content
        article_match = re.search(r"<article>(.*?)</article>", raw, re.DOTALL)
        meta_match    = re.search(r"<meta>\s*(\{.*?\})\s*</meta>", raw, re.DOTALL)

        content_html = article_match.group(1).strip() if article_match else raw
        meta = {}
        if meta_match:
            try:
                meta = json.loads(meta_match.group(1))
            except json.JSONDecodeError:
                pass

        return {
            "content_html":    content_html,
            "seo_title":       meta.get("seo_title", post.seo_title or post.title[:60]),
            "seo_description": meta.get("meta_description", post.seo_description or ""),
            "tags":            meta.get("tags", post.tags or []),
            "image_prompt":    meta.get("image_prompt", post.image_prompt or ""),
            "usage": {
                "input_tokens":  message.usage.prompt_tokens,
                "output_tokens": message.usage.completion_tokens,
            },
        }

    # ── Save to DB ────────────────────────────────────────────────────────────

    def save_draft(
        self,
        db: Session,
        title: str,
        slug: str,
        focus_keyword: str,
        result: dict,
        platform: Platform,
        channel_id: Optional[int] = None,
        cluster_id: Optional[int] = None,
    ) -> BlogPost:
        """Save generated article as draft in DB."""
        post = BlogPost(
            platform=platform,
            platform_id=None,
            channel_id=channel_id,
            title=title,
            slug=slug,
            content_html=result["content_html"],
            seo_title=result["seo_title"],
            seo_description=result["seo_description"],
            focus_keyword=focus_keyword,
            tags=result["tags"],
            image_prompt=result.get("image_prompt"),
            internal_links=result["internal_links"],
            status=PostStatus.DRAFT,
            source="generated",
            published_at=None,
            synced_at=datetime.utcnow(),
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return post
