from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    and_,
    or_,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


media_tags = Table(
    "media_tags",
    Base.metadata,
    Column("media_id", ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

media_categories = Table(
    "media_categories",
    Base.metadata,
    Column("media_id", ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)

users_groups = Table(
    "users_groups",
    Base.metadata,
    Column("user_id", ForeignKey("users.user_id", ondelete="CASCADE")),
    Column("group_id", ForeignKey("groups.group_id", ondelete="CASCADE")),
)

groups_permissions = Table(
    "groups_permissions",
    Base.metadata,
    Column("group_id", ForeignKey("groups.group_id", ondelete="CASCADE")),
    Column("permission_id", ForeignKey("permissions.permission_id", ondelete="CASCADE")),
)


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    value: Mapped[Optional[str]] = mapped_column(Text)


class Storage(Base):
    __tablename__ = "storage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engine_type: Mapped[str] = mapped_column(String(30), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_on: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    modified_on: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    # Legacy MediaDrop stores JSON in a TEXT column.
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    files: Mapped[list[MediaFile]] = relationship(back_populates="storage")


class Podcast(Base):
    __tablename__ = "podcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    created_on: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    modified_on: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    subtitle: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(50))
    author_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    author_email: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    explicit: Mapped[Optional[bool]] = mapped_column(Boolean)
    copyright: Mapped[Optional[str]] = mapped_column(String(50))
    itunes_url: Mapped[Optional[str]] = mapped_column(String(80))
    feedburner_url: Mapped[Optional[str]] = mapped_column(String(80))

    media: Mapped[list[Media]] = relationship(back_populates="podcast")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    media: Mapped[list[Media]] = relationship(secondary=media_tags, back_populates="tags")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=True
    )

    parent: Mapped[Optional[Category]] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Category]] = relationship(back_populates="parent")
    media: Mapped[list[Media]] = relationship(
        secondary=media_categories, back_populates="categories"
    )


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[Optional[str]] = mapped_column(String(8))
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    podcast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("podcasts.id", ondelete="SET NULL"), nullable=True
    )
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    encoded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_on: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    modified_on: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    publish_on: Mapped[Optional[datetime]] = mapped_column(DateTime)
    publish_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    description_plain: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dislikes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    popularity_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    popularity_likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    popularity_dislikes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    author_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    author_email: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    podcast: Mapped[Optional[Podcast]] = relationship(back_populates="media")
    files: Mapped[list[MediaFile]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=media_tags, back_populates="media")
    categories: Mapped[list[Category]] = relationship(
        secondary=media_categories, back_populates="media"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )

    @classmethod
    def published_clause(cls):
        now = datetime.now()
        return and_(
            cls.reviewed.is_(True),
            cls.encoded.is_(True),
            cls.publishable.is_(True),
            cls.publish_on.is_not(None),
            cls.publish_on <= now,
            or_(cls.publish_until.is_(None), cls.publish_until >= now),
        )

    @property
    def is_published(self) -> bool:
        now = datetime.now()
        return bool(
            self.reviewed
            and self.encoded
            and self.publishable
            and self.publish_on
            and self.publish_on <= now
            and (self.publish_until is None or self.publish_until >= now)
        )


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    storage_id: Mapped[int] = mapped_column(ForeignKey("storage.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    container: Mapped[Optional[str]] = mapped_column(String(10))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    unique_id: Mapped[Optional[str]] = mapped_column(String(255))
    size: Mapped[Optional[int]] = mapped_column(Integer)
    created_on: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    modified_on: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    bitrate: Mapped[Optional[int]] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)

    media: Mapped[Media] = relationship(back_populates="files")
    storage: Mapped[Storage] = relationship(back_populates="files")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    subject: Mapped[Optional[str]] = mapped_column(String(100))
    created_on: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    modified_on: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    author_name: Mapped[str] = mapped_column(String(50), nullable=False)
    author_email: Mapped[Optional[str]] = mapped_column(String(255))
    author_ip: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    media: Mapped[Optional[Media]] = relationship(back_populates="comments")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column("permission_id", Integer, primary_key=True, autoincrement=True)
    permission_name: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))

    groups: Mapped[list[Group]] = relationship(
        secondary=groups_permissions, back_populates="permissions"
    )


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column("group_id", Integer, primary_key=True, autoincrement=True)
    group_name: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    created: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.now)

    users: Mapped[list[User]] = relationship(secondary=users_groups, back_populates="groups")
    permissions: Mapped[list[Permission]] = relationship(
        secondary=groups_permissions, back_populates="groups"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column("user_id", Integer, primary_key=True, autoincrement=True)
    user_name: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    email_address: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    password: Mapped[Optional[str]] = mapped_column(String(80))
    created: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.now)

    groups: Mapped[list[Group]] = relationship(secondary=users_groups, back_populates="users")

    def has_permission(self, name: str) -> bool:
        return any(p.permission_name == name for group in self.groups for p in group.permissions)
