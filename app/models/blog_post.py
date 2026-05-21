import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Platform(str, enum.Enum):
    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"


class PostStatus(str, enum.Enum):
    PUBLISHED = "published"
    DRAFT = "draft"


class BlogChannel(Base):
    """Blog channels / categories synced from platform."""
    __tablename__ = "blog_channels"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(Enum(Platform), nullable=False)
    platform_id = Column(String(100), nullable=False)
    title = Column(String(500))
    handle = Column(String(500))
    commentable = Column(String(50))
    feedburner = Column(String(500))
    synced_at = Column(DateTime, default=datetime.utcnow)

    posts = relationship("BlogPost", back_populates="channel")


class BlogPost(Base):
    """Blog posts — both synced from platform and AI-generated."""
    __tablename__ = "blog_posts"

    id = Column(Integer, primary_key=True, index=True)

    # Platform origin
    platform = Column(Enum(Platform), nullable=False)
    platform_id = Column(String(100), index=True)        # Shopify article ID
    platform_url = Column(Text)
    channel_id = Column(Integer, ForeignKey("blog_channels.id"))

    # Content
    title = Column(Text, nullable=False)
    slug = Column(String(1000), index=True)
    content_html = Column(Text)
    excerpt_html = Column(Text)
    author = Column(String(200))
    tags = Column(JSON, default=list)                    # list[str]
    featured_image_url = Column(Text)
    featured_image_alt = Column(Text)

    # SEO meta
    seo_title = Column(Text)
    seo_description = Column(Text)
    focus_keyword = Column(String(500))

    # Internal links inserted (list of target post IDs)
    internal_links = Column(JSON, default=list)

    # State
    status = Column(Enum(PostStatus), default=PostStatus.PUBLISHED)
    source = Column(String(50), default="synced")        # 'synced' | 'generated'
    published_at = Column(DateTime)
    synced_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    channel = relationship("BlogChannel", back_populates="posts")
