from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "MediaDrop"
    environment: str = "production"
    secret_key: str = Field(default="change-me", min_length=8)
    database_url: str = "sqlite:///./mediadrop.db"
    # Modern-only settings live outside the legacy MediaDrop schema.
    modern_database_url: str = "sqlite:///./mediadrop-modern.db"

    # Writable roots for files created by the modern application.
    media_root: Path = Path("/data/media")
    image_root: Path = Path("/data/images")

    # Optional read-only roots containing files copied from legacy MediaDrop.
    # When a file is not found in the modern root, these are checked next.
    legacy_media_root: Path | None = Path("/data/legacy-media")
    legacy_image_root: Path | None = Path("/data/legacy-images")

    cookie_secure: bool = True
    public_uploads: bool = False
    uploads_require_review: bool = True
    media_serve_mode: str = "app"  # app | nginx
    nginx_internal_prefix: str = "/__mediadrop_media__"
    max_upload_mb: int = 2047
    max_thumbnail_mb: int = 20
    page_size: int = 20
    locale: str = "en"

    # Security/audit events only: authentication and state-changing actions.
    audit_log_path: Path = Path("/data/logs/audit.log")
    audit_log_max_bytes: int = 20 * 1024 * 1024
    audit_log_backup_count: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
