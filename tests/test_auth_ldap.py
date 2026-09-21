from __future__ import annotations

import re

from sqlalchemy import inspect, select

from app.auth_config import AuthSettings, load_auth_settings, save_auth_settings, validate_auth_settings
from app.ldap_auth import LdapIdentity, _group_names, _permissions_for_groups
from app.models import Group, Permission, User
from app.modern_database import ModernSetting
from app.security import hash_legacy_password


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _enable_auth(app, **changes):
    with app.state.modern_session_factory() as db:
        current = load_auth_settings(db, app.state.settings)
        values = AuthSettings(**{**current.__dict__, **changes})
        save_auth_settings(db, app.state.settings, values)
        db.commit()


def test_modern_auth_settings_use_separate_sidecar_database(app_env):
    app, client, _ = app_env
    assert client.get("/").status_code == 200
    assert "modern_settings" in inspect(app.state.modern_engine).get_table_names()
    assert "modern_settings" not in inspect(app.state.engine).get_table_names()


def test_auth_settings_encrypt_bind_password_and_change_cookie_ttl(logged_in):
    app, client, _ = logged_in
    page = client.get("/admin/settings/auth")
    assert page.status_code == 200
    token = _csrf(page.text)
    response = client.post(
        "/admin/settings/auth",
        data={
            "csrf": token,
            "require_login": "on",
            "session_ttl_seconds": str(8 * 60 * 60),
            "ldap_enabled": "on",
            "ldap_url": "ldap://dc.example.test:389",
            "ldap_bind_dn": "ldap@example.test",
            "ldap_bind_password": "bind-secret",
            "ldap_base_dn": "DC=example,DC=test",
            "ldap_user_base_dn": "OU=People",
            "ldap_user_filter": "(objectCategory=Person)",
            "ldap_username_attribute": "sAMAccountName",
            "ldap_display_name_attribute": "displayName",
            "ldap_email_attribute": "mail",
            "ldap_member_of_attribute": "memberOf",
            "ldap_access_group": "MediaDrop Users",
            "ldap_editor_group": "MediaDrop Editors",
            "ldap_admin_group": "MediaDrop Admins",
            "ldap_ca_cert_file": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with app.state.modern_session_factory() as db:
        raw_password = db.scalar(
            select(ModernSetting.value).where(ModernSetting.key == "ldap.bind_password")
        )
        assert raw_password
        assert raw_password != "bind-secret"
        values = load_auth_settings(db, app.state.settings)
        assert values.ldap_bind_password == "bind-secret"
        assert values.session_ttl_seconds == 28800
        assert values.require_login is True

    # The next response is signed with the newly configured cookie lifetime.
    page = client.get("/admin/settings/auth")
    assert page.status_code == 200
    assert "Max-Age=28800" in page.headers.get("set-cookie", "")


def test_require_login_redirects_public_pages_but_not_health(app_env):
    app, client, _ = app_env
    _enable_auth(app, require_login=True)
    client.cookies.clear()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert client.get("/healthz").status_code == 200
    api = client.get("/api/media", follow_redirects=False)
    assert api.status_code == 401

    login = client.get("/login?next=%2F")
    token = _csrf(login.text)
    response = client.post(
        "/login",
        data={"csrf": token, "username": "admin", "password": "secret", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_ldap_is_tried_before_duplicate_local_account_and_local_is_fallback(app_env, monkeypatch):
    app, client, _ = app_env
    _enable_auth(
        app,
        ldap_enabled=True,
        ldap_url="ldap://dc.example.test:389",
        ldap_base_dn="DC=example,DC=test",
        ldap_access_group="MediaDrop Users",
        ldap_editor_group="MediaDrop Editors",
    )

    calls = []

    def fake_ldap(_settings, username, password):
        calls.append((username, password))
        if username == "admin" and password == "ldap-secret":
            return LdapIdentity(
                username="admin",
                display_name="LDAP Admin Duplicate",
                email="admin@directory.test",
                groups=("MediaDrop Editors",),
                permissions=frozenset({"edit"}),
            )
        return None

    monkeypatch.setattr("app.routes_auth.authenticate_ldap", fake_ldap)

    page = client.get("/login")
    token = _csrf(page.text)
    ldap_login = client.post(
        "/login",
        data={"csrf": token, "username": "admin", "password": "ldap-secret"},
        follow_redirects=False,
    )
    assert ldap_login.status_code == 303
    assert ldap_login.headers["location"] == "/admin"
    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "LDAP" in dashboard.text
    media_form = client.get("/admin/media/new")
    assert 'value="LDAP Admin Duplicate"' in media_form.text
    assert 'value="admin@directory.test"' in media_form.text
    assert calls == [("admin", "ldap-secret")]

    client.cookies.clear()
    page = client.get("/login")
    token = _csrf(page.text)
    local_login = client.post(
        "/login",
        data={"csrf": token, "username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert local_login.status_code == 303
    assert client.get("/admin").status_code == 200
    assert calls[-1] == ("admin", "secret")


def test_editor_role_can_manage_media_but_not_admin_settings(app_env):
    app, client, _ = app_env
    with app.state.session_factory() as db:
        edit = db.scalar(select(Permission).where(Permission.permission_name == "edit"))
        editors = Group(group_name="editors", display_name="Editors")
        editors.permissions.append(edit)
        editor = User(
            user_name="editor",
            email_address="editor@example.test",
            display_name="Editor",
            password=hash_legacy_password("editor-secret"),
        )
        editor.groups.append(editors)
        db.add(editor)
        db.commit()

    page = client.get("/login")
    token = _csrf(page.text)
    response = client.post(
        "/login",
        data={"csrf": token, "username": "editor", "password": "editor-secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/admin/media").status_code == 200
    assert client.get("/admin/comments").status_code == 200
    assert client.get("/admin/settings", follow_redirects=False).status_code == 403
    assert client.get("/admin/users", follow_redirects=False).status_code == 403
    assert client.get("/admin/categories", follow_redirects=False).status_code == 403


def test_ldap_viewer_can_authenticate_without_admin_permissions(app_env, monkeypatch):
    app, client, _ = app_env
    _enable_auth(
        app,
        require_login=True,
        ldap_enabled=True,
        ldap_url="ldaps://dc.example.test:636",
        ldap_base_dn="DC=example,DC=test",
        ldap_access_group="MediaDrop Users",
    )

    def fake_ldap(_settings, username, password):
        if username == "viewer" and password == "viewer-secret":
            return LdapIdentity(
                username="viewer",
                display_name="Directory Viewer",
                email="viewer@example.test",
                groups=("MediaDrop Users",),
                permissions=frozenset(),
            )
        return None

    monkeypatch.setattr("app.routes_auth.authenticate_ldap", fake_ldap)
    login = client.get("/login?next=%2F")
    token = _csrf(login.text)
    response = client.post(
        "/login",
        data={"csrf": token, "username": "viewer", "password": "viewer-secret", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/").status_code == 200
    assert client.get("/admin", follow_redirects=False).status_code == 403
    assert "Log out" in client.get("/").text


def test_ldap_and_ldaps_urls_are_valid_and_group_cn_is_extracted():
    base = AuthSettings(
        ldap_enabled=True,
        ldap_url="ldap://dc.example.test:389",
        ldap_base_dn="DC=example,DC=test",
    )
    validate_auth_settings(base)
    validate_auth_settings(AuthSettings(**{**base.__dict__, "ldap_url": "ldaps://dc.example.test:636"}))
    groups = _group_names([
        "CN=MediaDrop Editors,OU=Groups,DC=example,DC=test",
        "CN=MediaDrop Admins,OU=Groups,DC=example,DC=test",
    ])
    assert "MediaDrop Editors" in groups
    assert "MediaDrop Admins" in groups


def test_ldap_group_role_mapping():
    settings = AuthSettings(
        ldap_access_group="MediaDrop Users",
        ldap_editor_group="MediaDrop Editors",
        ldap_admin_group="MediaDrop Admins",
    )
    assert _permissions_for_groups(settings, ("MediaDrop Users",)) == frozenset()
    assert _permissions_for_groups(settings, ("MediaDrop Editors",)) == frozenset({"edit"})
    assert _permissions_for_groups(settings, ("MediaDrop Admins",)) == frozenset({"edit", "admin"})
    assert _permissions_for_groups(settings, ("Other Group",)) is None


def test_authenticate_ldap_uses_service_search_user_bind_and_group_mapping(monkeypatch):
    import ssl
    import sys
    import types

    from app.ldap_auth import authenticate_ldap

    calls: list[dict] = []
    searches: list[dict] = []
    servers: list[object] = []

    class FakeAttribute:
        def __init__(self, values):
            self.values = values

    class FakeEntry:
        entry_dn = "CN=Alice,OU=People,DC=example,DC=test"
        attrs = {
            "sAMAccountName": ["alice"],
            "displayName": ["Alice Example"],
            "mail": ["alice@example.test"],
            "memberOf": ["CN=MediaDrop Admins,OU=Groups,DC=example,DC=test"],
        }

        def __getitem__(self, key):
            return FakeAttribute(self.attrs.get(key, []))

    class FakeTls:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeServer:
        def __init__(self, host, **kwargs):
            self.host = host
            self.kwargs = kwargs
            servers.append(self)

    class FakeConnection:
        def __init__(self, server, **kwargs):
            self.server = server
            self.kwargs = kwargs
            self.entries = []
            calls.append(kwargs)

        def search(self, **kwargs):
            searches.append(kwargs)
            self.entries = [FakeEntry()]
            return True

        def unbind(self):
            return True

    fake_ldap3 = types.SimpleNamespace(
        AUTO_BIND_NO_TLS="AUTO_BIND_NO_TLS",
        NONE="NONE",
        Connection=FakeConnection,
        Server=FakeServer,
        Tls=FakeTls,
    )
    monkeypatch.setitem(sys.modules, "ldap3", fake_ldap3)

    settings = AuthSettings(
        ldap_enabled=True,
        ldap_url="ldaps://dc.example.test:636",
        ldap_bind_dn="ldap@example.test",
        ldap_bind_password="bind-password",
        ldap_base_dn="DC=example,DC=test",
        ldap_user_base_dn="OU=People",
        ldap_user_filter="(objectCategory=Person)",
        ldap_access_group="MediaDrop Users",
        ldap_editor_group="MediaDrop Editors",
        ldap_admin_group="MediaDrop Admins",
    )
    identity = authenticate_ldap(settings, "alice*)(", "user-password")
    assert identity is not None
    assert identity.username == "alice"
    assert identity.permissions == frozenset({"edit", "admin"})
    assert len(calls) == 2
    assert calls[0]["user"] == "ldap@example.test"
    assert calls[0]["password"] == "bind-password"
    assert calls[1]["user"] == FakeEntry.entry_dn
    assert calls[1]["password"] == "user-password"
    assert searches[0]["search_base"] == "OU=People,DC=example,DC=test"
    assert "alice\\2a\\29\\28" in searches[0]["search_filter"]
    assert servers[0].host == "dc.example.test"
    assert servers[0].kwargs["port"] == 636
    assert servers[0].kwargs["use_ssl"] is True
    assert servers[0].kwargs["tls"].kwargs["validate"] == ssl.CERT_REQUIRED


def test_auth_settings_layout_and_product_copy_are_localized(logged_in):
    _app, client, _ = logged_in

    client.cookies.set("mediadrop_locale", "ru")
    page = client.get("/admin/settings/auth")
    assert page.status_code == 200
    assert page.text.count('class="settings-section__body"') == 4
    assert "Аутентификация и LDAP" in page.text
    assert "Атрибут имени пользователя" in page.text
    assert "Базовый DN каталога" in page.text
    assert "вашему приведённому конфигу" not in page.text
    assert "legacy-схем" not in page.text

    client.cookies.set("mediadrop_locale", "en")
    page = client.get("/admin/settings/auth")
    assert page.status_code == 200
    assert "Authentication &amp; LDAP" in page.text or "Authentication & LDAP" in page.text
    assert "Username attribute" in page.text
    assert "Directory base DN" in page.text
    assert "Groups and roles" in page.text
    assert "configuration you provided" not in page.text
    assert "legacy schema" not in page.text
