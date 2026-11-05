from collections.abc import Generator
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    """Create tables and add the small set of POC columns for existing SQLite files."""
    Base.metadata.create_all(engine)
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    migrations = {
        "social_accounts": {
            "token_ref": "VARCHAR(255)",
            "connection_status": "VARCHAR(30) DEFAULT 'connected'",
        },
        "collection_jobs": {"error_message": "TEXT"},
    }
    with engine.begin() as connection:
        for table, columns in migrations.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

