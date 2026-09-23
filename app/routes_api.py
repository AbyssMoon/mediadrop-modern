from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import audit_event
from app.database import db_dependency
from app.models import Category, Media, MediaFile
from app.security import current_principal, validate_csrf

router = APIRouter(prefix="/api", tags=["api"])
Db = Depends(db_dependency)


def media_dict(media: Media) -> dict:
    return {
        "id": media.id,
        "slug": media.slug,
        "title": media.title,
        "description": media.description_plain,
        "type": media.type,
        "publish_on": media.publish_on.isoformat() if media.publish_on else None,
        "views": media.views,
        "likes": media.likes,
        "dislikes": media.dislikes,
        "files": [
            {
                "id": f.id,
                "type": f.type,
                "container": f.container,
                "display_name": f.display_name,
                "size": f.size,
            }
            for f in media.files
        ],
    }


@router.get("/media")
@router.get("/media/index")
def media_index(db: Session = Db):
    rows = db.scalars(
        select(Media)
        .where(Media.published_clause())
        .options(selectinload(Media.files))
        .order_by(Media.publish_on.desc())
        .limit(100)
    ).all()
    return {"media": [media_dict(m) for m in rows]}


@router.get("/media/{media_id:int}")
def media_detail(media_id: int, db: Session = Db):
    media = db.scalar(
        select(Media)
        .where(Media.id == media_id, Media.published_clause())
        .options(selectinload(Media.files))
    )
    if media is None:
        raise HTTPException(404)
    return media_dict(media)


@router.post("/media/{media_id:int}/play", status_code=status.HTTP_204_NO_CONTENT)
def media_play(media_id: int, request: Request, db: Session = Db):
    """Audit a real playback start reported by the HTML5 player.

    The browser sends this only after five cumulative seconds in the
    ``playing`` state and only once per rendered page.  This endpoint does
    not increment the legacy page-view counter; it is an audit event only.
    """
    validate_csrf(request, request.headers.get("x-csrf-token"))
    media = db.scalar(select(Media).where(Media.id == media_id, Media.published_clause()))
    if media is None:
        raise HTTPException(404)

    principal = current_principal(request, db)
    audit_event(
        request,
        "media.play",
        principal=principal,
        object_type="media",
        object_id=media.id,
        details={"slug": media.slug, "title": media.title},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/categories")
@router.get("/categories/index")
def categories(db: Session = Db):
    rows = db.scalars(select(Category).order_by(Category.name)).all()
    return {"categories": [{"id": c.id, "name": c.name, "slug": c.slug, "parent_id": c.parent_id} for c in rows]}
