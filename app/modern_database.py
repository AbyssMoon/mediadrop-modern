from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import Request
from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import Settings


class ModernBase(DeclarativeBase):
    pass


class ModernSetting(ModernBase):
    __tablename__ = "modern_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    parsed = urlparse(database_url)
    if parsed.path in {"", "/:memory:"} or database_url.endswith(":memory:"):
        return
    # sqlite:////absolute/path.db -> /absolute/path.db
    # sqlite:///relative/path.db -> relative/path.db
    path_text = unquote(parsed.path)
    if database_url.startswith("sqlite:////"):
        db_path = Path(path_text)
    else:
        db_path = Path(path_text.lstrip("/"))
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)


def build_modern_session_factory(settings: Settings):
    _ensure_sqlite_parent(settings.modern_database_url)
    kwargs = {"pool_pre_ping": True}
    if settings.modern_database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(settings.modern_database_url, **kwargs)
    ModernBase.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def modern_db_dependency(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.modern_session_factory
    with session_factory() as session:
        yield session


def load_modern_settings(db: Session) -> dict[str, str]:
    rows = db.scalars(select(ModernSetting)).all()
    return {row.key: row.value for row in rows}


def get_modern_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(ModernSetting, key)
    return default if row is None else row.value


def set_modern_setting(db: Session, key: str, value: str | int | bool | None) -> None:
    row = db.get(ModernSetting, key)
    if row is None:
        row = ModernSetting(key=key, value="")
        db.add(row)
    if isinstance(value, bool):
        row.value = "true" if value else "false"
    elif value is None:
        row.value = ""
    else:
        row.value = str(value)
