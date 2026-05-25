"""
Platform-specific SEO guidelines.
Seeded on startup; admin can update via API.
"""
from sqlalchemy.orm import Session

DEFAULT_GUIDELINES: list[dict] = [
    {
        "platform": "google",
        "display_name": "Google Search",
        "icon": "🔍",
        "content": """GOOGLE SEARCH SEO GUIDELINES (2024-2025)

CORE RANKING SIGNALS:
- E-E-A-T: Experience, Expertise, Authoritativeness, Trustworthiness — Google's #1 quality signal
- Helpful Content: Write for humans first, search engines second. Avoid thin, AI-padded, or repetitive content.
- Core Web Vitals: LCP < 2.5s, FID < 100ms, CLS < 0.1 — ensure the article doesn't add layout-shifting elements

CONTENT STRUCTURE:
- Title tag: 50–60 characters, keyword near the front
- Meta description: 150–160 characters, include keyword + clear value proposition
- H1: One per page, matches article title, contains focus keyword
- H2/H3: Use keyword variations and semantic terms in headings
- Word count: 1500+ for competitive topics; 800+ for long-tail; depth beats length
- FAQ section: Structured with <h3> questions + <p> answers — directly targets featured snippets and PAA boxes
- First paragraph: Keyword in first 100 words, state the article's value immediately

INTERNAL LINKING:
- 3–5 internal links per 1000 words — distributes PageRank and reduces bounce rate
- Anchor text: Descriptive, keyword-rich (not "click here")
- Link to pillar pages, related articles, and product pages naturally in context

EXTERNAL LINKING:
- 1–2 external links to authoritative sources (Wikipedia, gov sites, peer-reviewed sources)
- Use rel="noopener noreferrer" on external links
- External links signal to Google that the article is well-researched

SEMANTIC SEO:
- Cover the full topical map: include related entities, subtopics, and LSI keywords
- Use structured data (FAQ, HowTo, Article) for rich results eligibility
- Natural keyword density: 1–2% for focus keyword; avoid stuffing

MOBILE & UX:
- Short paragraphs (2–4 sentences max) for mobile readability
- Use bullet lists and numbered lists for scannable content
- Include images with descriptive alt text (keyword where natural)""",
    },
    {
        "platform": "amazon",
        "display_name": "Amazon / Marketplace",
        "icon": "📦",
        "content": """AMAZON & MARKETPLACE CONTENT SEO GUIDELINES (2024-2025)

A9/A10 ALGORITHM PRIORITIES:
- Purchase intent is paramount: every sentence should move the reader toward buying
- Keyword relevance: front-load primary keyword in title and first bullet
- Conversion rate signals: A+ content, detailed descriptions, and answered questions improve ranking

CONTENT TONE & STRUCTURE:
- Benefit-first writing: lead with the problem solved, not features
- Use active voice and power words: "Eliminates", "Guarantees", "Proven to..."
- Short, punchy sentences — shoppers skim, not read
- Product comparisons: clearly state why this product wins on specific use cases

KEYWORD STRATEGY:
- Primary keyword: appears in title, first paragraph, and naturally 2–3× in body
- Long-tail buying keywords: "best X for Y", "X under $Z", "X for [audience]"
- Search intent is transactional — every keyword should map to a purchase decision
- Include brand name, model numbers, and compatibility terms as keywords

TRUST SIGNALS:
- Cite real specifications, certifications, and test results
- Reference real customer pain points (from reviews/Q&A)
- Include social proof language: "trusted by 10,000+ customers", "4.8 stars"
- Money-back guarantee and warranty mentions reduce purchase hesitation

INTERNAL LINKING:
- Link to related product categories, comparison pages, and buying guides on the same site
- CTA phrases: "shop our collection", "view all options", "compare models" — always internal links
- Never link CTA phrases to competitor or external product pages

ARTICLE TYPES THAT RANK:
- Buying guides: "Best [Product] for [Use Case] in 2025"
- Comparison articles: "[Product A] vs [Product B]: Which Should You Buy?"
- How-to articles that naturally mention the product as the solution""",
    },
    {
        "platform": "etsy",
        "display_name": "Etsy",
        "icon": "🛍️",
        "content": """ETSY SEO & CONTENT GUIDELINES (2024-2025)

ETSY SEARCH ALGORITHM:
- Relevancy score: title + tags + attributes must all match the buyer's search query
- Recency boost: new and recently-renewed listings get a temporary ranking boost
- Customer experience score: positive reviews, complete shop info, and fast responses

CONTENT FOCUS FOR BLOG/SITE ARTICLES LINKING TO ETSY:
- Handmade + unique angle: emphasize the artisan, custom, and one-of-a-kind nature
- Gift-focused keywords perform strongly on Etsy: "unique gift for", "personalized X", "custom Y for Z occasion"
- Seasonal and occasion-based content: holidays, weddings, birthdays drive most Etsy traffic
- Niche audiences: narrow targeting outperforms broad ("custom dog portrait" > "art")

KEYWORD STRATEGY:
- Use long-tail, shopper-intent phrases: "handmade silver ring with birthstone", "personalized wooden cutting board wedding gift"
- Combine product type + material + style + occasion: [item] + [material] + [style] + [recipient/occasion]
- Avoid generic keywords — specificity wins on Etsy

TONE & STYLE:
- Warm, personal, and storytelling-driven: buyers want to connect with the maker
- Describe the making process where relevant — it builds trust and justifies premium pricing
- Use sensory language: textures, colors, weights, smells (for candles/soaps)
- Include care instructions and customization options naturally in the article

TRUST & CONVERSION:
- Shipping timelines and customization lead times build buyer confidence
- Mention "made to order" or "ready to ship" to set expectations
- Photography descriptions: help readers visualize receiving the item
- Mention shop policies: easy returns, eco-friendly packaging, etc.""",
    },
    {
        "platform": "tiktok",
        "display_name": "TikTok Shop",
        "icon": "🎵",
        "content": """TIKTOK SHOP & SHORT-VIDEO SEO GUIDELINES (2024-2025)

TIKTOK SEARCH & DISCOVERY:
- TikTok search is keyword-driven — users search for product reviews, tutorials, and "best of" lists
- Hashtag discovery: mix broad (#skincare), niche (#cleangirlaesthetic), and product-specific tags
- Interest graph algorithm: engagement signals (watch time, saves, shares) define distribution
- TikTok Shop ranks products by: sales velocity, positive reviews, video mention frequency

CONTENT STRUCTURE FOR ARTICLES/LANDING PAGES:
- Hook in first sentence: lead with a trending topic, surprising fact, or bold claim
- Short paragraphs (1–2 sentences) mirroring TikTok's fast-paced consumption style
- Trend-aware language: reference current TikTok trends, viral sounds, challenges
- "TikTok made me buy it" appeal: position products as discovered/loved by the community

KEYWORD STRATEGY:
- Use TikTok search patterns: "TikTok viral X", "TikTok [aesthetic] trend", "#TikTokMadeMeBuyIt products"
- Conversational and question-based: "does X actually work?", "honest review of X"
- Trend lifecycle keywords: capture emerging trends early ("dupes", "micro-trends", "aesthetic")
- UGC language: "everyone is talking about", "trending right now", "you need to try"

SOCIAL PROOF & COMMUNITY:
- Reference viral moments and creator testimonials where possible
- "As seen on TikTok" is a conversion trigger for this audience
- Engage with comments culture: "POV:", "tell me you haven't tried X without telling me"
- Community-first framing: position the brand/product as part of a movement or identity

LINKING STRATEGY:
- Internal links to product pages, "shop the look" pages, and video collections
- Never link CTA phrases externally — keep the traffic in the ecosystem
- TikTok Shop links should be to specific product listings, not general homepages""",
    },
    {
        "platform": "bing",
        "display_name": "Bing / Microsoft",
        "icon": "🪟",
        "content": """BING SEARCH SEO GUIDELINES (2024-2025)

BING RANKING FACTORS (vs Google):
- Social signals matter more on Bing: Facebook shares, LinkedIn links, and Twitter/X engagement directly influence rankings
- Exact-match keywords: Bing's algorithm is more literal — use exact keyword phrases more frequently than on Google
- Domain age and authority: Bing rewards established, older domains more heavily
- Multimedia content: Bing Image Search and Video Search drive significant traffic — include descriptive alt text and transcripts

CONTENT STRUCTURE:
- Clear, straightforward writing — Bing's algorithm favors readability and plain language
- Keyword in first paragraph, H1, and meta title (exact match preferred)
- Longer meta descriptions are OK on Bing (up to 200 characters)
- Bing rewards keyword-rich anchor text in internal links

TECHNICAL SEO FOR BING:
- Submit sitemap to Bing Webmaster Tools
- Schema markup: Bing supports Article, Product, FAQ, and LocalBusiness schemas
- HTTPS required; Bing penalizes insecure pages more harshly than Google
- Clean URL structures with keyword in URL slug

AUDIENCE PROFILE:
- Bing users: slightly older (25–54), higher income, more likely to be on desktop
- Microsoft 365 and Cortana integration: Bing powers AI-assisted search in these products
- Shopping intent is high — Bing Shopping (Microsoft Shopping) drives e-commerce traffic

CONTENT TONE:
- Professional and authoritative tone performs well with the Bing audience
- Data-backed claims with citations from established sources
- Long-form content (2000+ words) with clear section structure
- Avoid excessive casual language or slang""",
    },
    {
        "platform": "youtube",
        "display_name": "YouTube",
        "icon": "▶️",
        "content": """YOUTUBE SEO GUIDELINES (2024-2025)

YOUTUBE RANKING SIGNALS:
- Watch time and audience retention are the #1 signals — algorithm rewards content people finish watching
- Click-through rate (CTR): thumbnail + title combination must drive clicks from search results
- Engagement: likes, comments, shares, and saves signal quality to the algorithm
- Keywords in title, description, and tags directly influence search ranking

CONTENT STRUCTURE FOR COMPANION ARTICLES/LANDING PAGES:
- Mirror the video's structure: intro hook → main content → CTA — same flow works for both
- Include video transcript or key timestamps (improves SEO for written companion content)
- Expand on the video: written articles that go deeper than the video capture text-search traffic
- Include the video embed on the article page — increases session time signals

KEYWORD STRATEGY:
- YouTube search queries are conversational: "how to X", "best X in 2025", "X tutorial for beginners"
- Long-tail keywords have lower competition and higher intent on YouTube
- Include year in titles for "best of" and review content (freshness signal)
- Keyword must appear in: video title, first 100 characters of description, as a tag

CONTENT TONE:
- Conversational and direct — mirror how people speak in videos
- "You" focused: "you'll learn...", "you can...", "your results..."
- Beginner-friendly explanations increase watch time and reduce drop-off
- Include clear chapter markers / timestamp sections in companion written content

LINKING STRATEGY:
- Channel subscription CTAs are internal links — always link to channel or related videos
- "Watch next" recommendations: use internal links to companion articles and video playlist pages
- External links: only to cited sources, tools mentioned, or referenced studies
- Never use generic "click here" — always use descriptive, keyword-rich anchor text for links""",
    },
]


def seed_platform_guidelines(db: Session) -> None:
    """Insert default platform guidelines if they don't exist yet."""
    from app.models.platform_guideline import PlatformGuideline

    for item in DEFAULT_GUIDELINES:
        exists = db.query(PlatformGuideline).filter_by(platform=item["platform"]).first()
        if not exists:
            db.add(PlatformGuideline(**item))
    db.commit()


def get_guideline_content(platform: str, db: Session) -> str:
    """Return the guideline content for the given platform slug, or empty string."""
    from app.models.platform_guideline import PlatformGuideline

    row = db.query(PlatformGuideline).filter_by(platform=platform, is_active=True).first()
    return row.content if row else ""
