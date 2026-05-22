"""
User management + auth endpoints.

Bootstrap: first POST /api/v1/users/register creates an admin (no auth required).
Subsequent registrations require admin token.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserStorePermission
from app.services.auth_service import (
    check_store_scope,
    create_access_token,
    get_current_user,
    get_user_shops,
    hash_password,
    require_admin,
    verify_password,
)

user_router = APIRouter(prefix="/api/v1/users", tags=["users"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: str
    name: str
    password: str

class LoginBody(BaseModel):
    email: str
    password: str

class CreateUserBody(BaseModel):
    email: str
    name: str
    password: str
    role: str = "member"

class UpdateUserBody(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

class GrantPermBody(BaseModel):
    shop_domain: str
    scopes: List[str] = ["read", "audit"]   # read | write | publish | audit


# ── Auth ──────────────────────────────────────────────────────────────────────

@user_router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    """
    First call (no users in DB) → creates admin.
    Subsequent calls require an admin Bearer token.
    """
    existing_count = db.query(User).count()
    is_bootstrap = existing_count == 0

    if not is_bootstrap:
        # Require admin auth for subsequent registrations
        from fastapi import Header
        raise HTTPException(403, "Use POST /api/v1/users/ (admin) to create more users")

    if db.query(User).filter_by(email=body.email).first():
        raise HTTPException(400, "Email already registered")

    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(body.password),
        role="admin",   # first user is always admin
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.email, user.role)
    return {"token": token, "user": _user_out(user)}


@user_router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled")
    token = create_access_token(user.id, user.email, user.role)
    return {"token": token, "user": _user_out(user, db)}


@user_router.get("/me")
def get_me(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _user_out(user, db)


# ── User CRUD (admin) ─────────────────────────────────────────────────────────

@user_router.get("/")
def list_users(admin=Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_user_out(u, db) for u in users]


@user_router.post("/")
def create_user(body: CreateUserBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter_by(email=body.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@user_router.put("/{user_id}")
def update_user(user_id: int, body: UpdateUserBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.hashed_password = hash_password(body.password)
    db.commit()
    return _user_out(user, db)


@user_router.delete("/{user_id}")
def delete_user(user_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    db.delete(user)
    db.commit()
    return {"deleted": user_id}


# ── Store Permissions ─────────────────────────────────────────────────────────

@user_router.get("/{user_id}/permissions")
def get_permissions(user_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    perms = db.query(UserStorePermission).filter_by(user_id=user_id).all()
    return [_perm_out(p) for p in perms]


@user_router.post("/{user_id}/permissions")
def grant_permission(
    user_id: int,
    body: GrantPermBody,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    valid_scopes = {"read", "write", "publish", "audit"}
    bad = [s for s in body.scopes if s not in valid_scopes]
    if bad:
        raise HTTPException(422, f"Invalid scopes: {bad}. Valid: {sorted(valid_scopes)}")

    perm = db.query(UserStorePermission).filter_by(
        user_id=user_id, shop_domain=body.shop_domain
    ).first()
    if perm:
        perm.scopes = body.scopes
    else:
        perm = UserStorePermission(
            user_id=user_id,
            shop_domain=body.shop_domain,
            scopes=body.scopes,
            granted_by=admin.id,
        )
        db.add(perm)
    db.commit()
    return _perm_out(perm)


@user_router.delete("/{user_id}/permissions/{shop_domain:path}")
def revoke_permission(
    user_id: int,
    shop_domain: str,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    perm = db.query(UserStorePermission).filter_by(
        user_id=user_id, shop_domain=shop_domain
    ).first()
    if not perm:
        raise HTTPException(404, "Permission not found")
    db.delete(perm)
    db.commit()
    return {"revoked": shop_domain}


# ── Store list for current user ───────────────────────────────────────────────

@user_router.get("/stores/accessible")
def my_stores(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns stores + scopes available to the current user."""
    if user.role == "admin":
        from app.models.shopify_store import ShopifyStore
        stores = db.query(ShopifyStore).all()
        return [
            {"shop_domain": s.shop_domain, "scopes": ["read","write","publish","audit"], "installed_at": s.installed_at}
            for s in stores
        ]
    perms = db.query(UserStorePermission).filter_by(user_id=user.id).all()
    return [_perm_out(p) for p in perms]


# ── Keyword suggestions for a store ──────────────────────────────────────────

@user_router.get("/stores/{shop_domain:path}/suggestions")
def store_keyword_suggestions(
    shop_domain: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """GPT-4o + Serper keyword suggestions scoped to the store's content."""
    check_store_scope(user, shop_domain, "audit", db)

    from app.models.blog_post import BlogPost, Platform
    from app.services.keyword_suggester import generate_suggestions

    posts = db.query(BlogPost).filter(
        BlogPost.platform == Platform.SHOPIFY,
    ).all()
    # Filter by shop domain via channel
    # (BlogPost doesn't have shop_domain directly; use platform_url prefix as proxy)
    store_posts = [
        p for p in posts
        if p.platform_url and shop_domain in p.platform_url
    ] or posts  # fallback: all posts if no URL match yet

    try:
        return generate_suggestions(store_posts, shop_domain)
    except Exception as e:
        raise HTTPException(502, f"Suggestion generation failed: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_out(user: User, db: Session = None) -> dict:
    perms = []
    if db:
        raw = db.query(UserStorePermission).filter_by(user_id=user.id).all()
        perms = [_perm_out(p) for p in raw]
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "store_permissions": perms,
    }

def _perm_out(perm: UserStorePermission) -> dict:
    return {
        "shop_domain": perm.shop_domain,
        "scopes": perm.scopes or [],
        "created_at": perm.created_at,
    }
