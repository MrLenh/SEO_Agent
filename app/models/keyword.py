import enum
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class KeywordStatus(str, enum.Enum):
    TRACKED = "tracked"
    PAUSED = "paused"


class TopicClusterStatus(str, enum.Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"


class Keyword(Base):
    """Tracked keywords with ranking history."""
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String(500), nullable=False, index=True)
    search_engine = Column(String(50), default="google")
    language = Column(String(10), default="en")
    country = Column(String(10), default="US")

    # Metrics (refreshed daily)
    volume = Column(Integer)
    difficulty = Column(Float)
    cpc = Column(Float)
    current_rank = Column(Integer)
    prev_rank = Column(Integer)
    best_rank = Column(Integer)

    # Related article
    article_id = Column(Integer, ForeignKey("blog_posts.id"), nullable=True)

    # PAA questions from SERP (list of str)
    people_also_ask = Column(JSON, default=list)

    status = Column(Enum(KeywordStatus), default=KeywordStatus.TRACKED)
    last_checked = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    article = relationship("BlogPost", foreign_keys=[article_id])


class TopicCluster(Base):
    """Topic cluster = pillar page + supporting articles."""
    __tablename__ = "topic_clusters"

    id = Column(Integer, primary_key=True, index=True)
    seed_keyword = Column(String(500), nullable=False)
    cluster_name = Column(String(500))
    description = Column(Text)

    # Pillar article
    pillar_article_id = Column(Integer, ForeignKey("blog_posts.id"), nullable=True)

    # Subtopic articles planned / generated (list of article IDs)
    subtopic_article_ids = Column(JSON, default=list)

    # Questions to cover (from PAA / keyword research)
    questions = Column(JSON, default=list)

    status = Column(Enum(TopicClusterStatus), default=TopicClusterStatus.PLANNED)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pillar_article = relationship("BlogPost", foreign_keys=[pillar_article_id])


class AuditSnapshot(Base):
    """Daily audit metrics per article from Google Search Console."""
    __tablename__ = "audit_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("blog_posts.id"), nullable=False)

    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0.0)
    avg_position = Column(Float)

    # Top queries driving traffic (list of {query, clicks, position})
    top_queries = Column(JSON, default=list)

    # AI suggestions for optimization
    suggestions = Column(JSON, default=list)

    created_at = Column(DateTime, default=datetime.utcnow)

    article = relationship("BlogPost", foreign_keys=[article_id])
