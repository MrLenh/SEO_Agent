from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text

from app.database import Base


class Product(Base):
    """Shopify products synced for AI content context and internal linking."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    shop_domain = Column(String(255), index=True, nullable=False)
    platform_id = Column(String(100), index=True)  # Shopify numeric ID

    title = Column(Text, nullable=False)
    handle = Column(String(500))
    description_html = Column(Text)
    description_text = Column(Text)   # plain text for AI context
    vendor = Column(String(500))
    product_type = Column(String(500))
    tags = Column(JSON, default=list)
    status = Column(String(50), default="active")  # active | draft | archived

    # Pricing
    price_min = Column(Float)
    currency = Column(String(10), default="USD")

    # Media
    featured_image_url = Column(Text)
    featured_image_alt = Column(Text)

    # URLs
    platform_url = Column(Text)

    # SEO meta from Shopify
    seo_title = Column(Text)
    seo_description = Column(Text)

    synced_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
