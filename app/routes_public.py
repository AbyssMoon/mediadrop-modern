from __future__ import annotations

from datetime import datetime
from html import escape
from random import choice

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import db_dependency
from app.models import Category, Comment, Media, MediaFile, Podcast, Setting, Tag, media_categories
from app.security import validate_csrf
from app.site_settings import get_site_settings
from app.services import (
    available_slug,
    build_category_tree,
    delete_modern_thumbnails,
    ip_to_legacy_int,
    media_file_path,
    sanitize_description,
    save_upload,
    save_thumbnail,
    thumbnail_path,
)
from app.views import playable_file, render

router = APIRouter()
Db = Depends(db_dependency)


def _site_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.scalar(select(Setting).where(Setting.key == key))
    return row.value if row and row.value is not None else default


def _published_stmt():
    return select(Media).where(Media.published_clause())


def _published_category_counts(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(media_categories.c.category_id, func.count(func.distinct(Media.id)))
        .select_from(media_categories.join(Media, Media.id == media_categories.c.media_id))
        .where(Media.published_clause())
        .group_by(media_categories.c.category_id)
    ).all()
    return {int(category_id): int(count) for category_id, count in rows}


def _media_url(media: Media) -> str:
    if media.podcast:
        return f"/podcasts/{media.podcast.slug}/{media.slug}/view"
    return f"/media/{media.slug}/view"




@router.get("/language/{locale}")
def set_language(locale: str, next: str = "/"):
    from app.i18n import SUPPORTED_LOCALES

    if locale not in SUPPORTED_LOCALES:
        raise HTTPException(404)
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    response = RedirectResponse(next, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        "mediadrop_locale",
        locale,
        max_age=60 * 60 * 24 * 365,
        samesite="lax",
        httponly=False,
    )
    return response


@router.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@router.get("/", response_class=HTMLResponse)
def explore(request: Request, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    load_options = (
        selectinload(Media.files),
        selectinload(Media.podcast),
        selectinload(Media.categories),
        selectinload(Media.comments),
    )

    featured = None
    featured_category_id = site.get("featured_category")
    if featured_category_id:
        featured = db.scalar(
            _published_stmt()
            .join(Media.categories)
            .where(Category.id == int(featured_category_id))
            .options(*load_options)
            .order_by(Media.popularity_points.desc(), Media.publish_on.desc())
            .limit(1)
        )
    if featured is None:
        featured = db.scalar(
            _published_stmt()
            .options(*load_options)
            .order_by(Media.popularity_points.desc(), Media.views.desc(), Media.publish_on.desc())
            .limit(1)
        )

    latest = list(
        db.scalars(
            _published_stmt()
            .options(*load_options)
            .order_by(Media.publish_on.desc())
            .limit(6)
        ).all()
    )
    popular_stmt = (
        _published_stmt()
        .options(*load_options)
        .order_by(Media.popularity_points.desc(), Media.views.desc(), Media.publish_on.desc())
        .limit(5)
    )
    popular = list(db.scalars(popular_stmt).all())
    if featured is not None:
        popular = [item for item in popular if item.id != featured.id][:4]
    else:
        popular = popular[:4]

    categories = list(
        db.scalars(
            select(Category)
            .where(Category.parent_id.is_(None))
            .order_by(Category.name)
            .limit(12)
        ).all()
    )
    category_counts = _published_category_counts(db)
    return render(
        request,
        "home.html",
        featured=featured,
        latest=latest,
        popular=popular,
        categories=categories,
        category_counts=category_counts,
    )


@router.get("/media", response_class=HTMLResponse)
def media_index(
    request: Request,
    q: str | None = None,
    show: str = "latest",
    page: int = Query(1, ge=1),
    db: Session = Db,
):
    settings = request.app.state.settings
    stmt = _published_stmt().options(selectinload(Media.files), selectinload(Media.podcast))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Media.title.ilike(like), Media.description_plain.ilike(like)))
    if show == "popular":
        stmt = stmt.order_by(Media.popularity_points.desc(), Media.publish_on.desc())
    else:
        stmt = stmt.order_by(Media.publish_on.desc())
    stmt = stmt.offset((page - 1) * settings.page_size).limit(settings.page_size + 1)
    rows = list(db.scalars(stmt).all())
    has_next = len(rows) > settings.page_size
    rows = rows[: settings.page_size]
    return render(
        request,
        "media/index.html",
        media=rows,
        q=q,
        show=show,
        page=page,
        has_next=has_next,
        title="Media",
    )


@router.get("/random")
def random_media(db: Session = Db):
    rows = list(db.scalars(_published_stmt().options(selectinload(Media.podcast))).all())
    if not rows:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(_media_url(choice(rows)), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/media/{slug}")
def media_short(slug: str, db: Session = Db):
    media = db.scalar(select(Media).where(Media.slug == slug).options(selectinload(Media.podcast)))
    if media is None:
        raise HTTPException(404)
    return RedirectResponse(_media_url(media), status_code=status.HTTP_308_PERMANENT_REDIRECT)


@router.get("/media/{slug}/view", response_class=HTMLResponse)
@router.get("/podcasts/{podcast_slug}/{slug}/view", response_class=HTMLResponse)
def media_view(
    request: Request,
    slug: str,
    podcast_slug: str | None = None,
    db: Session = Db,
):
    stmt = (
        select(Media)
        .where(Media.slug == slug)
        .options(
            selectinload(Media.files).selectinload(MediaFile.storage),
            selectinload(Media.tags),
            selectinload(Media.categories),
            selectinload(Media.podcast),
            selectinload(Media.comments),
        )
    )
    media = db.scalar(stmt)
    if media is None or not media.is_published:
        raise HTTPException(404)
    if media.podcast and podcast_slug != media.podcast.slug:
        return RedirectResponse(_media_url(media), status_code=status.HTTP_308_PERMANENT_REDIRECT)
    media.views = (media.views or 0) + 1
    db.commit()
    comments = [c for c in media.comments if c.reviewed and c.publishable]
    playable = playable_file(media)
    return render(request, "media/view.html", media=media, playable=playable, comments=comments)


@router.post("/media/{slug}/rate")
@router.post("/podcasts/{podcast_slug}/{slug}/rate")
def media_rate(
    request: Request,
    slug: str,
    podcast_slug: str | None = None,
    csrf: str = Form(...),
    direction: str = Form(...),
    db: Session = Db,
):
    validate_csrf(request, csrf)
    media = db.scalar(select(Media).where(Media.slug == slug).options(selectinload(Media.podcast)))
    if media is None or not media.is_published:
        raise HTTPException(404)
    site = get_site_settings(db, request.app.state.settings)
    if direction == "up":
        if not site["show_like"]:
            raise HTTPException(404)
        media.likes = (media.likes or 0) + 1
    elif direction == "down":
        if not site["show_dislike"]:
            raise HTTPException(404)
        media.dislikes = (media.dislikes or 0) + 1
    else:
        raise HTTPException(400, "Invalid rating")
    media.popularity_points = (media.likes or 0) - (media.dislikes or 0)
    db.commit()
    return RedirectResponse(_media_url(media), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/media/{slug}/comment")
@router.post("/podcasts/{podcast_slug}/{slug}/comment")
def media_comment(
    request: Request,
    slug: str,
    podcast_slug: str | None = None,
    csrf: str = Form(...),
    name: str = Form(..., min_length=1, max_length=50),
    email: str = Form("", max_length=255),
    body: str = Form(..., min_length=1, max_length=10000),
    db: Session = Db,
):
    validate_csrf(request, csrf)
    media = db.scalar(select(Media).where(Media.slug == slug).options(selectinload(Media.podcast)))
    if media is None or not media.is_published:
        raise HTTPException(404)
    site = get_site_settings(db, request.app.state.settings)
    if not site["comments_enabled"]:
        raise HTTPException(404)
    require_review = bool(site["require_comment_approval"])
    comment = Comment(
        media=media,
        subject=f"Re: {media.title}"[:100],
        reviewed=not require_review,
        publishable=not require_review,
        author_name=name,
        author_email=email or None,
        author_ip=ip_to_legacy_int(request.client.host if request.client else None),
        body=body,
    )
    db.add(comment)
    db.commit()
    return RedirectResponse(_media_url(media), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/thumbnails/media/{media_id:int}/{size}.jpg")
def serve_thumbnail(
    request: Request,
    media_id: int,
    size: str,
):
    if size not in {"s", "m", "l"}:
        raise HTTPException(404)
    path = thumbnail_path(request.app.state.settings, media_id, size)
    if path is None or not path.is_file():
        raise HTTPException(404)
    response = FileResponse(path)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@router.get("/files/{file_id:int}.{container}")
@router.get("/files/{file_id:int}-{slug}.{container}")
def serve_file(
    request: Request,
    file_id: int,
    container: str,
    slug: str | None = None,
    download: bool = False,
    db: Session = Db,
):
    media_file = db.scalar(
        select(MediaFile)
        .where(MediaFile.id == file_id)
        .options(selectinload(MediaFile.media), selectinload(MediaFile.storage))
    )
    if media_file is None or media_file.media is None or not media_file.media.is_published:
        raise HTTPException(404)
    if media_file.storage and media_file.storage.engine_type == "RemoteURLStorage":
        uid = media_file.unique_id or ""
        if uid.startswith(("http://", "https://")):
            return RedirectResponse(uid, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        raise HTTPException(404)
    path = media_file_path(request.app.state.settings, media_file)
    if not path.is_file():
        raise HTTPException(404)
    media_type = media_file.type or "video"
    mime = {
        "mp4": "video/mp4",
        "m4v": "video/mp4",
        "webm": "video/webm",
        "ogv": "video/ogg",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "oga": "audio/ogg",
        "ogg": "audio/ogg" if media_type == "audio" else "video/ogg",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get((media_file.container or container).lower(), "application/octet-stream")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{media_file.display_name}"'
    settings = request.app.state.settings
    if settings.media_serve_mode == "nginx":
        internal = f"{settings.nginx_internal_prefix.rstrip('/')}/{media_file.unique_id}"
        headers["X-Accel-Redirect"] = internal
        return Response(headers=headers, media_type=mime)
    return FileResponse(path, media_type=mime, filename=media_file.display_name if download else None, headers=headers)


@router.get("/categories", response_class=HTMLResponse)
def categories_index(request: Request, db: Session = Db):
    categories = list(db.scalars(select(Category).order_by(Category.name)).all())
    tree = build_category_tree(categories)
    counts = _published_category_counts(db)
    return render(
        request,
        "categories/index.html",
        category_tree=tree,
        counts=counts,
    )


@router.get("/categories/{slug}", response_class=HTMLResponse)
def category_view(request: Request, slug: str, db: Session = Db):
    category = db.scalar(
        select(Category)
        .where(Category.slug == slug)
        .options(selectinload(Category.parent), selectinload(Category.children))
    )
    if category is None:
        raise HTTPException(404)
    rows = list(
        db.scalars(
            _published_stmt()
            .join(Media.categories)
            .where(Category.id == category.id)
            .options(selectinload(Media.files), selectinload(Media.podcast))
            .order_by(Media.publish_on.desc())
        ).unique().all()
    )
    counts = _published_category_counts(db)
    children = sorted(category.children, key=lambda child: child.name.casefold())
    return render(
        request,
        "categories/view.html",
        category=category,
        children=children,
        counts=counts,
        media=rows,
    )


@router.get("/tags", response_class=HTMLResponse)
def tags_index(request: Request, db: Session = Db):
    tags = list(db.scalars(select(Tag).order_by(Tag.name)).all())
    return render(request, "media/tags.html", tags=tags)


@router.get("/tags/{tag}", response_class=HTMLResponse)
def tag_view(request: Request, tag: str, db: Session = Db):
    tag_obj = db.scalar(select(Tag).where(Tag.slug == tag))
    if tag_obj is None:
        raise HTTPException(404)
    rows = list(
        db.scalars(
            _published_stmt()
            .join(Media.tags)
            .where(Tag.id == tag_obj.id)
            .options(selectinload(Media.files), selectinload(Media.podcast))
            .order_by(Media.publish_on.desc())
        ).unique().all()
    )
    return render(request, "media/index.html", media=rows, q=None, show="latest", page=1, has_next=False, title=f"Tag: {tag_obj.name}")


@router.get("/podcasts", response_class=HTMLResponse)
def podcast_index(request: Request, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    if not site["enable_podcasts"]:
        raise HTTPException(404)
    podcasts = list(db.scalars(select(Podcast).order_by(Podcast.title.asc())).all())
    counts = dict(
        db.execute(
            select(Media.podcast_id, func.count(Media.id))
            .where(
                Media.podcast_id.is_not(None),
                Media.published_clause(),
            )
            .group_by(Media.podcast_id)
        ).all()
    )
    return render(request, "podcasts/index.html", podcasts=podcasts, counts=counts)


@router.get("/podcasts/{slug}", response_class=HTMLResponse)
def podcast_view(request: Request, slug: str, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    if not site["enable_podcasts"]:
        raise HTTPException(404)
    podcast = db.scalar(select(Podcast).where(Podcast.slug == slug))
    if podcast is None:
        raise HTTPException(404)
    rows = list(
        db.scalars(
            _published_stmt()
            .where(Media.podcast_id == podcast.id)
            .options(selectinload(Media.files), selectinload(Media.podcast))
            .order_by(Media.publish_on.desc())
        ).all()
    )
    return render(request, "podcasts/view.html", podcast=podcast, media=rows)


@router.get("/podcasts/feed/{slug}.xml")
def podcast_feed(request: Request, slug: str, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    if not site["enable_podcasts"] or not site["rss_enabled"]:
        raise HTTPException(404)
    podcast = db.scalar(select(Podcast).where(Podcast.slug == slug))
    if podcast is None:
        raise HTTPException(404)
    rows = list(
        db.scalars(
            _published_stmt()
            .where(Media.podcast_id == podcast.id)
            .options(selectinload(Media.files))
            .order_by(Media.publish_on.desc())
            .limit(100)
        ).all()
    )
    base = str(request.base_url).rstrip("/")
    items = []
    for media in rows:
        enclosure = next((f for f in media.files if f.type in {"audio", "video"}), None)
        enc = ""
        if enclosure:
            url = f"{base}/files/{enclosure.id}-{media.slug}.{enclosure.container or 'bin'}"
            enc = f'<enclosure url="{escape(url)}" length="{enclosure.size or 0}" type="application/octet-stream" />'
        pub = media.publish_on.strftime("%a, %d %b %Y %H:%M:%S +0000") if media.publish_on else ""
        items.append(
            f"<item><title>{escape(media.title)}</title><link>{escape(base + _media_url(media))}</link>"
            f"<guid>{escape(base + _media_url(media))}</guid><pubDate>{pub}</pubDate>{enc}"
            f"<description>{escape(media.description_plain or '')}</description></item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{escape(podcast.title)}</title><link>{escape(base + '/podcasts/' + podcast.slug)}</link>"
        f"<description>{escape(podcast.description or '')}</description>{''.join(items)}</channel></rss>"
    )
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    if not site["enable_user_uploads"]:
        raise HTTPException(404)
    return render(request, "upload.html")


@router.post("/upload")
def upload_submit(
    request: Request,
    csrf: str = Form(...),
    title: str = Form(..., min_length=1, max_length=255),
    description: str = Form(""),
    name: str = Form("", max_length=50),
    email: str = Form("", max_length=255),
    file: UploadFile | None = File(None),
    thumbnail: UploadFile | None = File(None),
    db: Session = Db,
):
    settings = request.app.state.settings
    site = get_site_settings(db, settings)
    if not site["enable_user_uploads"]:
        raise HTTPException(404)
    effective_settings = settings.model_copy(update={"max_upload_mb": int(site["max_upload_mb"])})
    validate_csrf(request, csrf)
    if file is None or not file.filename:
        raise HTTPException(400, "A media file is required")
    description_html, description_plain = sanitize_description(description)
    now = datetime.now()
    media = Media(
        slug=available_slug(db, title),
        title=title,
        description=description_html,
        description_plain=description_plain,
        author_name=name,
        author_email=email,
        reviewed=not settings.uploads_require_review,
        publishable=not settings.uploads_require_review,
        encoded=True,
        publish_on=now,
    )
    db.add(media)
    db.flush()
    uploaded = None
    try:
        uploaded = save_upload(db, effective_settings, media, file)
        if thumbnail is not None and thumbnail.filename:
            save_thumbnail(settings, media, thumbnail)
    except ValueError as exc:
        if uploaded is not None and uploaded.unique_id:
            (settings.media_root / uploaded.unique_id).unlink(missing_ok=True)
        delete_modern_thumbnails(settings, media.id)
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse("/upload/success", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/upload/success", response_class=HTMLResponse)
def upload_success(request: Request):
    return render(request, "upload_success.html")


@router.get("/latest.xml")
def latest_feed(request: Request, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    if not site["rss_enabled"]:
        raise HTTPException(404)
    rows = list(
        db.scalars(
            _published_stmt().options(selectinload(Media.podcast)).order_by(Media.publish_on.desc()).limit(50)
        ).all()
    )
    base = str(request.base_url).rstrip("/")
    items = "".join(
        f"<item><title>{escape(m.title)}</title><link>{escape(base + _media_url(m))}</link>"
        f"<description>{escape(m.description_plain or '')}</description></item>"
        for m in rows
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(request.app.state.settings.app_name)}</title>{items}</channel></rss>',
        media_type="application/rss+xml; charset=utf-8",
    )


@router.get("/featured.xml")
def featured_feed(request: Request, db: Session = Db):
    return latest_feed(request, db)


@router.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Db):
    site = get_site_settings(db, request.app.state.settings)
    if not site["sitemap_enabled"]:
        raise HTTPException(404)
    rows = list(db.scalars(_published_stmt().options(selectinload(Media.podcast))).all())
    base = str(request.base_url).rstrip("/")
    urls = "".join(f"<url><loc>{escape(base + _media_url(m))}</loc></url>" for m in rows)
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        media_type="application/xml; charset=utf-8",
    )
