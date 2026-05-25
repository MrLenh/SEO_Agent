from datetime import datetime

from sqlalchemy import Column, DateTime, String, Integer, Text

from app.database import Base


class Product(Base):
    """Products tracked for SEO ranking.

    Only identification + tracking metadata is stored here.
    Full product data (description, price, images) is fetched live from
    Shopify at article-generation time so it is always up-to-date.
    """
    __tablename__ = "products"

    id          = Column(Integer, primary_key=True, index=True)
    shop_domain = Column(String(255), index=True, nullable=False)
    platform_id = Column(String(100), index=True)   # Shopify numeric product ID
    handle      = Column(String(500))               # /products/{handle}
    title       = Column(Text)                      # cached display name
    product_type = Column(String(500))
    platform_url = Column(Text)

    # Tracking state
    status     = Column(String(20), default="tracked")  # tracked | paused
    notes      = Column(Text)                           # admin notes for this product

    tracked_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
