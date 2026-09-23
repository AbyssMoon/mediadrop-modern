from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import Request

from app.config import Settings
from app.security import AuthPrincipal

_LOGGER_NAME = "mediadrop.audit"


def configure_audit_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    path = Path(settings.audit_log_path).resolve()
    # create_app() may be called more than once in tests/tools. Reuse the
    # handler only when it points at the same configured file.
    for existing in list(logger.handlers):
        existing_path = Path(getattr(existing, "baseFilename", "")).resolve() if getattr(existing, "baseFilename", "") else None
        if existing_path == path:
            return logger
        logger.removeHandler(existing)
        existing.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.audit_log_max_bytes,
        backupCount=settings.audit_log_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _client_ip(request: Request) -> str:
    # In production the app is reachable only from the trusted reverse proxy,
    # which sets X-Real-IP. Fall back to the direct peer for local/dev use.
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else ""


def audit_event(
    request: Request,
    event: str,
    *,
    principal: AuthPrincipal | None = None,
    username: str | None = None,
    backend: str | None = None,
    outcome: str = "success",
    object_type: str | None = None,
    object_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        logger = configure_audit_logging(request.app.state.settings)

    if principal is not None:
        username = principal.username
        backend = principal.backend

    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": event,
        "outcome": outcome,
        "username": username or "",
        "backend": backend or "",
        "ip": _client_ip(request),
        "method": request.method,
        "path": request.url.path,
    }
    if object_type:
        record["object_type"] = object_type
    if object_id is not None:
        record["object_id"] = object_id
    if details:
        record["details"] = details

    logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
