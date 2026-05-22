from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id             = Column(Integer, primary_key=True, index=True)
    email          = Column(String(255), unique=True, nullable=False, index=True)
    name           = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role           = Column(String(20), default="member")   # "admin" | "member"
    is_active      = Column(Boolean, default=True)
    created_at     = Column(DateTime, default=datetime.utcnow)

    store_permissions = relationship(
        "UserStorePermission",
        foreign_keys="UserStorePermission.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserStorePermission(Base):
    """Links a user to a Shopify store with specific capability scopes."""
    __tablename__ = "user_store_permissions"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shop_domain = Column(String(255), nullable=False)
    # scopes: subset of ["read", "write", "publish", "audit"]
    scopes      = Column(JSON, default=list)
    granted_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id], back_populates="store_permissions")

    __table_args__ = (UniqueConstraint("user_id", "shop_domain"),)
