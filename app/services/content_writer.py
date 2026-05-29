"""
SEO content writer powered by Claude.
Writes full HTML articles with internal + external links.
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.agents.base import get_client, get_model, build_messages
from app.models.blog_post import BlogPost, Platform, PostStatus

logger = logging.getLogger(__name__)


class ContentWriter:
    def __init__(self):
        self.client = get_client()

    # ── Product helpers ───────────────────────────────────────────────────────

    async def _fetch_live_products(
        self,
        db: Session,
        focus_keyword: str,
        shop_domain: str,
        limit: int = 8,
    ) -> list[dict]:
        """
        Fetch fresh product data from Shopify at generation time.
        No local DB read — always up-to-date price, description, availability.
        Returns [] silently on any error (product context is optional).
        """
        try:
            from app.models.shopify_store import ShopifyStore
            store = db.query(ShopifyStore).filter_by(shop_domain=shop_domain).first()
            if not store or not store.access_token:
                return []
            from app.services.product_syncer import fetch_products_for_keyword
            return await fetch_products_for_keyword(
                shop_domain=shop_domain,
                access_token=store.access_token,
                keyword=focus_keyword,
                limit=limit,
            )
        except Exception:
            return []

    # ── Internal link helpers ─────────────────────────────────────────────────

    def _find_related_posts(
        self,
        db: Session,
        focus_keyword: str,
        tags: list[str],
        exclude_slug: str = None,
        shop_domain: Optional[str] = None,
    ) -> list[BlogPost]:
        """Find existing posts relevant for internal linking, scoped to the store."""
        q = db.query(BlogPost).filter(BlogPost.platform_url.isnot(None))
        if shop_domain:
            q = q.filter(BlogPost.shop_domain == shop_domain)
        posts = q.order_by(BlogPost.published_at.desc()).limit(100).all()

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

    # ── Word count helpers ────────────────────────────────────────────────────

    @staticmethod
    def _count_words(html: str) -> int:
        """Count visible words in HTML (strips tags first)."""
        text = re.sub(r'<[^>]+>', ' ', html or '')
        text = re.sub(r'\s+', ' ', text).strip()
        return len(text.split()) if text else 0

    def _word_targets_block(self, word_count: int, outline: list, paa_count: int) -> str:
        """Build a prescriptive per-section word-count instruction for the prompt."""
        n_sec   = max(1, len(outline))
        faq_w   = max(120, min(350, paa_count * 65))
        intro_w = max(120, int(word_count * 0.08))
        conc_w  = max(100, int(word_count * 0.07))
        body_w  = max(n_sec * 150, word_count - intro_w - conc_w - faq_w)
        per_sec = max(150, body_w // n_sec)
        return (
            f"WORD COUNT — MANDATORY: Write exactly {word_count} words (±5%). "
            f"Count every visible word.\n"
            f"  • Introduction: ≥{intro_w} words\n"
            f"  • Each of the {n_sec} H2 body section(s): ≥{per_sec} words "
            f"(use examples, sub-points, data — no filler)\n"
            f"  • FAQ ({paa_count} Qs): ≥{faq_w} words total\n"
            f"  • Conclusion: ≥{conc_w} words\n"
            f"  ▸ Finish each section before moving on. "
            f"If a section is under its minimum, expand it with more depth."
        )

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
        notes: Optional[str] = None,
        market: str = "us",
        article_type: Optional[str] = None,
        products: Optional[list] = None,
        platform_guideline: str = "",
    ) -> tuple[str, str]:
        """Returns (system_prompt, user_prompt)."""

        internal_ctx = ""
        if internal_posts:
            internal_ctx = "\n\nAvailable internal links — embed 2–3 of these naturally in the body:\n"
            for p in internal_posts:
                url = p.platform_url or f"/blogs/news/{p.slug}"
                internal_ctx += f'- <a href="{url}">{p.title}</a>\n'
        else:
            # No published posts yet — provide generic store URL patterns
            kw_slug = re.sub(r"[^a-z0-9]+", "-", focus_keyword.lower()).strip("-")
            internal_ctx = (
                "\n\nNo existing articles found. Add 2 internal links using these Shopify URL patterns:\n"
                f'- <a href="/blogs/news/{kw_slug}-guide">explore our guides</a>\n'
                '- <a href="/collections/all">shop our full range</a>\n'
                '- <a href="/pages/about">learn about us</a>\n'
                "Use these patterns (or similar) naturally in the article body."
            )

        external_ctx = ""
        _ALLOWED_DOMAINS = ("wikipedia.org", "who.int", "nih.gov", "cdc.gov", "usda.gov",
                            ".gov", ".edu", "pubmed.ncbi", "doi.org", "ncbi.nlm.nih.gov")
        if external_refs:
            authority_refs = [
                r for r in external_refs
                if any(d in r.get("url", "") for d in _ALLOWED_DOMAINS)
            ]
            if authority_refs:
                external_ctx = "\n\nAuthoritative references ONLY — Wikipedia / .gov / .edu / peer-reviewed only:\n"
                for r in authority_refs[:2]:
                    external_ctx += f'- <a href="{r["url"]}" target="_blank" rel="noopener noreferrer">{r["title"]}</a>: {r.get("snippet", "")}\n'
                external_ctx += "(Do NOT link any commercial, retail, or brand sites — use them for research only, not as hyperlinks)\n"

        bp = brand_profile or {}
        tone_instruction = bp.get("tone_of_voice") or tone
        type_ctx = f"\n- Article type: {article_type}" if article_type else ""

        # ── Brand rules block — injected FIRST, enforced as highest priority ──
        brand_block = ""
        if any(bp.get(k) for k in ("brand_name", "brand_description", "brand_style",
                                    "tone_of_voice", "output_requirements", "writing_notes")):
            lines = ["━━━ BRAND RULES — MANDATORY — OVERRIDE EVERYTHING ELSE ━━━"]
            if bp.get("brand_name"):
                lines.append(f"Brand: {bp['brand_name']}")
            if bp.get("brand_description"):
                lines.append(f"Brand: {bp['brand_description']}")
            if bp.get("brand_style"):
                lines.append(f"Style: {bp['brand_style']}")
            if bp.get("tone_of_voice"):
                lines.append(f"Tone of voice: {bp['tone_of_voice']}")
            if bp.get("output_requirements"):
                lines.append(f"\nOutput requirements (follow exactly):\n{bp['output_requirements']}")
            if bp.get("writing_notes"):
                lines.append(
                    f"\n⚠️ CRITICAL WRITING NOTES — These are NON-NEGOTIABLE constraints.\n"
                    f"Violating any of these is NOT acceptable under any circumstance:\n"
                    f"{bp['writing_notes']}"
                )
            lines.append("━━━ END BRAND RULES ━━━\n")
            brand_block = "\n".join(lines) + "\n"

        lessons_ctx = ""
        if feedback_lessons:
            lessons_ctx = "\n\nLessons from feedback on previous articles (apply these):\n"
            lessons_ctx += "\n".join(f"- {l}" for l in feedback_lessons)

        notes_ctx = ""
        if notes and notes.strip():
            notes_ctx = (
                "\n\nUser notes for this specific article — follow these strictly:\n"
                f"{notes.strip()}"
            )

        platform_ctx = ""
        if platform_guideline and platform_guideline.strip():
            platform_ctx = (
                "\n\n━━━ PLATFORM-SPECIFIC SEO GUIDELINES — MANDATORY ━━━\n"
                "Apply the following platform guidelines to this article. "
                "These rules are authoritative and override generic defaults where they conflict.\n\n"
                f"{platform_guideline.strip()}\n"
                "━━━ END PLATFORM GUIDELINES ━━━"
            )

        product_ctx = ""
        if products:
            product_ctx = "\n\nSTORE PRODUCTS — use these for accurate internal links and recommendations:\n"
            for p in products:
                price_min = p.get("price_min") if isinstance(p, dict) else getattr(p, "price_min", None)
                currency  = p.get("currency",  "") if isinstance(p, dict) else getattr(p, "currency",  "")
                title     = p.get("title",     "") if isinstance(p, dict) else getattr(p, "title",     "")
                url       = p.get("platform_url","") if isinstance(p, dict) else getattr(p, "platform_url","")
                ptype     = p.get("product_type","") if isinstance(p, dict) else getattr(p, "product_type","")
                tags      = p.get("tags",      []) if isinstance(p, dict) else getattr(p, "tags",      [])
                desc      = (p.get("description_text","") if isinstance(p, dict) else getattr(p, "description_text","") or "")[:120].strip()

                price_str = f" ({currency} {price_min:.0f})" if price_min else ""
                product_ctx += f'- [{title}{price_str}]({url})'
                if ptype:
                    product_ctx += f' | Type: {ptype}'
                if tags:
                    product_ctx += f' | Tags: {", ".join(tags[:5])}'
                if desc:
                    product_ctx += f'\n  {desc}'
                product_ctx += "\n"
            product_ctx += (
                "\nProduct link rules:\n"
                "- Insert 2-4 product links naturally in the body using <a href='URL'>Product Name</a>\n"
                "- At the end of the article, add a <section class=\"recommended-products\"> block with "
                "2-3 specific product recommendations (only if they are genuinely relevant to the topic)\n"
                "- Use exact product URLs from the list above — do NOT invent URLs"
            )

        system = f"""{brand_block}You are a professional SEO content writer.

Writing rules:
- Write in {language} for the {market.upper()} market, tone: {tone_instruction}{type_ctx}
- {self._word_targets_block(word_count, outline, len(paa_questions))}
- NEVER use <h1> tags — Shopify automatically generates H1 from the article title
- Use focus keyword in: first 100 words, at least 2 <h2> headings, and {max(10, int(word_count * 0.015))} times total across the article (≈1.5% density — count carefully before finishing)
- Structure: intro paragraph → <h2> sections → FAQ (from PAA) → conclusion paragraph
- Use proper HTML tags: <h2>, <h3>, <p>, <ul>, <li>, <strong>
- Never use <html>, <head>, <body> tags
- End with a <section class="faq"> containing PAA questions as <h3> + <p> answers

━━━ LINK STRATEGY — MANDATORY ━━━
INTERNAL LINKS — every navigational or CTA phrase MUST link internally:
✓ Correct: <a href="/blogs/news/slug">learn more about X</a>
✓ Correct: <a href="/products/slug">discover our Y</a>
✗ FORBIDDEN: linking "learn more / read more / discover / explore /
  find out / check out / see more / click here" to any external domain
→ Use the internal links provided for these phrases

EXTERNAL LINKS — only 2 permitted uses:
✓ Define or explain a technical term, concept, or industry keyword
  (Wikipedia, official body, authoritative definition — NOT a retail or commercial site)
✓ Cite a specific statistic, clinical study, or data source inline
  (peer-reviewed journal, government/academic source ONLY — NOT a brand, blog, or retailer)
✗ NEVER external links on navigational phrases
✗ NEVER external links as calls-to-action
✗ NEVER link to any commercial website, competitor, retailer, or brand as a "source"
→ If a fact comes from a commercial site: state it as general knowledge WITHOUT a link
→ External links must use: target="_blank" rel="noopener noreferrer"
━━━ END LINK STRATEGY ━━━

━━━ SEMANTIC KEYWORD SET — build topic authority ━━━
Identify 8–12 semantically related keywords/LSI phrases that reinforce the topic authority of this article.
These must:
1. Appear naturally integrated in the body — NOT forced or stuffed
2. Cover related subtopics, synonyms, supporting concepts, and long-tail variants
3. Be linked where it adds genuine value:
   - Prefer internal links (use the list provided) — anchor the phrase to a related article on the same site
   - Use an external authority link ONLY for technical terms needing a definition (see link rules above)
   - Leave unlinked if no relevant destination exists
4. Include the final list in the meta JSON as "semantic_keywords": ["term1", "term2", ...]

Goal: Google must recognize this article as expert-level content covering the FULL semantic field
of the topic — not exact-match keyword stuffing but rich, interconnected topic coverage.
━━━ END SEMANTIC KEYWORD SET ━━━{lessons_ctx}{notes_ctx}{kb_context}{product_ctx}{platform_ctx}"""

        user = f"""Write a complete SEO article (do NOT include an H1 — the title is handled by the platform):

**Article title:** {title}
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
{{"seo_title": "60-char SEO title", "meta_description": "155-char description with keyword", "tags": ["tag1","tag2","tag3","tag4","tag5"], "image_prompt": "DALL-E prompt for a professional blog banner", "semantic_keywords": ["related term 1", "related term 2", "...up to 12 terms"]}}
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
        notes: Optional[str] = None,
        market: str = "us",
        article_type: Optional[str] = None,
        target_platform: str = "google",
    ) -> dict:
        """Generate SEO article with internal + external links."""

        paa_questions = paa_questions or []
        external_refs = external_refs or []

        # Find related posts for internal linking
        internal_posts = []
        if db:
            internal_posts = self._find_related_posts(
                db, focus_keyword, [], exclude_slug, shop_domain=shop_domain
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

        # Auto-inject Learning Agent lessons (from past audits & user feedback)
        # Works even when writing outside the full pipeline (e.g. direct Generate)
        if db and shop_domain:
            try:
                from app.agents.learning_agent import LearningAgent
                lessons_ctx = LearningAgent().get_lessons_context(shop_domain, db)
                if lessons_ctx:
                    kb_context = (kb_context or "") + lessons_ctx
            except Exception:
                pass

        # Fetch live product data from Shopify (always fresh — never from local cache)
        products = []
        if db and shop_domain:
            products = await self._fetch_live_products(db, focus_keyword, shop_domain)

        # Platform-specific guideline
        platform_guideline = ""
        if db and target_platform:
            try:
                from app.services.platform_guidelines import get_guideline_content
                platform_guideline = get_guideline_content(target_platform, db)
            except Exception:
                pass

        system, user = self._build_prompt(
            title, focus_keyword, outline, paa_questions,
            external_refs, internal_posts, language, tone, word_count,
            brand_profile=brand_profile,
            feedback_lessons=feedback_lessons,
            kb_context=kb_context,
            notes=notes,
            market=market,
            article_type=article_type,
            products=products,
            platform_guideline=platform_guideline,
        )

        model = get_model("copywrite")
        messages = build_messages(system, user, model)

        # Allow ~5 tokens per output word (HTML overhead) with a generous buffer
        max_tokens = min(16000, max(4096, int(word_count * 5)))

        message = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )

        raw = message.choices[0].message.content

        # Parse response
        article_match = re.search(r"<article>(.*?)</article>", raw, re.DOTALL)
        meta_match = re.search(r"<meta>\s*(\{.*?\})\s*</meta>", raw, re.DOTALL)

        content_html = article_match.group(1).strip() if article_match else raw

        # Enforce word count: expand if AI wrote significantly less than requested
        actual = self._count_words(content_html)
        threshold = int(word_count * 0.88)
        if actual < threshold:
            logger.info(
                "Word count short: got %d / %d requested — running expansion",
                actual, word_count,
            )
            for attempt in range(2):
                content_html = await self._expand_content(
                    content_html, actual, word_count, focus_keyword, brand_profile
                )
                actual = self._count_words(content_html)
                logger.info("Expansion attempt %d: now %d words", attempt + 1, actual)
                if actual >= threshold:
                    break

        meta = {}
        if meta_match:
            try:
                meta = json.loads(meta_match.group(1))
            except json.JSONDecodeError:
                pass

        # Inject internal links used
        internal_links_used = [p.id for p in internal_posts]
        image_prompt = meta.get("image_prompt", f"Professional blog banner about {focus_keyword}")

        # Generate featured image and embed it in the article body
        image_url = None
        try:
            from app.services.image_generator import ImageGenerator
            img = ImageGenerator().generate(image_prompt)
            image_url = img.get("url")
        except Exception:
            pass

        # Inject the banner image after the first paragraph so SeoAuditor
        # can count it and Shopify renders it inside the article body
        if image_url:
            img_tag = (
                f'<figure class="article-banner" style="margin:0 0 1.5em">'
                f'<img src="{image_url}" alt="{focus_keyword}" '
                f'style="width:100%;max-width:900px;height:auto;border-radius:6px" loading="lazy">'
                f'</figure>'
            )
            if '</p>' in content_html:
                content_html = content_html.replace('</p>', f'</p>\n{img_tag}', 1)
            else:
                content_html = img_tag + '\n' + content_html

        return {
            "content_html": content_html,
            "seo_title": meta.get("seo_title", title[:60]),
            "seo_description": meta.get("meta_description", ""),
            "tags": meta.get("tags", []),
            "image_prompt": image_prompt,
            "image_url": image_url,
            "internal_links": internal_links_used,
            "semantic_keywords": meta.get("semantic_keywords", []),
            "usage": {
                "input_tokens": message.usage.prompt_tokens,
                "output_tokens": message.usage.completion_tokens,
            },
        }

    # ── Content expansion ─────────────────────────────────────────────────────

    async def _expand_content(
        self,
        html: str,
        current_words: int,
        target_words: int,
        focus_keyword: str,
        brand_profile: Optional[dict] = None,
    ) -> str:
        """Add genuine depth to an under-length article to hit the target word count."""
        deficit = target_words - current_words
        bp = brand_profile or {}
        tone_hint = f"\nMaintain tone: {bp['tone_of_voice']}" if bp.get("tone_of_voice") else ""

        model = get_model("copywrite")
        expand_system = (
            f"You are an expert SEO content editor.{tone_hint}\n"
            "Expand articles to hit a precise word count — no padding."
        )
        expand_user = (
            f'This article about "{focus_keyword}" has {current_words} words '
            f"but must reach {target_words} words.\n\n"
            f"Add ≈{deficit} words by:\n"
            f"1. Expanding H2 sections under 150 words with depth, examples, or data\n"
            f"2. Lengthening FAQ answers that are under 80 words each\n"
            f"3. Adding one new relevant H2 section if body is still short\n\n"
            f"Rules:\n"
            f"- Return the COMPLETE expanded article HTML (all existing + new content)\n"
            f"- Keep all existing links, headings, and structure intact\n"
            f"- No filler: every added sentence must convey real information\n\n"
            f"Current article:\n{html}"
        )
        resp = self.client.chat.completions.create(
            model=model,
            max_tokens=min(16000, int(target_words * 5)),
            messages=build_messages(expand_system, expand_user, model),
        )
        expanded = resp.choices[0].message.content.strip()
        # Strip accidental <article> wrapper if the model added one
        m = re.search(r"<article>(.*?)</article>", expanded, re.DOTALL)
        return m.group(1).strip() if m else expanded

    # ── Title suggestions ─────────────────────────────────────────────────────

    def suggest_titles(
        self,
        focus_keyword: str,
        language: str = "en",
        market: str = "us",
        article_type: Optional[str] = None,
        notes: Optional[str] = None,
        count: int = 5,
    ) -> list[str]:
        """Return `count` SEO-optimized title suggestions."""
        type_hint = f"\nArticle type: {article_type}" if article_type else ""
        notes_hint = f"\nUser notes (incorporate these): {notes.strip()}" if notes and notes.strip() else ""

        system = (
            "You are an SEO title strategist. You generate blog article titles "
            "that rank well on Google and earn clicks."
        )
        user = f"""Generate {count} SEO-optimized blog article titles in {language} for the {market.upper()} market.

Focus keyword: {focus_keyword}{type_hint}{notes_hint}

Rules:
- 50–65 characters each (Google truncates past 60)
- Include the focus keyword naturally (front-loaded if possible)
- Include 1–2 supporting long-tail keywords or modifiers (year, benefit, audience, location) — but keep titles meaningful, not stuffed
- Vary the angle across the {count} options: question, listicle, how-to, year-based, benefit-driven, comparison
- Title-case for English; sentence-case for languages where that's standard
- No clickbait

Return ONLY a JSON array of {count} strings, no commentary. Example:
["Best Wireless Earbuds for Running in 2025", "How to Pick Running Earbuds That Don't Fall Out"]"""

        model = get_model("copywrite")
        message = self.client.chat.completions.create(
            model=model,
            max_tokens=600,
            messages=build_messages(system, user, model),
        )
        raw = (message.choices[0].message.content or "").strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        try:
            titles = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        return [str(t).strip() for t in titles if isinstance(t, str) and t.strip()][:count]

    # ── Rewrite ───────────────────────────────────────────────────────────────

    async def rewrite(
        self,
        post: BlogPost,
        instructions: str,
        brand_profile: Optional[dict] = None,
        feedback_lessons: Optional[list] = None,
        target_word_count: Optional[int] = None,
    ) -> dict:
        """Rewrite an existing draft based on user instructions."""
        bp = brand_profile or {}

        rewrite_brand_block = ""
        if any(bp.get(k) for k in ("brand_name", "tone_of_voice", "output_requirements", "writing_notes")):
            lines = ["━━━ BRAND RULES — MANDATORY — OVERRIDE EVERYTHING ELSE ━━━"]
            if bp.get("brand_name"):
                lines.append(f"Brand: {bp['brand_name']}")
            if bp.get("tone_of_voice"):
                lines.append(f"Tone of voice: {bp['tone_of_voice']}")
            if bp.get("output_requirements"):
                lines.append(f"Output requirements:\n{bp['output_requirements']}")
            if bp.get("writing_notes"):
                lines.append(
                    f"\n⚠️ CRITICAL WRITING NOTES — Non-negotiable:\n{bp['writing_notes']}"
                )
            lines.append("━━━ END BRAND RULES ━━━\n")
            rewrite_brand_block = "\n".join(lines) + "\n"

        lessons_ctx = ""
        if feedback_lessons:
            lessons_ctx = "\n\nLessons from past feedback (keep these in mind):\n"
            lessons_ctx += "\n".join(f"- {l}" for l in feedback_lessons)

        orig_words = self._count_words(post.content_html or "")
        wc_rule = ""
        if target_word_count:
            wc_rule = (
                f"\n\nWORD COUNT — MANDATORY: The rewritten article MUST contain "
                f"{target_word_count} words (±5%). Current: {orig_words} words. "
                f"You must add ≈{max(0, target_word_count - orig_words)} words of genuine content. "
                f"Count every visible word before finishing."
            )

        system = f"""{rewrite_brand_block}You are an expert SEO content editor.{lessons_ctx}{wc_rule}

Your job is to rewrite the given article based on the user's instructions.
Rules:
- Apply ALL the rewrite instructions precisely
- Keep the focus keyword and SEO structure intact
- Use proper HTML: <h2>, <h3>, <p>, <ul>, <li>, <strong> — never <html>/<head>/<body>
- Keep a <section class="faq"> if the original has one (update it if relevant)

━━━ LINK STRATEGY — MANDATORY ━━━
INTERNAL LINKS — every navigational or CTA phrase MUST link internally:
✓ Correct: <a href="/blogs/news/slug">learn more about X</a>
✓ Correct: <a href="/products/slug">discover our Y</a>
✗ FORBIDDEN: "learn more / read more / discover / explore / find out / check out / see more" linked to any external domain
→ If the original article has external links on these phrases, replace them with the nearest matching internal URL or remove the link entirely

EXTERNAL LINKS — only 2 permitted uses:
✓ Define or explain a technical term, concept, or industry keyword
✓ Cite a specific statistic, study, or authoritative data source inline
✗ All other external links on navigational/CTA phrases must be removed or converted to internal links
→ Keep rel="noopener noreferrer" on all retained external links
━━━ END LINK STRATEGY ━━━"""

        wc_note = (
            f"\n**Word count target: {target_word_count} words "
            f"(current: {orig_words} words — add ≈{max(0, target_word_count - orig_words)} words)**"
            if target_word_count else ""
        )

        user = f"""Rewrite this article based on the instructions below.{wc_note}

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

        model = get_model("copywrite")
        # Need tokens for target size (not just original) when expanding
        effective_words = max(orig_words, target_word_count or 0)
        rw_max_tokens   = min(16000, max(4096, int(effective_words * 5)))
        message = self.client.chat.completions.create(
            model=model,
            max_tokens=rw_max_tokens,
            messages=build_messages(system, user, model),
        )
        raw = message.choices[0].message.content
        article_match = re.search(r"<article>(.*?)</article>", raw, re.DOTALL)
        meta_match    = re.search(r"<meta>\s*(\{.*?\})\s*</meta>", raw, re.DOTALL)

        content_html = article_match.group(1).strip() if article_match else raw

        # Expansion loop: if word count is still short after rewrite, expand
        if target_word_count:
            actual = self._count_words(content_html)
            threshold = int(target_word_count * 0.88)
            if actual < threshold:
                logger.info(
                    "Rewrite word count short: got %d / %d — running expansion",
                    actual, target_word_count,
                )
                for attempt in range(2):
                    content_html = await self._expand_content(
                        content_html, actual, target_word_count,
                        post.focus_keyword or "", brand_profile
                    )
                    actual = self._count_words(content_html)
                    logger.info("Rewrite expansion attempt %d: now %d words", attempt + 1, actual)
                    if actual >= threshold:
                        break
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
        shop_domain: Optional[str] = None,
    ) -> BlogPost:
        """Save generated article as draft in DB."""
        post = BlogPost(
            platform=platform,
            platform_id=None,
            shop_domain=shop_domain,
            channel_id=channel_id,
            title=title,
            slug=slug,
            content_html=result["content_html"],
            seo_title=result["seo_title"],
            seo_description=result["seo_description"],
            focus_keyword=focus_keyword,
            tags=result["tags"],
            image_prompt=result.get("image_prompt"),
            featured_image_url=result.get("image_url"),
            internal_links=result["internal_links"],
            semantic_keywords=result.get("semantic_keywords", []),
            status=PostStatus.DRAFT,
            source="generated",
            published_at=None,
            synced_at=datetime.utcnow(),
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return post
