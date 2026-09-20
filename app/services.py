from __future__ import annotations

import html
import io
import ipaddress
import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import bleach
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from unidecode import unidecode

from app.config import Settings
from app.models import Category, Media, MediaFile, Storage, Tag

SAFE_DESCRIPTION_TAGS = ["p", "br", "strong", "em", "ul", "ol", "li", "a", "blockquote"]
SAFE_DESCRIPTION_ATTRS = {"a": ["href", "title", "rel"]}
VIDEO_EXTENSIONS = {"mp4", "m4v", "webm", "ogv", "ogg"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "aac", "oga", "ogg", "wav", "flac"}
THUMBNAIL_SIZES: dict[str, tuple[int, int]] = {
    # Keep legacy s/m/l filenames so imported MediaDrop installations need no
    # schema or URL migration, but make newly generated previews suitable for
    # modern high-density displays. Images are never upscaled above the source.
    "s": (320, 180),
    "m": (960, 540),
    "l": (1920, 1080),
}
THUMBNAIL_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


@dataclass(slots=True)
class CategoryNode:
    category: Category
    depth: int = 0
    children: list["CategoryNode"] = field(default_factory=list)


def build_category_tree(categories: Iterable[Category]) -> list[CategoryNode]:
    """Build a stable hierarchy without relying on eager-loaded relationships.

    Legacy databases can contain orphaned parent ids, so orphaned categories are
    treated as roots. A defensive visited set also prevents malformed cycles
    from making templates recurse forever.
    """
    ordered = sorted(categories, key=lambda category: (category.name or "").casefold())
    by_id = {category.id: category for category in ordered if category.id is not None}
    child_ids: dict[int, list[int]] = {}
    root_ids: list[int] = []
    for category in ordered:
        if category.id is None:
            continue
        parent_id = category.parent_id
        if parent_id is None or parent_id not in by_id or parent_id == category.id:
            root_ids.append(category.id)
        else:
            child_ids.setdefault(parent_id, []).append(category.id)

    visited: set[int] = set()

    def make_node(category_id: int, depth: int, ancestors: set[int]) -> CategoryNode | None:
        if category_id in visited or category_id in ancestors:
            return None
        category = by_id[category_id]
        visited.add(category_id)
        node = CategoryNode(category=category, depth=depth)
        next_ancestors = ancestors | {category_id}
        for child_id in child_ids.get(category_id, []):
            child = make_node(child_id, depth + 1, next_ancestors)
            if child is not None:
                node.children.append(child)
        return node

    roots: list[CategoryNode] = []
    for category_id in root_ids:
        node = make_node(category_id, 0, set())
        if node is not None:
            roots.append(node)

    # A fully cyclic legacy hierarchy has no root. Surface any leftovers as
    # top-level items instead of hiding them or recursing forever.
    for category in ordered:
        if category.id is not None and category.id not in visited:
            node = make_node(category.id, 0, set())
            if node is not None:
                roots.append(node)
    return roots


def flatten_category_tree(nodes: Iterable[CategoryNode]) -> list[CategoryNode]:
    rows: list[CategoryNode] = []
    for node in nodes:
        rows.append(node)
        rows.extend(flatten_category_tree(node.children))
    return rows


def sanitize_description(value: str) -> tuple[str, str]:
    cleaned = bleach.clean(
        value or "",
        tags=SAFE_DESCRIPTION_TAGS,
        attributes=SAFE_DESCRIPTION_ATTRS,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
    plain = bleach.clean(cleaned, tags=[], strip=True)
    return cleaned, html.unescape(plain)


def slugify(value: str) -> str:
    value = unidecode(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:45] or uuid.uuid4().hex[:12]


def available_slug(db: Session, title_or_slug: str, current_id: int | None = None) -> str:
    base = slugify(title_or_slug)
    candidate = base
    n = 2
    while True:
        stmt = select(Media.id).where(Media.slug == candidate)
        if current_id is not None:
            stmt = stmt.where(Media.id != current_id)
        if db.scalar(stmt) is None:
            return candidate
        suffix = f"-{n}"
        candidate = f"{base[: 50 - len(suffix)]}{suffix}"
        n += 1


def get_or_create_local_storage(db: Session, settings: Settings) -> Storage:
    storage = db.scalar(
        select(Storage).where(Storage.engine_type == "LocalFileStorage", Storage.enabled.is_(True))
    )
    if storage:
        return storage
    storage = Storage(
        engine_type="LocalFileStorage",
        display_name="Local File Storage",
        enabled=True,
        data="{}",
    )
    db.add(storage)
    db.flush()
    return storage


def detect_media_type(filename: str, content_type: str | None = None) -> tuple[str, str, str]:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in VIDEO_EXTENSIONS:
        media_type = "video"
    elif ext in AUDIO_EXTENSIONS:
        media_type = "audio"
    elif content_type and content_type.startswith("video/") and ext != "flv":
        media_type = "video"
    elif content_type and content_type.startswith("audio/"):
        media_type = "audio"
    else:
        raise ValueError("Unsupported format. Upload a web-ready audio/video file.")
    mimetype = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return media_type, ext or "bin", mimetype


def save_upload(db: Session, settings: Settings, media: Media, upload: UploadFile) -> MediaFile:
    media_type, container, _ = detect_media_type(upload.filename or "upload", upload.content_type)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    unique_id = f"{uuid.uuid4().hex}.{container}"
    destination = settings.media_root / unique_id
    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with destination.open("wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(f"File exceeds {settings.max_upload_mb} MB limit")
                out.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        upload.file.close()

    storage = get_or_create_local_storage(db, settings)
    mf = MediaFile(
        media=media,
        storage=storage,
        type=media_type,
        container=container,
        display_name=upload.filename or unique_id,
        unique_id=unique_id,
        size=written,
    )
    db.add(mf)
    media.type = media_type if media_type == "video" or not media.type else media.type
    # No encoder pipeline: accepted files are considered browser-ready.
    media.encoded = True
    return mf


def _safe_file(root: Path, name: str) -> Path:
    root = root.resolve()
    path = (root / name).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid file path")
    return path


def media_file_path(settings: Settings, media_file: MediaFile) -> Path:
    name = media_file.unique_id or ""
    modern = _safe_file(settings.media_root, name)
    if modern.is_file():
        return modern
    if settings.legacy_media_root:
        legacy = _safe_file(settings.legacy_media_root, name)
        if legacy.is_file():
            return legacy
    # Keep the historical behavior for callers that need a deterministic path
    # even when the file does not exist.
    return modern


def media_file_is_modern(settings: Settings, path: Path) -> bool:
    try:
        path.resolve().relative_to(settings.media_root.resolve())
        return True
    except ValueError:
        return False


def _thumbnail_roots(settings: Settings) -> list[Path]:
    roots = [settings.image_root]
    if settings.legacy_image_root:
        roots.append(settings.legacy_image_root)
    return roots


def thumbnail_path(settings: Settings, media_id: int, size: str = "m") -> Path | None:
    """Return a modern or legacy MediaDrop thumbnail path.

    Legacy MediaDrop stores media thumbnails as images/media/<id><size>.jpg,
    where size is usually s/m/l. Original uploads are backed up as
    <id>orig.<extension>. We understand both layouts without modifying them.
    """
    if size not in THUMBNAIL_SIZES:
        size = "m"
    order = {
        "s": ("s", "m", "l"),
        "m": ("m", "l", "s"),
        "l": ("l", "m", "s"),
    }[size]
    for root in _thumbnail_roots(settings):
        media_dir = root / "media"
        for key in order:
            path = media_dir / f"{int(media_id)}{key}.jpg"
            if path.is_file():
                return path
        for ext in ("jpg", "jpeg", "png", "webp"):
            path = media_dir / f"{int(media_id)}orig.{ext}"
            if path.is_file():
                return path
    return None


def delete_modern_thumbnails(settings: Settings, media_id: int) -> None:
    media_dir = settings.image_root / "media"
    for key in THUMBNAIL_SIZES:
        (media_dir / f"{int(media_id)}{key}.jpg").unlink(missing_ok=True)
    if media_dir.exists():
        for original in media_dir.glob(f"{int(media_id)}orig.*"):
            if original.is_file():
                original.unlink(missing_ok=True)


def save_thumbnail(settings: Settings, media: Media, upload: UploadFile) -> None:
    """Store a preview image and generate high-resolution legacy s/m/l JPEGs."""
    if not media.id:
        raise ValueError("Media must be saved before its preview image")

    max_bytes = settings.max_thumbnail_mb * 1024 * 1024
    data = upload.file.read(max_bytes + 1)
    upload.file.close()
    if len(data) > max_bytes:
        raise ValueError(f"Preview image exceeds {settings.max_thumbnail_mb} MB limit")
    if not data:
        raise ValueError("Preview image is empty")

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Preview must be a JPEG, PNG or WebP image") from exc

    if image.format not in THUMBNAIL_FORMATS:
        raise ValueError("Preview must be a JPEG, PNG or WebP image")
    if image.width * image.height > 50_000_000:
        raise ValueError("Preview image dimensions are too large")

    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")

    media_dir = settings.image_root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    delete_modern_thumbnails(settings, media.id)

    # Keep the user's original upload as a backup just like legacy MediaDrop.
    original_ext = THUMBNAIL_FORMATS[Image.open(io.BytesIO(data)).format]
    (media_dir / f"{media.id}orig.{original_ext}").write_bytes(data)

    # Crop once to the 16:9 poster shape, then only downscale. This preserves
    # the uploaded detail while avoiding pointless enlargement of small images.
    source_ratio = image.width / image.height
    target_ratio = 16 / 9
    if source_ratio > target_ratio:
        crop_width = max(1, round(image.height * target_ratio))
        left = max(0, (image.width - crop_width) // 2)
        poster = image.crop((left, 0, left + crop_width, image.height))
    elif source_ratio < target_ratio:
        crop_height = max(1, round(image.width / target_ratio))
        top = max(0, (image.height - crop_height) // 2)
        poster = image.crop((0, top, image.width, top + crop_height))
    else:
        poster = image

    for key, size in THUMBNAIL_SIZES.items():
        thumb = poster.copy()
        if thumb.width > size[0] or thumb.height > size[1]:
            thumb.thumbnail(size, resample=Image.Resampling.LANCZOS)
        target = media_dir / f"{media.id}{key}.jpg"
        temp = media_dir / f".{media.id}{key}.{uuid.uuid4().hex}.tmp"
        try:
            thumb.save(temp, format="JPEG", quality=93, optimize=True, progressive=True)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)


def set_tags(db: Session, media: Media, raw: str) -> None:
    names = {x.strip() for x in re.split(r"[,\n]", raw or "") if x.strip()}
    result: list[Tag] = []
    for name in sorted(names):
        slug = slugify(name)
        tag = db.scalar(select(Tag).where(func.lower(Tag.name) == name.lower()))
        if tag is None:
            tag = db.scalar(select(Tag).where(Tag.slug == slug))
        if tag is None:
            tag = Tag(name=name[:50], slug=slug[:50])
            db.add(tag)
            db.flush()
        result.append(tag)
    media.tags = result


def set_categories(db: Session, media: Media, category_ids: list[int]) -> None:
    if not category_ids:
        media.categories = []
        return
    media.categories = list(
        db.scalars(select(Category).where(Category.id.in_(category_ids))).all()
    )


def ip_to_legacy_int(value: str | None) -> int:
    try:
        ip = ipaddress.ip_address(value or "0.0.0.0")
        if ip.version == 4:
            return int(ip)
    except ValueError:
        pass
    return 0
