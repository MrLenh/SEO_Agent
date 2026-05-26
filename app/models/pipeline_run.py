from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, JSON, String, Text
from app.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id           = Column(Integer, primary_key=True, index=True)
    shop_domain  = Column(String(255), nullable=True, index=True)
    keyword      = Column(String(500), nullable=False)
    title        = Column(String(500), nullable=True)
    status       = Column(String(50), default="running", index=True)  # running/done/failed
    post_id      = Column(Integer, nullable=True)
    steps        = Column(JSON, default=list)
    error        = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
