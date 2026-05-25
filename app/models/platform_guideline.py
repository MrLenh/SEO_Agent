from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from app.database import Base


class PlatformGuideline(Base):
    __tablename__ = "platform_guidelines"

    id           = Column(Integer, primary_key=True, index=True)
    platform     = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    icon         = Column(String(10), default="🔍")
    content      = Column(Text, nullable=False)   # guidelines injected into AI prompt
    is_active    = Column(Boolean, default=True)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
