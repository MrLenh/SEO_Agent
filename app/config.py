from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./seo_agent.db"
    SECRET_KEY: str = "change-me-in-production"

    # Shopify
    SHOPIFY_SHOP_DOMAIN: str = ""
    SHOPIFY_ACCESS_TOKEN: str = ""
    SHOPIFY_API_VERSION: str = "2024-01"

    # WooCommerce
    WC_STORE_URL: str = ""
    WC_CONSUMER_KEY: str = ""
    WC_CONSUMER_SECRET: str = ""

    # AI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # SEO
    SERPER_API_KEY: str = ""

    # Cloudinary
    CLOUDINARY_URL: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
