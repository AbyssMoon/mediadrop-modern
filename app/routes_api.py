from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import db_dependency
from app.models import Category, Media, MediaFile

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


@router.get("/categories")
@router.get("/categories/index")
def categories(db: Session = Db):
    rows = db.scalars(select(Category).order_by(Category.name)).all()
    return {"categories": [{"id": c.id, "name": c.name, "slug": c.slug, "parent_id": c.parent_id} for c in rows]}
