"""Read-only compatibility check for a MediaDrop database.

Usage:
    DATABASE_URL=mysql+pymysql://... python scripts/check_legacy_db.py
"""
from __future__ import annotations

from sqlalchemy import inspect

from app.config import get_settings
from app.database import build_engine
from app.models import Base


def main() -> int:
    settings = get_settings()
    engine = build_engine(settings)
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    missing_tables: list[str] = []
    missing_columns: list[str] = []

    for name, table in Base.metadata.tables.items():
        if name not in actual_tables:
            missing_tables.append(name)
            continue
        actual_columns = {c["name"] for c in inspector.get_columns(name)}
        for column in table.columns:
            if column.name not in actual_columns:
                missing_columns.append(f"{name}.{column.name}")

    print(f"Database: {settings.database_url.split('@')[-1]}")
    if missing_tables:
        print("Missing tables:")
        for name in missing_tables:
            print(f"  - {name}")
    if missing_columns:
        print("Missing columns:")
        for name in missing_columns:
            print(f"  - {name}")
    if missing_tables or missing_columns:
        print("\nCompatibility check FAILED. No database changes were made.")
        return 1
    print("Required legacy tables/columns are present. No database changes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
