from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.config import get_settings
from app.database import build_session_factory
from app.local_accounts import set_local_user_enabled, set_modern_password
from app.models import Base, Group, Permission, User
from app.modern_database import build_modern_session_factory
from app.security import hash_legacy_password


def init_db() -> None:
    settings = get_settings()
    engine, _ = build_session_factory(settings)
    Base.metadata.create_all(engine)
    print("Database tables created. Use this only for a fresh database.")


def create_admin(username: str, email: str, password: str | None = None) -> None:
    settings = get_settings()
    engine, factory = build_session_factory(settings)
    modern_engine, modern_factory = build_modern_session_factory(settings)
    password = password or getpass.getpass("Password: ")
    with factory() as db:
        user = db.scalar(select(User).where(User.user_name == username))
        if user is not None:
            raise SystemExit(f"User {username!r} already exists")
        permissions = []
        for name, description in (("edit", "Edit MediaDrop content"), ("admin", "Administer MediaDrop")):
            permission = db.scalar(select(Permission).where(Permission.permission_name == name))
            if permission is None:
                permission = Permission(permission_name=name, description=description)
                db.add(permission)
                db.flush()
            permissions.append(permission)
        group = db.scalar(select(Group).where(Group.group_name == "admins"))
        if group is None:
            group = Group(group_name="admins", display_name="Administrators")
            db.add(group)
            db.flush()
        for permission in permissions:
            if permission not in group.permissions:
                group.permissions.append(permission)
        user = User(
            user_name=username,
            email_address=email,
            display_name=username,
            password=hash_legacy_password(password),
        )
        user.groups.append(group)
        db.add(user)
        db.flush()
        with modern_factory() as modern_db:
            set_modern_password(modern_db, user.id, password)
            set_local_user_enabled(modern_db, user.id, True)
            modern_db.commit()
        db.commit()
    engine.dispose()
    modern_engine.dispose()
    print(f"Created admin user {username!r} with Argon2id authentication.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MediaDrop modern administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Create tables in a new empty database")
    admin = sub.add_parser("create-admin", help="Create a legacy-compatible admin user")
    admin.add_argument("username")
    admin.add_argument("email")
    admin.add_argument("--password")
    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
    elif args.command == "create-admin":
        create_admin(args.username, args.email, args.password)


if __name__ == "__main__":
    main()
