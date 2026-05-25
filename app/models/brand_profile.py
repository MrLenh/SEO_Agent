from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, UniqueConstraint

from app.database import Base


class BrandProfile(Base):
    __tablename__ = "brand_profiles"

    id              = Column(Integer, primary_key=True, index=True)
    shop_domain     = Column(String(255), nullable=True, index=True)  # NULL = global default
    brand_name      = Column(String(255), nullable=True)
    brand_style     = Column(Text, nullable=True)
    brand_description = Column(Text, nullable=True)
    tone_of_voice   = Column(Text, nullable=True)
    output_requirements = Column(Text, nullable=True)

    # Strict rules the AI must NEVER violate — highest enforcement priority
    writing_notes   = Column(Text, nullable=True)

    # User IDs explicitly granted access to this brand profile
    shared_user_ids = Column(JSON, default=list)

    # Google Search Console — per-brand OAuth2
    gsc_site_url      = Column(String(512), nullable=True)
    gsc_refresh_token = Column(Text, nullable=True)

    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("shop_domain"),)
