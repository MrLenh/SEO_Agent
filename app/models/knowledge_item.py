from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from app.database import Base


class KnowledgeStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    id = Column(Integer, primary_key=True)
    shop_domain = Column(String(255), nullable=True, index=True)
    source_url = Column(String(2048), nullable=True)
    source_type = Column(String(50), nullable=False)  # blog, product, external, feedback, trend, analysis
    title = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    content_md = Column(Text, nullable=True)
    embedding = Column(JSON, nullable=True)             # list[float] from text-embedding-3-small
    checksum = Column(String(64), nullable=True, index=True)
    status = Column(String(20), default=KnowledgeStatus.PENDING, index=True)
    approved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    extra_meta = Column(JSON, nullable=True)            # {word_count, tags, …}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
