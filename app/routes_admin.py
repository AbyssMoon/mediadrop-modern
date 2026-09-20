from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import db_dependency
from app.i18n import LOCALES
from app.models import Category, Comment, Group, Media, MediaFile, Podcast, Tag, User
from app.security import hash_legacy_password, require_admin, validate_csrf
from app.site_settings import get_site_settings, set_setting
from app.services import (
    available_slug,
    build_category_tree,
    delete_modern_thumbnails,
    flatten_category_tree,
    media_file_is_modern,
    media_file_path,
    sanitize_description,
    save_upload,
    save_thumbnail,
    set_categories,
    set_tags,
    slugify,
)
from app.views import render

router = APIRouter(prefix="/admin", tags=["admin"])
Db = Depends(db_dependency)


def _guard(request: Request, db: Session) -> User:
    return require_admin(request, db)


def _media_for_edit(db: Session, media_id: int) -> Media:
    media = db.scalar(
        select(Media)
        .where(Media.id == media_id)
        .options(
            selectinload(Media.files).selectinload(MediaFile.storage),
            selectinload(Media.tags),
            selectinload(Media.categories),
            selectinload(Media.podcast),
        )
    )
    if media is None:
        raise HTTPException(404)
    return media


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "Invalid date/time") from exc


def _edit_context(db: Session) -> dict:
    return {
        "categories": list(db.scalars(select(Category).order_by(Category.name)).all()),
        "podcasts": list(db.scalars(select(Podcast).order_by(Podcast.title)).all()),
    }


def _category_rows(db: Session):
    categories = list(db.scalars(select(Category).order_by(Category.name)).all())
    return flatten_category_tree(build_category_tree(categories))


def _validate_category_parent(db: Session, category_id: int | None, parent_id: int | None) -> None:
    if not parent_id:
        return
    if category_id is not None and parent_id == category_id:
        raise HTTPException(400, "A category cannot be its own parent")
    parent = db.get(Category, parent_id)
    if parent is None:
        raise HTTPException(400, "Unknown parent category")
    if category_id is None:
        return
    seen: set[int] = set()
    current = parent
    while current is not None and current.id not in seen:
        if current.id == category_id:
            raise HTTPException(400, "A category cannot be moved below its own descendant")
        seen.add(current.id)
        current = db.get(Category, current.parent_id) if current.parent_id else None


def _user_groups(db: Session) -> list[Group]:
    return list(
        db.scalars(
            select(Group)
            .options(selectinload(Group.permissions))
            .order_by(Group.display_name, Group.group_name)
        ).all()
    )


def _selected_groups(db: Session, group_ids: list[int]) -> list[Group]:
    if not group_ids:
        return []
    unique_ids = set(group_ids)
    groups = list(db.scalars(select(Group).where(Group.id.in_(unique_ids))).all())
    if len(groups) != len(unique_ids):
        raise HTTPException(400, "Unknown user group")
    return groups


def _validate_user_identity(
    db: Session,
    user_name: str,
    email_address: str,
    current_id: int | None = None,
) -> None:
    stmt = select(User.id).where(
        or_(User.user_name == user_name, User.email_address == email_address)
    )
    if current_id is not None:
        stmt = stmt.where(User.id != current_id)
    if db.scalar(stmt) is not None:
        raise HTTPException(409, "Username or email already exists")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Db):
    user = _guard(request, db)
    counts = {
        "media": db.scalar(select(func.count(Media.id))) or 0,
        "pending_media": db.scalar(
            select(func.count(Media.id)).where(Media.reviewed.is_(False))
        )
        or 0,
        "comments": db.scalar(select(func.count(Comment.id))) or 0,
        "pending_comments": db.scalar(
            select(func.count(Comment.id)).where(Comment.reviewed.is_(False))
        )
        or 0,
    }
    return render(request, "admin/dashboard.html", user=user, counts=counts)


@router.get("/media", response_class=HTMLResponse)
def media_list(request: Request, q: str | None = None, db: Session = Db):
    _guard(request, db)
    stmt = select(Media).options(selectinload(Media.podcast)).order_by(Media.created_on.desc())
    if q:
        stmt = stmt.where(Media.title.ilike(f"%{q}%"))
    rows = list(db.scalars(stmt.limit(500)).all())
    return render(request, "admin/media_list.html", media=rows, q=q)


@router.get("/media/new", response_class=HTMLResponse)
def media_new(request: Request, db: Session = Db):
    _guard(request, db)
    return render(request, "admin/media_form.html", media=None, **_edit_context(db))


@router.post("/media/new")
def media_create(
    request: Request,
    csrf: str = Form(...),
    title: str = Form(..., min_length=1, max_length=255),
    slug: str = Form(""),
    description: str = Form(""),
    notes: str = Form(""),
    author_name: str = Form("", max_length=50),
    author_email: str = Form("", max_length=255),
    podcast_id: int | None = Form(None),
    tags: str = Form(""),
    category_ids: list[int] = Form(default=[]),
    publish_on: str = Form(""),
    reviewed: str | None = Form(None),
    publishable: str | None = Form(None),
    file: UploadFile | None = File(None),
    thumbnail: UploadFile | None = File(None),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    description_html, description_plain = sanitize_description(description)
    media = Media(
        title=title,
        slug=available_slug(db, slug or title),
        description=description_html,
        description_plain=description_plain,
        notes=notes or None,
        author_name=author_name,
        author_email=author_email,
        podcast_id=podcast_id or None,
        reviewed=reviewed is not None,
        publishable=publishable is not None,
        encoded=False,
        publish_on=_parse_datetime(publish_on) or datetime.now(),
    )
    db.add(media)
    db.flush()
    set_tags(db, media, tags)
    set_categories(db, media, category_ids)
    created_paths: list[Path] = []
    try:
        if file is not None and file.filename:
            uploaded = save_upload(db, request.app.state.settings, media, file)
            if uploaded.unique_id:
                created_paths.append(request.app.state.settings.media_root / uploaded.unique_id)
        if thumbnail is not None and thumbnail.filename:
            save_thumbnail(request.app.state.settings, media, thumbnail)
    except ValueError as exc:
        for path in created_paths:
            path.unlink(missing_ok=True)
        delete_modern_thumbnails(request.app.state.settings, media.id)
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse(
        f"/admin/media/{media.id}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/media/{media_id:int}", response_class=HTMLResponse)
@router.get("/media/{media_id:int}/edit", response_class=HTMLResponse)
def media_edit(request: Request, media_id: int, db: Session = Db):
    _guard(request, db)
    media = _media_for_edit(db, media_id)
    return render(request, "admin/media_form.html", media=media, **_edit_context(db))


@router.post("/media/{media_id:int}/edit")
@router.post("/media/{media_id:int}/save")
def media_save(
    request: Request,
    media_id: int,
    csrf: str = Form(...),
    title: str = Form(..., min_length=1, max_length=255),
    slug: str = Form(""),
    description: str = Form(""),
    notes: str = Form(""),
    author_name: str = Form("", max_length=50),
    author_email: str = Form("", max_length=255),
    podcast_id: int | None = Form(None),
    tags: str = Form(""),
    category_ids: list[int] = Form(default=[]),
    publish_on: str = Form(""),
    publish_until: str = Form(""),
    reviewed: str | None = Form(None),
    publishable: str | None = Form(None),
    file: UploadFile | None = File(None),
    thumbnail: UploadFile | None = File(None),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    media = _media_for_edit(db, media_id)
    description_html, description_plain = sanitize_description(description)
    media.title = title
    media.slug = available_slug(db, slug or title, current_id=media.id)
    media.description = description_html
    media.description_plain = description_plain
    media.notes = notes or None
    media.author_name = author_name
    media.author_email = author_email
    media.podcast_id = podcast_id or None
    media.reviewed = reviewed is not None
    media.publishable = publishable is not None
    media.publish_on = _parse_datetime(publish_on) or media.publish_on or datetime.now()
    media.publish_until = _parse_datetime(publish_until)
    set_tags(db, media, tags)
    set_categories(db, media, category_ids)
    created_paths: list[Path] = []
    try:
        if file is not None and file.filename:
            uploaded = save_upload(db, request.app.state.settings, media, file)
            if uploaded.unique_id:
                created_paths.append(request.app.state.settings.media_root / uploaded.unique_id)
        if thumbnail is not None and thumbnail.filename:
            save_thumbnail(request.app.state.settings, media, thumbnail)
    except ValueError as exc:
        for path in created_paths:
            path.unlink(missing_ok=True)
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    # There is no encoder anymore. Existing playable files make media ready.
    if any(f.type in {"video", "audio"} for f in media.files):
        media.encoded = True
    db.commit()
    return RedirectResponse(
        f"/admin/media/{media.id}/edit", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/media/{media_id:int}/delete")
def media_delete(
    request: Request,
    media_id: int,
    csrf: str = Form(...),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    media = _media_for_edit(db, media_id)
    paths: list[Path] = []
    for media_file in media.files:
        if media_file.storage and media_file.storage.engine_type == "LocalFileStorage":
            try:
                paths.append(media_file_path(request.app.state.settings, media_file))
            except ValueError:
                pass
    db.delete(media)
    db.commit()
    for path in paths:
        if media_file_is_modern(request.app.state.settings, path):
            path.unlink(missing_ok=True)
    delete_modern_thumbnails(request.app.state.settings, media_id)
    return RedirectResponse("/admin/media", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/comments", response_class=HTMLResponse)
def comments(request: Request, db: Session = Db):
    _guard(request, db)
    rows = list(
        db.scalars(
            select(Comment)
            .options(selectinload(Comment.media))
            .order_by(Comment.created_on.desc())
            .limit(500)
        ).all()
    )
    return render(request, "admin/comments.html", comments=rows)


@router.post("/comments/{comment_id:int}/approve")
def comment_approve(
    request: Request,
    comment_id: int,
    csrf: str = Form(...),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(404)
    comment.reviewed = True
    comment.publishable = True
    db.commit()
    return RedirectResponse("/admin/comments", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/comments/{comment_id:int}/delete")
def comment_delete(
    request: Request,
    comment_id: int,
    csrf: str = Form(...),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(404)
    db.delete(comment)
    db.commit()
    return RedirectResponse("/admin/comments", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/categories", response_class=HTMLResponse)
@router.get("/settings/categories", response_class=HTMLResponse)
def category_admin(request: Request, db: Session = Db):
    _guard(request, db)
    rows = _category_rows(db)
    return render(request, "admin/categories.html", category_rows=rows)


@router.post("/categories/new")
def category_create(
    request: Request,
    csrf: str = Form(...),
    name: str = Form(..., min_length=1, max_length=50),
    parent_id: int | None = Form(None),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    _validate_category_parent(db, None, parent_id)
    name = name.strip()
    if not name:
        raise HTTPException(400, "Category name cannot be empty")
    slug = slugify(name)[:50]
    if db.scalar(select(Category.id).where(Category.slug == slug)) is not None:
        raise HTTPException(409, "Category slug already exists")
    db.add(Category(name=name, slug=slug, parent_id=parent_id or None))
    db.commit()
    return RedirectResponse("/admin/categories", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/categories/{category_id:int}/edit", response_class=HTMLResponse)
def category_edit(request: Request, category_id: int, db: Session = Db):
    _guard(request, db)
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(404)
    return render(
        request,
        "admin/category_form.html",
        category=category,
        category_rows=_category_rows(db),
    )


@router.post("/categories/{category_id:int}/edit")
def category_save(
    request: Request,
    category_id: int,
    csrf: str = Form(...),
    name: str = Form(..., min_length=1, max_length=50),
    slug: str = Form("", max_length=50),
    parent_id: int | None = Form(None),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(404)
    _validate_category_parent(db, category_id, parent_id)
    name = name.strip()
    if not name:
        raise HTTPException(400, "Category name cannot be empty")
    # Preserve an explicitly submitted legacy slug byte-for-byte apart from
    # surrounding whitespace. Renaming/reparenting an imported category must
    # not silently rewrite old public URLs.
    normalized_slug = slug.strip() or slugify(name)[:50]
    duplicate = db.scalar(
        select(Category.id).where(
            Category.slug == normalized_slug,
            Category.id != category_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(409, "Category slug already exists")
    category.name = name
    category.slug = normalized_slug
    category.parent_id = parent_id or None
    db.commit()
    return RedirectResponse("/admin/categories", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categories/{category_id:int}/delete")
def category_delete(
    request: Request,
    category_id: int,
    csrf: str = Form(...),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(404)
    db.delete(category)
    db.commit()
    return RedirectResponse("/admin/categories", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/podcasts", response_class=HTMLResponse)
def podcast_admin(request: Request, db: Session = Db):
    _guard(request, db)
    rows = list(db.scalars(select(Podcast).order_by(Podcast.title)).all())
    return render(request, "admin/podcasts.html", podcasts=rows)


@router.post("/podcasts/new")
def podcast_create(
    request: Request,
    csrf: str = Form(...),
    title: str = Form(..., min_length=1, max_length=50),
    description: str = Form(""),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    slug = slugify(title)[:50]
    if db.scalar(select(Podcast.id).where(Podcast.slug == slug)) is not None:
        raise HTTPException(409, "Podcast slug already exists")
    db.add(Podcast(title=title, slug=slug, description=description, author_name="", author_email=""))
    db.commit()
    return RedirectResponse("/admin/podcasts", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/podcasts/{podcast_id:int}/delete")
def podcast_delete(
    request: Request,
    podcast_id: int,
    csrf: str = Form(...),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    podcast = db.get(Podcast, podcast_id)
    if podcast is None:
        raise HTTPException(404)
    # Preserve media and only detach it from the podcast.
    db.execute(
        Media.__table__.update().where(Media.podcast_id == podcast_id).values(podcast_id=None)
    )
    db.delete(podcast)
    db.commit()
    return RedirectResponse("/admin/podcasts", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, db: Session = Db):
    _guard(request, db)
    rows = list(
        db.scalars(
            select(User)
            .options(selectinload(User.groups).selectinload(Group.permissions))
            .order_by(User.user_name)
        ).all()
    )
    return render(request, "admin/users.html", users=rows)


@router.get("/users/new", response_class=HTMLResponse)
def user_new(request: Request, db: Session = Db):
    _guard(request, db)
    return render(request, "admin/user_form.html", user=None, groups=_user_groups(db))


@router.post("/users/new")
def user_create(
    request: Request,
    csrf: str = Form(...),
    user_name: str = Form(..., min_length=1, max_length=16),
    email_address: str = Form(..., min_length=3, max_length=255),
    display_name: str = Form("", max_length=255),
    password: str = Form(..., min_length=8, max_length=256),
    group_ids: list[int] = Form(default=[]),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    user_name = user_name.strip()
    email_address = email_address.strip()
    if not user_name or not email_address:
        raise HTTPException(400, "Username and email are required")
    _validate_user_identity(db, user_name, email_address)
    user = User(
        user_name=user_name,
        email_address=email_address,
        display_name=display_name.strip() or None,
        password=hash_legacy_password(password),
    )
    user.groups = _selected_groups(db, group_ids)
    db.add(user)
    db.commit()
    return RedirectResponse(f"/admin/users/{user.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/users/{user_id:int}/edit", response_class=HTMLResponse)
def user_edit(request: Request, user_id: int, db: Session = Db):
    _guard(request, db)
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.groups).selectinload(Group.permissions))
    )
    if user is None:
        raise HTTPException(404)
    return render(request, "admin/user_form.html", user=user, groups=_user_groups(db))


@router.post("/users/{user_id:int}/edit")
def user_save(
    request: Request,
    user_id: int,
    csrf: str = Form(...),
    user_name: str = Form(..., min_length=1, max_length=16),
    email_address: str = Form(..., min_length=3, max_length=255),
    display_name: str = Form("", max_length=255),
    password: str = Form("", max_length=256),
    group_ids: list[int] = Form(default=[]),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    user = db.scalar(select(User).where(User.id == user_id).options(selectinload(User.groups)))
    if user is None:
        raise HTTPException(404)
    user_name = user_name.strip()
    email_address = email_address.strip()
    if not user_name or not email_address:
        raise HTTPException(400, "Username and email are required")
    _validate_user_identity(db, user_name, email_address, current_id=user.id)
    user.user_name = user_name
    user.email_address = email_address
    user.display_name = display_name.strip() or None
    if password:
        if len(password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        user.password = hash_legacy_password(password)
    user.groups = _selected_groups(db, group_ids)
    db.commit()
    return RedirectResponse(f"/admin/users/{user.id}/edit", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    saved: bool = False,
    db: Session = Db,
):
    _guard(request, db)
    site = get_site_settings(db, request.app.state.settings)
    categories = list(db.scalars(select(Category).order_by(Category.name)).all())
    return render(
        request,
        "admin/settings.html",
        site_values=site,
        categories=categories,
        language_options=LOCALES,
        saved=saved,
    )


@router.post("/settings")
def settings_save(
    request: Request,
    csrf: str = Form(...),
    site_name: str = Form(..., min_length=1, max_length=255),
    primary_language: str = Form("en"),
    featured_category: int | None = Form(None),
    comments_enabled: str | None = Form(None),
    require_comment_approval: str | None = Form(None),
    enable_user_uploads: str | None = Form(None),
    enable_podcasts: str | None = Form(None),
    show_download: str | None = Form(None),
    show_like: str | None = Form(None),
    show_dislike: str | None = Form(None),
    rss_enabled: str | None = Form(None),
    sitemap_enabled: str | None = Form(None),
    max_upload_mb: int = Form(2047, ge=1, le=1048576),
    accent_color: str = Form("#3157d5"),
    footer_text: str = Form("", max_length=500),
    db: Session = Db,
):
    _guard(request, db)
    validate_csrf(request, csrf)
    if primary_language not in LOCALES:
        raise HTTPException(400, "Unsupported language")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent_color):
        raise HTTPException(400, "Accent color must be a six-digit hex color")
    if featured_category and db.get(Category, featured_category) is None:
        raise HTTPException(400, "Unknown featured category")

    set_setting(db, "general_site_name", site_name.strip())
    set_setting(db, "primary_language", primary_language)
    set_setting(db, "featured_category", featured_category or "")
    set_setting(db, "comments_engine", "builtin" if comments_enabled else "disabled")
    set_setting(db, "req_comment_approval", require_comment_approval is not None)
    set_setting(db, "appearance_enable_user_uploads", enable_user_uploads is not None)
    set_setting(db, "appearance_enable_podcast_tab", enable_podcasts is not None)
    set_setting(db, "appearance_show_download", show_download is not None)
    set_setting(db, "appearance_show_like", show_like is not None)
    set_setting(db, "appearance_show_dislike", show_dislike is not None)
    set_setting(db, "rss_display", rss_enabled is not None)
    set_setting(db, "sitemaps_display", sitemap_enabled is not None)
    set_setting(db, "modern_max_upload_mb", max_upload_mb)
    set_setting(db, "modern_accent_color", accent_color.lower())
    set_setting(db, "modern_footer_text", footer_text.strip())
    db.commit()
    return RedirectResponse("/admin/settings?saved=1", status_code=status.HTTP_303_SEE_OTHER)

