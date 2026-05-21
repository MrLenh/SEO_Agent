from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.blog_post import Platform, PostStatus


class BlogChannelOut(BaseModel):
    id: int
    platform: Platform
    platform_id: str
    title: Optional[str]
    handle: Optional[str]
    synced_at: datetime

    class Config:
        from_attributes = True


class BlogPostOut(BaseModel):
    id: int
    platform: Platform
    platform_id: Optional[str]
    platform_url: Optional[str]
    channel_id: Optional[int]
    title: str
    slug: Optional[str]
    excerpt_html: Optional[str]
    author: Optional[str]
    tags: list[str]
    featured_image_url: Optional[str]
    seo_title: Optional[str]
    seo_description: Optional[str]
    focus_keyword: Optional[str]
    status: PostStatus
    source: str
    published_at: Optional[datetime]
    synced_at: datetime

    class Config:
        from_attributes = True


class SyncResult(BaseModel):
    platform: str
    shop: str
    blogs_found: int
    articles_synced: int
    articles_skipped: int
    articles_updated: int
    duration_seconds: float
    errors: list[str]
