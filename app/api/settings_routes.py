"""
Brand profile / global settings endpoints.

GET  /api/v1/settings/brands                 — list all brand profiles
GET  /api/v1/settings/brand?shop_domain=...  — fetch single profile
PUT  /api/v1/settings/brand                  — create or update profile
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.brand_profile import BrandProfile
from app.services.auth_service import get_current_user

settings_router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class BrandProfileBody(BaseModel):
    shop_domain: Optional[str] = None
    brand_name: Optional[str] = None
    brand_style: Optional[str] = None
    brand_description: Optional[str] = None
    tone_of_voice: Optional[str] = None
    output_requirements: Optional[str] = None


def _profile_out(p: BrandProfile) -> dict:
    return {
        "shop_domain": p.shop_domain,
        "brand_name": p.brand_name or "",
        "brand_style": p.brand_style or "",
        "brand_description": p.brand_description or "",
        "tone_of_voice": p.tone_of_voice or "",
        "output_requirements": p.output_requirements or "",
        "updated_at": p.updated_at,
    }


@settings_router.get("/brands")
def list_brand_profiles(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profiles = db.query(BrandProfile).order_by(BrandProfile.brand_name).all()
    return [_profile_out(p) for p in profiles]


@settings_router.get("/brand")
def get_brand_profile(
    shop_domain: Optional[str] = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(BrandProfile).filter_by(shop_domain=shop_domain).first()
    if not profile:
        return {
            "shop_domain": shop_domain,
            "brand_name": "",
            "brand_style": "",
            "brand_description": "",
            "tone_of_voice": "",
            "output_requirements": "",
            "updated_at": None,
        }
    return _profile_out(profile)


@settings_router.put("/brand")
def save_brand_profile(
    body: BrandProfileBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(BrandProfile).filter_by(shop_domain=body.shop_domain).first()
    if not profile:
        profile = BrandProfile(shop_domain=body.shop_domain)
        db.add(profile)

    if body.brand_name is not None:
        profile.brand_name = body.brand_name
    if body.brand_style is not None:
        profile.brand_style = body.brand_style
    if body.brand_description is not None:
        profile.brand_description = body.brand_description
    if body.tone_of_voice is not None:
        profile.tone_of_voice = body.tone_of_voice
    if body.output_requirements is not None:
        profile.output_requirements = body.output_requirements

    db.commit()
    db.refresh(profile)
    return _profile_out(profile)
