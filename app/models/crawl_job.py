from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from app.database import Base


class CrawlStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    id = Column(Integer, primary_key=True)
    shop_domain = Column(String(255), nullable=True)
    url = Column(String(2048), nullable=False)
    job_type = Column(String(50), default="single")     # single | sitemap
    status = Column(String(20), default=CrawlStatus.QUEUED, index=True)
    items_found = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
