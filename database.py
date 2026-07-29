"""
database.py — Database Connection Setup
========================================
This file sets up TWO ways to connect to your database:

  1. SQLAlchemy (engine/session) — used by all existing routes and models.
     Connects via DATABASE_URL pointing to your Supabase PostgreSQL.

  2. Supabase Client — for direct Supabase API calls (Auth, Storage, Realtime, etc.).

Think of it like this:
  - `engine`       = the physical connection to your database
  - `SessionLocal` = a factory that creates database "sessions" (like opening a conversation with the DB)
  - `Base`         = the parent class all your database models (tables) will inherit from
  - `get_db()`     = a helper that gives each web request its own DB session, then cleans up after
  - `supabase`     = the Supabase client for direct REST/Realtime/Auth calls
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
from supabase import create_client, Client

# Load values from your .env file into os.environ
load_dotenv()

# ── Supabase Client ───────────────────────────────────────────────────────────
# For direct Supabase API calls (Auth, Storage, Realtime, etc.)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Database URL ──────────────────────────────────────────────────────────────
# Reads DATABASE_URL from .env. In production, this should point to your
# Supabase PostgreSQL connection string, e.g.:
#   DATABASE_URL=postgresql://user:pass@db.example.com:5432/postgres
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aptura.db").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
elif DATABASE_URL.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)

# SQLite needs an extra argument so it works with FastAPI's threading model.
# PostgreSQL doesn't need this, so we only add it for SQLite.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith(
    "sqlite") else {}

# Create the database engine (the actual connection)
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,  # Set to True during development to see every SQL query printed
)

# SessionLocal is a class. Every time you call SessionLocal(), you get a new
# database session — like opening a new tab to your database.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Base Model ────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """All database table classes inherit from this."""
    pass


# ── Session Dependency ────────────────────────────────────────────────────────
def get_db():
    """
    FastAPI dependency. Every route that needs the database
    calls `db: Session = Depends(get_db)` and this function:
      1. Opens a fresh DB session
      2. Passes it to the route function
      3. Closes it when the request is done (even if there's an error)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
