from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from app.database import Base


class BrandProfile(Base):
    __tablename__ = "brand_profiles"

    id              = Column(Integer, primary_key=True, index=True)
    shop_domain     = Column(String(255), nullable=True, index=True)  # NULL = global default
    brand_name      = Column(String(255), nullable=True)
    brand_style     = Column(Text, nullable=True)       # e.g. "minimalist, premium, youthful"
    brand_description = Column(Text, nullable=True)     # who the brand is, products, audience
    tone_of_voice   = Column(Text, nullable=True)       # writing style & voice instructions
    output_requirements = Column(Text, nullable=True)   # markdown: structure/format rules

    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("shop_domain"),)
