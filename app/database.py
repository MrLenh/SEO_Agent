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
    with engine.begin() as conn:
        inspector = inspect(engine)
        if "blog_posts" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("blog_posts")}
        pending = [
            ("image_prompt", "TEXT"),
        ]
        for col, col_type in pending:
            if col not in existing:
                if is_sqlite:
                    conn.execute(text(f"ALTER TABLE blog_posts ADD COLUMN {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS {col} {col_type}"))
