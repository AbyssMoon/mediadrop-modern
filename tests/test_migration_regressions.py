from __future__ import annotations

import re
from datetime import datetime

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Category, Group, Media, MediaFile, Storage, User
from app.security import verify_legacy_password


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _published_media(slug: str, title: str) -> Media:
    return Media(
        slug=slug,
        title=title,
        reviewed=True,
        encoded=True,
        publishable=True,
        publish_on=datetime.now(),
        type="video",
        author_name="",
        author_email="",
    )


def test_category_counts_use_published_media_and_nested_categories_are_obvious(app_env):
    app, client, _ = app_env
    with app.state.session_factory() as db:
        root = db.scalar(select(Category).where(Category.slug == "demos"))
        child = Category(name="Nested demos", slug="nested-demos", parent=root)
        published = _published_media("nested-published", "Nested published")
        published.categories.append(child)
        hidden = Media(
            slug="nested-hidden",
            title="Nested hidden",
            reviewed=False,
            encoded=True,
            publishable=True,
            publish_on=datetime.now(),
            type="video",
            author_name="",
            author_email="",
        )
        hidden.categories.append(child)
        db.add_all([child, published, hidden])
        db.commit()

    page = client.get("/categories")
    assert page.status_code == 200
    assert "Nested sections are shown as separate levels" in page.text
    assert "category-tree-node--nested" in page.text
    assert re.search(r"Nested demos.*?1 items", page.text, re.DOTALL)

    child_page = client.get("/categories/nested-demos")
    assert child_page.status_code == 200
    assert "Nested published" in child_page.text
    assert "Nested hidden" not in child_page.text


def test_admin_category_hierarchy_can_be_edited_and_cycles_are_rejected(logged_in):
    app, client, _ = logged_in
    with app.state.session_factory() as db:
        root = db.scalar(select(Category).where(Category.slug == "demos"))
        child = Category(name="Child", slug="child", parent=root)
        db.add(child)
        db.commit()
        root_id = root.id
        child_id = child.id

    index = client.get("/admin/categories")
    assert index.status_code == 200
    assert "category-admin-row--nested" in index.text
    assert "Child" in index.text

    edit = client.get(f"/admin/categories/{child_id}/edit")
    assert edit.status_code == 200
    token = _csrf(edit.text)
    response = client.post(
        f"/admin/categories/{child_id}/edit",
        data={
            "csrf": token,
            "name": "Moved child",
            "slug": "moved-child",
            "parent_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with app.state.session_factory() as db:
        child = db.get(Category, child_id)
        assert child.name == "Moved child"
        assert child.slug == "moved-child"
        assert child.parent_id is None
        child.parent_id = root_id
        db.commit()

    root_edit = client.get(f"/admin/categories/{root_id}/edit")
    token = _csrf(root_edit.text)
    cycle = client.post(
        f"/admin/categories/{root_id}/edit",
        data={
            "csrf": token,
            "name": "Demos",
            "slug": "demos",
            "parent_id": str(child_id),
        },
        follow_redirects=False,
    )
    assert cycle.status_code == 400


def test_admin_can_create_user_and_assign_legacy_group_permissions(logged_in):
    app, client, _ = logged_in
    with app.state.session_factory() as db:
        admin_group = db.scalar(
            select(Group)
            .where(Group.group_name == "admins")
            .options(selectinload(Group.permissions))
        )
        group_id = admin_group.id

    page = client.get("/admin/users/new")
    assert page.status_code == 200
    assert "MediaDrop permissions are inherited through legacy groups" in page.text
    token = _csrf(page.text)
    response = client.post(
        "/admin/users/new",
        data={
            "csrf": token,
            "user_name": "editor",
            "email_address": "editor@example.test",
            "display_name": "Editor",
            "password": "editor-secret",
            "group_ids": str(group_id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with app.state.session_factory() as db:
        user = db.scalar(
            select(User)
            .where(User.user_name == "editor")
            .options(selectinload(User.groups).selectinload(Group.permissions))
        )
        assert user is not None
        assert verify_legacy_password("editor-secret", user.password)
        assert {group.group_name for group in user.groups} == {"admins"}
        assert user.has_permission("edit")

    listing = client.get("/admin/users")
    assert listing.status_code == 200
    assert "editor@example.test" in listing.text
    assert "edit" in listing.text

    edit_page = client.get(response.headers["location"])
    assert edit_page.status_code == 200
    assert "editor@example.test" in edit_page.text

    client.cookies.clear()
    login = client.get("/login")
    token = _csrf(login.text)
    response = client.post(
        "/login",
        data={"csrf": token, "username": "editor", "password": "editor-secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/admin").status_code == 200


def test_user_without_legacy_permissions_cannot_open_admin(app_env):
    app, client, _ = app_env
    from app.security import hash_legacy_password

    with app.state.session_factory() as db:
        user = User(
            user_name="viewer",
            email_address="viewer@example.test",
            display_name="Viewer",
            password=hash_legacy_password("viewer-secret"),
        )
        db.add(user)
        db.commit()

    login = client.get("/login")
    token = _csrf(login.text)
    response = client.post(
        "/login",
        data={"csrf": token, "username": "viewer", "password": "viewer-secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    denied = client.get("/admin", follow_redirects=False)
    assert denied.status_code == 403


def test_legacy_media_and_thumbnails_are_read_only_on_delete(logged_in):
    app, client, _ = logged_in
    settings = app.state.settings
    legacy_file = settings.legacy_media_root / "legacy-only.mp4"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_bytes(b"legacy-media-data")

    with app.state.session_factory() as db:
        storage = db.scalar(select(Storage).where(Storage.engine_type == "LocalFileStorage"))
        media = _published_media("legacy-delete", "Legacy delete")
        db.add(media)
        db.flush()
        media_id = media.id
        db.add(
            MediaFile(
                media=media,
                storage=storage,
                type="video",
                container="mp4",
                display_name="legacy-only.mp4",
                unique_id="legacy-only.mp4",
                size=len(b"legacy-media-data"),
            )
        )
        db.commit()

    legacy_image_dir = settings.legacy_image_root / "media"
    legacy_image_dir.mkdir(parents=True, exist_ok=True)
    legacy_thumb = legacy_image_dir / f"{media_id}l.jpg"
    Image.new("RGB", (640, 360), (30, 60, 90)).save(legacy_thumb, "JPEG")

    modern_image_dir = settings.image_root / "media"
    modern_image_dir.mkdir(parents=True, exist_ok=True)
    modern_thumb = modern_image_dir / f"{media_id}m.jpg"
    Image.new("RGB", (320, 180), (90, 60, 30)).save(modern_thumb, "JPEG")

    with app.state.session_factory() as db:
        file_id = db.scalar(select(MediaFile.id).where(MediaFile.media_id == media_id))
    served = client.get(f"/files/{file_id}-legacy-delete.mp4")
    assert served.status_code == 200
    assert served.content == b"legacy-media-data"

    edit = client.get(f"/admin/media/{media_id}/edit")
    token = _csrf(edit.text)
    deleted = client.post(
        f"/admin/media/{media_id}/delete",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert legacy_file.exists()
    assert legacy_thumb.exists()
    assert not modern_thumb.exists()


def test_modern_media_file_is_removed_when_media_is_deleted(logged_in):
    app, client, media_root = logged_in
    with app.state.session_factory() as db:
        media = db.scalar(select(Media).where(Media.slug == "sample-video"))
        media_id = media.id
        media_file = db.scalar(select(MediaFile).where(MediaFile.media_id == media_id))
        modern_file = media_root / media_file.unique_id
        assert modern_file.exists()

    edit = client.get(f"/admin/media/{media_id}/edit")
    token = _csrf(edit.text)
    deleted = client.post(
        f"/admin/media/{media_id}/delete",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert not modern_file.exists()
    assert (app.state.settings.legacy_image_root / "media" / f"{media_id}m.jpg").exists()
