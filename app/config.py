from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./seo_agent.db"
    SECRET_KEY: str = "change-me-in-production"

    # App
    APP_URL: str = ""                # e.g. https://myapp.up.railway.app (no trailing slash)

    # Shopify
    SHOPIFY_SHOP_DOMAIN: str = ""
    SHOPIFY_ACCESS_TOKEN: str = ""   # fallback if OAuth not used
    SHOPIFY_API_KEY: str = ""        # Partner App Client ID
    SHOPIFY_API_SECRET: str = ""     # Partner App Client Secret
    SHOPIFY_API_VERSION: str = "2025-07"

    # WooCommerce
    WC_STORE_URL: str = ""
    WC_CONSUMER_KEY: str = ""
    WC_CONSUMER_SECRET: str = ""

    # AI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # SEO
    SERPER_API_KEY: str = ""

    # Google Search Console
    GOOGLE_SERVICE_ACCOUNT_JSON: str = ""  # full JSON content of service account key
    GSC_SITE_URL: str = ""                 # e.g. https://gingerglow.myshopify.com/

    # Cloudinary
    CLOUDINARY_URL: str = ""

    # Superadmin bootstrap (auto-created on first startup if set)
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""
    ADMIN_NAME: str = "Super Admin"

    class Config:
        env_file = ".env"


settings = Settings()
