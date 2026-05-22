from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ArticleFeedback(Base):
    __tablename__ = "article_feedback"

    id               = Column(Integer, primary_key=True, index=True)
    post_id          = Column(Integer, ForeignKey("blog_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id          = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    shop_domain      = Column(String(255), nullable=True, index=True)
    rating           = Column(Integer, nullable=False)   # 1–5
    feedback_text    = Column(Text, nullable=True)       # overall comments
    improvement_notes = Column(Text, nullable=True)      # what to do differently next time
    created_at       = Column(DateTime, default=datetime.utcnow)
