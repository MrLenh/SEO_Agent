import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth_routes import auth_router
from app.api.content_routes import generate_router, research_router, topics_router
from app.api.init_routes import blog_router, router as init_router
from app.api.publish_routes import publish_router
from app.database import create_tables
from app.models import shopify_store as _  # ensure table is registered

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        create_tables()
        logger.info("Database tables ready")
    except Exception as e:
        logger.warning(f"DB init warning (non-fatal): {e}")
    yield


app = FastAPI(
    title="SEO Agent API",
    version="0.1.0",
    description="AI-powered SEO content agent for Shopify / WooCommerce",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(init_router)
app.include_router(blog_router)
app.include_router(research_router)
app.include_router(topics_router)
app.include_router(generate_router)
app.include_router(publish_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse("app/static/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug/config", include_in_schema=False)
def debug_config():
    """Shows which env vars are configured (no secret values)."""
    from app.config import settings
    def _set(v): return bool(v)
    return {
        "app_url":              settings.APP_URL or "(not set)",
        "shopify_shop_domain":  settings.SHOPIFY_SHOP_DOMAIN or "(not set)",
        "shopify_api_key":      _set(settings.SHOPIFY_API_KEY),
        "shopify_api_secret":   _set(settings.SHOPIFY_API_SECRET),
        "shopify_access_token": _set(settings.SHOPIFY_ACCESS_TOKEN),
        "shopify_api_version":  settings.SHOPIFY_API_VERSION,
        "openai_api_key":       _set(settings.OPENAI_API_KEY),
        "serper_api_key":       _set(settings.SERPER_API_KEY),
        "database_url":         settings.DATABASE_URL[:30] + "..." if settings.DATABASE_URL else "(not set)",
    }
