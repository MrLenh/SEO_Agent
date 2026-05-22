from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
    _migrate_columns()


def _migrate_columns():
    """Idempotent ALTER TABLE for columns added after initial deployment."""
    is_sqlite = "sqlite" in str(engine.url)

    def add_cols(table: str, cols: list[tuple[str, str]]):
        if table not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col, col_type in cols:
            if col not in existing:
                if is_sqlite:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))

    with engine.begin() as conn:
        inspector = inspect(engine)

        add_cols("blog_posts", [
            ("image_prompt", "TEXT"),
        ])

        add_cols("brand_profiles", [
            ("gsc_site_url",      "VARCHAR(512)"),
            ("gsc_refresh_token", "TEXT"),
        ])

        add_cols("article_feedback", [
            ("shop_domain", "VARCHAR(255)"),
        ])
