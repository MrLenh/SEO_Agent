"""
Image generator for SEO blog banners.
Saves the bytes locally under /static/uploads so the URL is durable
(gpt-image-1 returns b64; DALL·E 3 URLs expire after ~1 hour).
"""
import base64
import secrets
import time
from pathlib import Path

from app.agents.base import get_client


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
        """Generate a banner. Returns {"url": str, "revised_prompt": str}.

        Saves the image as a PNG under app/static/uploads so the URL is
        durable and Shopify can fetch it when publishing.
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

        # gpt-image-1 always returns b64_json. DALL·E 3 may return either.
        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)

        if b64:
            url = self._save_png(base64.b64decode(b64))
        elif url:
            # Fetch the temporary URL and persist locally so it doesn't expire
            try:
                import httpx
                resp = httpx.get(url, timeout=30.0)
                resp.raise_for_status()
                url = self._save_png(resp.content)
            except Exception:
                pass  # fall back to the temporary URL

        return {"url": url, "revised_prompt": revised_prompt}

    @staticmethod
    def _save_png(data: bytes) -> str:
        """Write bytes to /static/uploads and return the public URL path."""
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"img-{int(time.time())}-{secrets.token_hex(4)}.png"
        path = UPLOADS_DIR / name
        path.write_bytes(data)
        return f"/static/uploads/{name}"
