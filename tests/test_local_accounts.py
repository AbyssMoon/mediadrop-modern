from __future__ import annotations

import re

from sqlalchemy import select

from app.local_accounts import has_modern_password, is_local_user_enabled, set_local_user_enabled
from app.models import User
from app.security import verify_legacy_password


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_legacy_local_login_lazily_creates_argon2_hash(app_env):
    app, client, _ = app_env
    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.user_name == "admin"))
        assert user is not None
        assert verify_legacy_password("secret", user.password)
        user_id = user.id

    with app.state.modern_session_factory() as modern_db:
        assert has_modern_password(modern_db, user_id) is False

    page = client.get("/login")
    response = client.post(
        "/login",
        data={"csrf": _csrf(page.text), "username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with app.state.modern_session_factory() as modern_db:
        assert has_modern_password(modern_db, user_id) is True


def test_disabled_local_user_cannot_login(app_env):
    app, client, _ = app_env
    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.user_name == "admin"))
        user_id = user.id
    with app.state.modern_session_factory() as modern_db:
        set_local_user_enabled(modern_db, user_id, False)
        modern_db.commit()
        assert is_local_user_enabled(modern_db, user_id) is False

    page = client.get("/login")
    response = client.post(
        "/login",
        data={"csrf": _csrf(page.text), "username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_disabling_user_invalidates_existing_local_session(logged_in):
    app, client, _ = logged_in
    assert client.get("/admin").status_code == 200

    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.user_name == "admin"))
        user_id = user.id
    with app.state.modern_session_factory() as modern_db:
        set_local_user_enabled(modern_db, user_id, False)
        modern_db.commit()

    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_disabled_session_cannot_bypass_require_login_gate(logged_in):
    from app.auth_config import AuthSettings, load_auth_settings, save_auth_settings

    app, client, _ = logged_in
    with app.state.modern_session_factory() as modern_db:
        current = load_auth_settings(modern_db, app.state.settings)
        save_auth_settings(
            modern_db,
            app.state.settings,
            AuthSettings(**{**current.__dict__, "require_login": True}),
        )
        modern_db.commit()

    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.user_name == "admin"))
        user_id = user.id
    with app.state.modern_session_factory() as modern_db:
        set_local_user_enabled(modern_db, user_id, False)
        modern_db.commit()

    response = client.get("/media", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
