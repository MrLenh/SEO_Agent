import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.api.audit_routes import audit_router
from app.api.auth_routes import auth_router
from app.api.content_routes import generate_router, research_router, topics_router
from app.api.init_routes import blog_router, router as init_router
from app.api.publish_routes import publish_router
from app.api.user_routes import user_router
from app.database import create_tables, get_db
from app.models import shopify_store as _   # ensure table is registered
from app.models import user as _u           # ensure user tables are registered

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

app.include_router(user_router)
app.include_router(audit_router)
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


@app.get("/debug/shopify", include_in_schema=False)
async def debug_shopify(db: Session = Depends(get_db)):
    """Test Shopify token scopes and blogs GraphQL query."""
    import httpx
    from app.api.auth_routes import get_store_token
    from app.config import settings

    shop = settings.SHOPIFY_SHOP_DOMAIN
    ver  = settings.SHOPIFY_API_VERSION

    if not shop:
        return {"error": "SHOPIFY_SHOP_DOMAIN not set"}

    token = get_store_token(shop, db)
    token_source = "env_var"
    from app.models.shopify_store import ShopifyStore
    db_store = db.query(ShopifyStore).filter_by(shop_domain=shop).first()
    if db_store and db_store.access_token:
        token_source = "oauth_db"

    if not token:
        return {"error": "No access token found (env var or OAuth DB)"}

    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }

    endpoint = f"https://{shop}/admin/api/{ver}/graphql.json"

    async with httpx.AsyncClient(timeout=15.0) as client:
        scopes_resp = await client.get(
            f"https://{shop}/admin/oauth/access_scopes.json",
            headers=headers,
        )

        blogs_resp = await client.post(
            endpoint, headers=headers,
            json={"query": "{ blogs(first: 5) { nodes { id title handle } } }"},
        )

        # Test articles query with every possible field variant
        blogs_data = blogs_resp.json()
        first_blog_id = None
        if blogs_data.get("data", {}).get("blogs", {}).get("nodes"):
            first_blog_id = blogs_data["data"]["blogs"]["nodes"][0]["id"]

        articles_resp = None
        if first_blog_id:
            articles_resp = await client.post(
                endpoint, headers=headers,
                json={"query": f"""{{
                  blog(id: "{first_blog_id}") {{
                    articles(first: 3) {{
                      nodes {{
                        id title handle
                        body
                        author {{ name }}
                        tags
                        image {{ url altText }}
                        isPublished
                        publishedAt updatedAt
                      }}
                    }}
                  }}
                }}"""},
            )

    return {
        "shop": shop,
        "api_version": ver,
        "token_source": token_source,
        "token_scopes": scopes_resp.json() if scopes_resp.status_code == 200 else {
            "http_status": scopes_resp.status_code,
            "body": scopes_resp.text,
        },
        "blogs_gql": {
            "http_status": blogs_resp.status_code,
            "body": blogs_resp.json() if blogs_resp.status_code == 200 else blogs_resp.text,
        },
        "articles_gql": {
            "first_blog_id": first_blog_id,
            "http_status": articles_resp.status_code if articles_resp else None,
            "body": articles_resp.json() if articles_resp and articles_resp.status_code == 200 else (articles_resp.text if articles_resp else None),
        },
    }


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
