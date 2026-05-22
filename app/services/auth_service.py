from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

_ALGORITHM = "HS256"
_EXPIRE_HOURS = 24 * 7   # 7-day tokens

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, email: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])


# ── FastAPI dependency helpers ────────────────────────────────────────────────

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db


def _extract_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    return authorization.split(" ", 1)[1]


def get_current_user(
    token: str = Depends(_extract_token),
    db: Session = Depends(get_db),
):
    from app.models.user import User
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired token")
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(401, "User not found")
    return user


def require_admin(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def check_store_scope(user, shop_domain: str, scope: str, db: Session):
    """Raise 403 if user cannot perform `scope` on `shop_domain`."""
    if user.role == "admin":
        return
    from app.models.user import UserStorePermission
    perm = db.query(UserStorePermission).filter_by(
        user_id=user.id, shop_domain=shop_domain
    ).first()
    if not perm:
        raise HTTPException(403, f"No access to store {shop_domain}")
    if scope not in (perm.scopes or []):
        raise HTTPException(403, f"Scope '{scope}' not granted for {shop_domain}")


def get_user_shops(user, db: Session) -> list[str]:
    """Return list of shop domains accessible to this user."""
    if user.role == "admin":
        from app.models.shopify_store import ShopifyStore
        return [s.shop_domain for s in db.query(ShopifyStore).all()]
    from app.models.user import UserStorePermission
    return [p.shop_domain for p in
            db.query(UserStorePermission).filter_by(user_id=user.id).all()]
