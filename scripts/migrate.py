"""
Idempotent Aptura schema migration helper.

Run before deploying a new app version:
    python scripts/migrate.py

This intentionally uses the same SQLAlchemy engine configuration as the app,
but it does not import main.py, so it avoids starting routers or app startup
side effects while still applying required additive columns safely.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from sqlalchemy import inspect, text

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from database import engine  # noqa: E402


MIGRATION_ID = "20260919_role_workspaces"

COLUMN_SPECS = {
    "users": {
        "workspace": "VARCHAR(30) DEFAULT 'APPLICANT' NOT NULL",
        "primary_role": "VARCHAR(30)",
        "active_workspace": "VARCHAR(30)",
        "available_roles": "JSON",
        "workspace_preferences": "JSON",
        "onboarding_completed": "BOOLEAN DEFAULT FALSE NOT NULL",
    },
    "jobs": {
        "location": "VARCHAR(255)",
        "employment_type": "VARCHAR(50)",
        "salary_range": "VARCHAR(120)",
        "required_skills": "JSON",
        "preferred_skills": "JSON",
        "experience_years": "INTEGER",
        "education": "VARCHAR(255)",
        "application_url": "VARCHAR(1000)",
        "application_email": "VARCHAR(255)",
        "closing_date": "TIMESTAMP",
        "status": "VARCHAR(30) DEFAULT 'OPEN' NOT NULL",
    },
    "screenings": {
        "total_files": "INTEGER DEFAULT 0",
        "processed_candidates": "INTEGER DEFAULT 0",
        "status": "VARCHAR(50) DEFAULT 'QUEUED' NOT NULL",
        "error_message": "TEXT",
    },
    "candidates": {
        "status": "VARCHAR(50) DEFAULT 'QUEUED' NOT NULL",
        "error_message": "TEXT",
        "file_content_b64": "TEXT",
    },
}


def ensure_migration_table() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id VARCHAR(120) PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL
            )
        """))


def migration_recorded() -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM schema_migrations WHERE id = :id"),
            {"id": MIGRATION_ID},
        ).first()
    return row is not None


def record_migration() -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO schema_migrations (id, applied_at) VALUES (:id, :applied_at)"),
            {"id": MIGRATION_ID, "applied_at": datetime.utcnow()},
        )


def add_missing_columns() -> list[str]:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    applied: list[str] = []

    with engine.begin() as conn:
        for table_name, columns in COLUMN_SPECS.items():
            if table_name not in tables:
                continue
            existing = {column["name"] for column in insp.get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
                applied.append(f"{table_name}.{column_name}")

    return applied


def main() -> None:
    ensure_migration_table()
    applied = add_missing_columns()
    if not migration_recorded():
        record_migration()

    if applied:
        print("Applied columns:")
        for name in applied:
            print(f"- {name}")
    else:
        print("Schema already up to date.")


if __name__ == "__main__":
    main()
