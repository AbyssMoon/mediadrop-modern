# MediaDrop Modern

MediaDrop Modern is a small Python 3 runtime for existing MediaDrop installations.
It keeps the legacy MediaDrop data model and file layout where that provides useful
migration compatibility, while replacing the old Python 2 / Pylons runtime with a
maintainable FastAPI application.

Current release: **0.5.4**.

The project is already functional on an imported real-world legacy database and
media library. The current automated suite contains **32 passing tests**. Remaining
work is mostly operational cleanup rather than a rewrite: converting old FLV-only
media, migrating legacy password hashes later, completing lower-priority translations,
and eventually cleaning unused legacy schema after the rollback window is closed.

Upstream project: https://github.com/mediadrop/mediadrop

## Design goals

- Preserve existing MediaDrop content and the important URL shapes.
- Import an old MediaDrop database without a complex mandatory schema migration.
- Keep the copied legacy media/images read-only during transition.
- Store new uploads separately from the migration source.
- Do not bring back the old transcoding/encoder/player framework.
- Keep the application server-rendered and simple to operate.

## Stack

- Python 3.13 (project requirement: Python >= 3.12)
- FastAPI / Starlette
- SQLAlchemy 2.x
- Jinja2 + plain CSS/JavaScript
- Uvicorn
- MariaDB 11.4 in Docker Compose
- Pillow for thumbnails
- ldap3 for LDAP / Active Directory
- cryptography for encrypted LDAP bind-password storage
- native browser `<video>` / `<audio>` playback
- pytest

There is no Node/npm frontend build.

## Current functionality

### Public site

- overview/home page with featured, popular and latest media;
- media library and search;
- hierarchical categories;
- tags;
- podcasts when enabled;
- media and podcast pages;
- comments and moderation settings;
- like/dislike controls when enabled;
- downloads when enabled;
- RSS, podcast feeds and sitemap when enabled;
- JSON API under `/api`;
- legacy-shaped media and podcast URLs where compatibility matters.

### Player

The native browser video element remains the foundation. A small enhancement toolbar
adds:

- seek -10 / +10 seconds;
- playback speed from 0.5x to 2x;
- Picture-in-Picture when supported;
- keyboard-shortcut help;
- a non-persistent wide mode on media pages.

Shortcuts: `Space`/`K` play-pause, Left/Right seek, `M` mute, `F` fullscreen and `P`
Picture-in-Picture. No player preference is stored in the database or browser storage.

### Administration

- dashboard;
- media create/edit/delete;
- upload of already browser-ready video/audio files;
- preview upload and regeneration;
- hierarchical category selection in media forms;
- comments and moderation;
- category create/edit/delete/reparent with cycle protection;
- podcast management;
- local user management through the existing legacy groups/permissions model;
- site settings;
- authentication / LDAP settings.

On new media creation:

- Slug is generated from the title and remains editable;
- server-side Unidecode normalization provides a broad-script fallback;
- author and author email default to the signed-in local/LDAP identity;
- publication date/time defaults to the current time;
- the Podcast field is hidden when podcasts are disabled.

The category picker follows the real `parent_id` hierarchy and fills visual columns
top-to-bottom before moving to the next column.

## Thumbnails

Legacy previews are read directly from the old layout:

```text
images/media/<media_id>s.jpg
images/media/<media_id>m.jpg
images/media/<media_id>l.jpg
images/media/<media_id>orig.<ext>
```

New previews use the same `s/m/l` filenames for compatibility, but are generated at
modern sizes:

- `s`: up to 320x180
- `m`: up to 960x540
- `l`: up to 1920x1080

Small source images are not upscaled. JPEG, PNG and WebP uploads are accepted.

## Languages

Fresh installations default to **English** (`LOCALE=en`). The site-wide default
language can be changed in **Admin -> Settings** and is stored in the legacy
`settings` table. The public language selector is per-browser.

Russian and English cover the current modern UI. Historical MediaDrop locales are
also exposed and fall back to English for strings not yet migrated. Arabic and Hebrew
render RTL.

## Data model and storage

MediaDrop Modern deliberately separates legacy-compatible data from modern-only
configuration.

### Legacy database

The main MariaDB database continues to use the relevant MediaDrop tables, including
media, media files, categories, tags, podcasts, comments, settings and local
users/groups/permissions.

This is the compatibility boundary that makes a straightforward legacy import possible.

### Modern sidecar database

Modern-only authentication configuration is stored separately through
`MODERN_DATABASE_URL`. Docker defaults to:

```text
/data/modern/mediadrop-modern.db
```

The sidecar currently stores settings such as mandatory sign-in, session lifetime and
LDAP configuration. It does **not** contain imported media/content and it does not
modify the legacy schema.

If the sidecar is absent on a migrated installation, it is recreated automatically
with migration-safe defaults: public access and LDAP disabled.

### Media files

Docker keeps old and new data separate:

```text
/data/legacy-media   old MediaDrop media, read-only
/data/legacy-images  old MediaDrop images, read-only
/data/media          new uploads, writable
/data/images         new previews, writable
/data/modern         modern sidecar database, writable
```

When resolving a local media file, the application checks the modern writable root and
then the legacy read-only root. Deleting modern media removes only modern files; legacy
source files are never deleted by the modern runtime.

## Authentication and LDAP

**Admin -> Authentication** supports:

- public site or mandatory sign-in;
- session-cookie lifetime of 8 hours, 1 day, 1 week or 30 days;
- LDAP enable/disable;
- `ldap://` and `ldaps://`;
- bind/service account and encrypted bind password;
- Base DN and user search base;
- user filter and username/display-name/email/group attribute names;
- optional CA certificate for internal LDAPS PKI;
- Access, Editor and Admin group mappings.

Role model:

- **Access**: may sign in and view the site when authentication is required;
- **Editor**: Access + media/comment administration and uploads;
- **Admin**: Editor + settings, authentication, users, categories and podcasts.

LDAP is tried first; if it does not authenticate the submitted credentials, the
existing local MediaDrop account is tried. This preserves local break-glass/admin
accounts. LDAP users are not synchronized into the legacy `users` table.

Direct `memberOf` values are used for group mapping; nested LDAP groups are not
expanded. The historical local group name `admins` remains full-admin compatible even
on installations where it only has the old `edit` permission.

The LDAP bind password is encrypted with a key derived from `SECRET_KEY`. Keep
`SECRET_KEY` stable when moving the sidecar database. Changing it invalidates existing
sessions and requires entering the LDAP bind password again, but does not affect
legacy content.

Typical Active Directory-shaped values:

```text
LDAP URL:               ldap://dc.example.test:389
Base DN:                DC=example,DC=test
User search base:       OU=People
User filter:            (objectCategory=Person)
Username attribute:     sAMAccountName
Display name attribute: displayName
Email attribute:        mail
Group attribute:        memberOf
Bind DN:                ldap@example.test
```

For LDAPS use `ldaps://...:636` and configure a trusted CA when the directory uses an
internal certificate authority.

## Fresh installation

```sh
cp .env.example .env
# Set DB_PASSWORD, DB_ROOT_PASSWORD and SECRET_KEY.
# COOKIE_SECURE=false is useful only for direct local HTTP testing.

docker compose build
docker compose up -d db
docker compose run --rm app mediadrop init-db
docker compose run --rm app mediadrop create-admin admin admin@example.com
docker compose up -d
```

The application binds to `127.0.0.1:8080` by default. Put nginx/Caddy or another
reverse proxy in front for HTTPS and public traffic.

`mediadrop init-db` is for a **new empty installation only**.

## Importing an existing MediaDrop installation

1. Back up the old database and data tree.
2. Restore the old SQL dump into MariaDB.
3. Do **not** run `mediadrop init-db` against the imported database.
4. Point `LEGACY_DATA_ROOT` to the copied old data tree.
5. Run the read-only checks:

```sh
docker compose run --rm app python scripts/check_legacy_db.py
docker compose run --rm app python scripts/check_legacy_thumbnails.py
docker compose run --rm app python scripts/list_unplayable_media.py
```

6. Start the application and verify representative content/admin workflows.

See [MIGRATION_NOTES.md](MIGRATION_NOTES.md) for the detailed migration and FLV cleanup
strategy.

## FLV and other unsupported legacy media

The modern runtime intentionally does not include Flash or an automatic transcoding
pipeline. Use the inventory helper to find imported media that have no browser-ready
variant:

```sh
docker compose run --rm app python scripts/list_unplayable_media.py
```

For FLV-only content, keep the original legacy file unchanged, convert a copy offline
to MP4/H.264/AAC, validate it with `ffprobe`/`ffmpeg`, and attach the new MP4 to the
same media item through Admin. The new file is written to `/data/media`; the old FLV
stays read-only in `/data/legacy-media`. When both are present, the modern player uses
the browser-ready variant.

This keeps the legacy source intact and makes rollback straightforward.

## Updating an existing MediaDrop Modern installation

Do not run `init-db` again. Back up `.env` and the databases/volumes, replace the
source tree, rebuild and inspect logs:

```sh
cp .env .env.backup
docker compose build
docker compose up -d
docker compose logs --tail=100 app
```

Release 0.5.4 does not require a legacy schema or modern-sidecar schema migration.

## Development and checks

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
uvicorn app.main:app --reload
```

Current release verification:

```text
32 passed
```

Additional useful checks:

```sh
python -m compileall app scripts
node --check app/static/player.js
node --check app/static/admin-media.js
```

## Known follow-up work

These are intentionally not blockers for committing or operating the current runtime:

- convert remaining FLV-only legacy media to MP4 offline;
- after the rollback window, replace legacy SHA1 password storage with Argon2id
  rehash-on-login and a controlled schema migration;
- finish historical translations beyond Russian/English;
- optionally switch high-volume media delivery to nginx `X-Accel-Redirect`;
- only after migration confidence is high, inventory and remove truly unused legacy
  encoder/player/fulltext schema.

## Security notes

- Never commit `.env` or real credentials.
- Use a long random `SECRET_KEY` and database passwords.
- Use HTTPS in production and keep `COOKIE_SECURE=true` there.
- Keep copied legacy media/images read-only during migration.
- Do not expose Uvicorn directly to the Internet unless intentionally protected.
- Legacy SHA1 password support exists only for transition compatibility.

## License

GPL-3.0-or-later, matching upstream MediaDrop.
