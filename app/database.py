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
    """Idempotent ALTER TABLE for columns added after initial deployment.

    Each table is migrated in its own transaction so a failure on one table
    never blocks migrations for other tables.
    """
    is_sqlite = "sqlite" in str(engine.url)

    def add_cols(table: str, cols: list[tuple[str, str]]):
        """Add missing columns to *table* in an isolated transaction."""
        try:
            with engine.begin() as conn:
                inspector = inspect(conn)
                if table not in inspector.get_table_names():
                    return
                existing = {c["name"] for c in inspector.get_columns(table)}
                for col, col_type in cols:
                    if col not in existing:
                        if is_sqlite:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                        else:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Migration skipped for %s: %s", table, exc)

    add_cols("blog_posts", [
        ("image_prompt",        "TEXT"),
        ("extra_images",        "TEXT"),
        ("shop_domain",         "VARCHAR(255)"),
        ("scheduled_at",        "TIMESTAMP"),
        ("scheduled_blog_id",   "VARCHAR(100)"),
        ("semantic_keywords",   "TEXT"),
    ])

    add_cols("brand_profiles", [
        ("gsc_site_url",      "VARCHAR(512)"),
        ("gsc_refresh_token", "TEXT"),
        ("writing_notes",     "TEXT"),
        ("shared_user_ids",   "TEXT"),
    ])

    add_cols("article_feedback", [
        ("shop_domain", "VARCHAR(255)"),
    ])

    add_cols("users", [
        ("can_access_kb", "BOOLEAN DEFAULT FALSE"),
    ])

    add_cols("blog_posts", [
        ("shop_domain", "VARCHAR(255)"),
    ])

    add_cols("blog_channels", [
        ("shop_domain", "VARCHAR(255)"),
    ])

    add_cols("products", [
        ("notes", "TEXT"),
    ])

    add_cols("topic_clusters", [
        ("plan_json", "TEXT"),
    ])

    add_cols("keywords", [
        ("topic_cluster_id", "INTEGER"),
    ])

    try:
        with engine.begin() as conn:
            _backfill_shop_domain(conn)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("shop_domain backfill skipped: %s", exc)


def _backfill_shop_domain(conn):
    """Derive shop_domain from platform_url for rows added before scoping."""
    from urllib.parse import urlparse

    tables = set(inspect(conn).get_table_names())  # type: ignore[arg-type]
    if "blog_posts" not in tables or "blog_channels" not in tables:
        return

    rows = conn.execute(text(
        "SELECT id, platform_url FROM blog_posts "
        "WHERE shop_domain IS NULL AND platform_url IS NOT NULL"
    )).fetchall()
    for row in rows:
        host = urlparse(row[1]).hostname
        if host:
            conn.execute(
                text("UPDATE blog_posts SET shop_domain = :h WHERE id = :i"),
                {"h": host, "i": row[0]},
            )

    conn.execute(text(
        "UPDATE blog_channels SET shop_domain = ("
        "  SELECT shop_domain FROM blog_posts "
        "  WHERE blog_posts.channel_id = blog_channels.id "
        "    AND blog_posts.shop_domain IS NOT NULL LIMIT 1"
        ") WHERE shop_domain IS NULL"
    ))
