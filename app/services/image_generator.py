"""
Image generator for SEO blog banners.
Saves images to Cloudinary when configured (persistent across deploys),
otherwise falls back to /static/uploads on local disk.
"""
import base64
import logging
import secrets
import time
from pathlib import Path

from app.agents.base import get_client

logger = logging.getLogger(__name__)

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"


class ImageGenerator:
    VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}

    def __init__(self):
        # Image generation (gpt-image-1) is OpenAI-only
        self.client = get_client(force_openai=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        prompt: str,
        size: str = "1536x1024",
        response_format: str = "url",  # kept for call-site compat, ignored
    ) -> dict:
        """Generate a banner image and persist it durably.

        Returns {"url": str, "revised_prompt": str}.
        Uploads to Cloudinary when CLOUDINARY_URL is set; otherwise saves
        to /static/uploads so the URL doesn't expire.
        """
        if size not in self.VALID_SIZES:
            size = "1536x1024"

        response = self.client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size=size,
            quality="medium",
            n=1,
        )
        item = response.data[0]
        revised_prompt = getattr(item, "revised_prompt", prompt)

        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)

        # Get raw bytes
        img_bytes: bytes | None = None
        if b64:
            img_bytes = base64.b64decode(b64)
        elif url:
            try:
                import httpx
                resp = httpx.get(url, timeout=30.0)
                resp.raise_for_status()
                img_bytes = resp.content
            except Exception as exc:
                logger.warning("ImageGenerator: failed to fetch temp URL: %s", exc)

        # Persist: Cloudinary first, then local disk
        final_url: str | None = None
        if img_bytes:
            final_url = self._upload_to_cloudinary(img_bytes) or self._save_png(img_bytes)
        elif url:
            final_url = url  # last resort: temporary URL

        return {"url": final_url, "revised_prompt": revised_prompt}

    # ── Cloudinary upload ─────────────────────────────────────────────────────

    @staticmethod
    def _upload_to_cloudinary(data: bytes) -> str | None:
        """Upload image bytes to Cloudinary and return the secure CDN URL.

        Returns None if Cloudinary is not configured or upload fails,
        allowing the caller to fall back to local disk storage.
        """
        from app.config import settings
        if not settings.CLOUDINARY_URL:
            return None
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL)
            folder = "seo-agent/images"
            name = f"img-{int(time.time())}-{secrets.token_hex(4)}"
            result = cloudinary.uploader.upload(
                data,
                public_id=f"{folder}/{name}",
                resource_type="image",
                format="png",
            )
            return result.get("secure_url")
        except Exception as exc:
            logger.warning("ImageGenerator: Cloudinary upload failed (%s) — falling back to local", exc)
            return None

    # ── Local disk fallback ───────────────────────────────────────────────────

    @staticmethod
    def _save_png(data: bytes) -> str:
        """Write bytes to /static/uploads and return the public URL path."""
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"img-{int(time.time())}-{secrets.token_hex(4)}.png"
        path = UPLOADS_DIR / name
        path.write_bytes(data)
        return f"/static/uploads/{name}"
