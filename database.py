"""
Database connection setup.

The app uses regular synchronous SQLAlchemy sessions. In production,
DATABASE_URL should point to the Supabase PostgreSQL database.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()


def normalize_database_url(url: str) -> str:
    """Force PostgreSQL URLs onto SQLAlchemy's sync psycopg2 driver."""
    url = (url or "sqlite:///./aptura.db").strip()
    for prefix in (
        "postgres://",
        "postgresql://",
        "postgresql+asyncpg://",
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
    ):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql+psycopg2://", 1)
    return url


DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///./aptura.db"))

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
