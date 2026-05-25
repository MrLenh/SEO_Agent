from typing import Optional
from pydantic import BaseModel
from app.models.blog_post import Platform


class KeywordResearchRequest(BaseModel):
    keyword: str
    country: str = "us"
    language: str = "en"


class KeywordResearchOut(BaseModel):
    keyword: str
    people_also_ask: list[str]
    related_searches: list[str]
    top_results: list[dict]


class TopicPlanRequest(BaseModel):
    seed_keyword: str
    country: str = "us"
    language: str = "en"


class ArticleBrief(BaseModel):
    title: str
    slug: str
    focus_keyword: str
    outline: list[str]
    target_question: Optional[str] = None


class TopicClusterOut(BaseModel):
    id: int
    cluster_name: str
    seed_keyword: str
    pillar: ArticleBrief
    supporting_articles: list[ArticleBrief]


class GenerateArticleRequest(BaseModel):
    title: str
    focus_keyword: str
    outline: list[str]
    paa_questions: list[str] = []
    cluster_id: Optional[int] = None
    language: str = "en"
    market: str = "us"
    tone: str = "professional"
    word_count: int = 1500
    notes: Optional[str] = None
    article_type: Optional[str] = None
    platform: Platform = Platform.SHOPIFY
    target_platform: str = "google"   # SEO platform: google, amazon, etsy, tiktok, bing, youtube
    blog_channel_id: Optional[int] = None
    shop_domain: Optional[str] = None
    auto_publish: bool = False


class TitleSuggestionRequest(BaseModel):
    focus_keyword: str
    notes: Optional[str] = None
    language: str = "en"
    market: str = "us"
    article_type: Optional[str] = None
    count: int = 5


class GeneratedArticleOut(BaseModel):
    id: int
    title: str
    slug: str
    seo_title: str
    seo_description: str
    tags: list[str]
    image_prompt: str
    platform_url: Optional[str]
    status: str
    source: str
