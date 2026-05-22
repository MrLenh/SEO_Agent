import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.content_routes import generate_router, research_router, topics_router
from app.api.init_routes import blog_router, router as init_router
from app.api.publish_routes import publish_router
from app.database import create_tables

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

app.include_router(init_router)
app.include_router(blog_router)
app.include_router(research_router)
app.include_router(topics_router)
app.include_router(generate_router)
app.include_router(publish_router)


@app.get("/health")
def health():
    return {"status": "ok"}
