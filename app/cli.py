from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.config import get_settings
from app.database import build_session_factory
from app.models import Base, Group, Permission, User
from app.security import hash_legacy_password


def init_db() -> None:
    settings = get_settings()
    engine, _ = build_session_factory(settings)
    Base.metadata.create_all(engine)
    print("Database tables created. Use this only for a fresh database.")


def create_admin(username: str, email: str, password: str | None = None) -> None:
    settings = get_settings()
    engine, factory = build_session_factory(settings)
    password = password or getpass.getpass("Password: ")
    with factory() as db:
        user = db.scalar(select(User).where(User.user_name == username))
        if user is not None:
            raise SystemExit(f"User {username!r} already exists")
        permission = db.scalar(select(Permission).where(Permission.permission_name == "edit"))
        if permission is None:
            permission = Permission(permission_name="edit", description="Edit MediaDrop content")
            db.add(permission)
            db.flush()
        group = db.scalar(select(Group).where(Group.group_name == "admins"))
        if group is None:
            group = Group(group_name="admins", display_name="Administrators")
            db.add(group)
            db.flush()
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
        db.commit()
    engine.dispose()
    print(f"Created admin user {username!r}.")


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
