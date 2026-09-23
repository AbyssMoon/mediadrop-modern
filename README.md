# MediaDrop Modern

MediaDrop Modern is a Python 3 / FastAPI runtime for existing MediaDrop installations.
It keeps the legacy MediaDrop content schema and file layout where that helps migration,
while replacing the old Python 2 / Pylons runtime with a small maintainable application.

Current release: **0.6.6**.  
Current automated suite: **43 passing tests**.

For production installation and storage mapping, start with:

- [Развёртывание и реальные Docker mounts](docs/DEPLOYMENT_RU.md)
- [Эксплуатация, backup/restore и upgrade](docs/OPERATIONS_RU.md)
- [Migration notes](MIGRATION_NOTES.md)
- [Краткий контекст проекта](START_HERE_RU.txt)

Upstream historical project: <https://github.com/mediadrop/mediadrop>

## What the project preserves

The compatibility boundary is intentionally narrow:

- the useful legacy MediaDrop MariaDB tables for media, files, categories, tags,
  podcasts, comments, settings and local users/groups/permissions;
- legacy local-file `unique_id` references;
- legacy preview layout;
- important public URL shapes;
- publication/review semantics that matter for imported content.

The project intentionally does **not** revive the old Python 2 framework, Flash/RTMP,
transcoding queues, encoder profiles or the legacy plugin/player runtime.

## Stack

- Python 3.13; project requirement Python >= 3.12
- FastAPI / Starlette
- SQLAlchemy 2.x
- Jinja2 + plain CSS/JavaScript
- Uvicorn
- MariaDB 11.4 in Docker Compose
- SQLite sidecar for modern-only configuration
- Pillow
- ldap3 + cryptography
- Argon2id local-account authentication
- native browser `<video>` / `<audio>` playback
- pytest

There is no Node/npm frontend build.

## Current functionality

### Public site

- home/overview;
- media library and search;
- hierarchical categories;
- tags;
- podcasts when enabled;
- media and podcast pages;
- comments and moderation settings;
- like/dislike when enabled;
- downloads when enabled;
- RSS/podcast feeds and sitemap when enabled;
- JSON API under `/api`;
- compatibility URL shapes where needed.

Authenticated comments use the signed-in identity automatically: Name and Email are
pre-filled and read-only, and the backend overwrites submitted identity fields with
the authenticated principal. Anonymous visitors retain editable fields when anonymous
site access is allowed.

### Player

The browser-native media element remains the base player. The custom toolbar adds:

- seek -10 / +10 seconds;
- playback speed 0.5x .. 2x;
- mute/unmute;
- fullscreen;
- Picture-in-Picture when supported;
- wide mode on media pages;
- keyboard-shortcut help.

Shortcuts: `Space`/`K` play-pause, Left/Right seek, `M` mute, `F` fullscreen,
`P` Picture-in-Picture.

When the site setting disables downloads, video elements receive
`controlslist="nodownload"`. Supporting browsers hide the native Download action.
This is UI control, not DRM: a browser still has to receive media bytes to play them.

Since 0.6.4, `/files/*` uses a function-scoped DB dependency. Rapid HTML5 Range/seek
requests therefore do not hold SQLAlchemy connections for the duration of file transfer.

### Administration

- dashboard;
- media create/edit/delete;
- browser-ready video/audio upload;
- preview upload and regeneration;
- hierarchical category selection;
- comment moderation;
- category create/edit/delete/reparent with cycle protection;
- podcast management;
- local user management through legacy groups/permissions;
- site settings;
- authentication and LDAP settings.

On new media creation the slug remains editable, author/name defaults come from the
signed-in identity, publication time defaults to now, and podcast fields follow the
site feature setting.

## Storage model

MediaDrop Modern deliberately separates the imported archive from data created by the
new runtime.

Default production host mapping:

| Host | Container | Purpose | Mode |
| --- | --- | --- | --- |
| `/opt/mediadrop-modern-data/media` | `/data/media` | new media | rw |
| `/opt/mediadrop-modern-data/images` | `/data/images` | new previews | rw |
| `/opt/mediadrop-modern-data/modern` | `/data/modern` | modern SQLite | rw |
| `/opt/mediadrop-modern-data/logs` | `/data/logs` | audit logs | rw |
| `/opt/mediadrop_data/media` | `/data/legacy-media` | old media | **ro** |
| `/opt/mediadrop_data/images` | `/data/legacy-images` | old images | **ro** |
| Docker named volume | `/var/lib/mysql` in `db` | MariaDB | rw |

The host roots are configurable in `.env`:

```dotenv
COMPOSE_PROJECT_NAME=mediadrop-modern
MODERN_DATA_ROOT=/opt/mediadrop-modern-data
LEGACY_DATA_ROOT=/opt/mediadrop_data
```

`COMPOSE_PROJECT_NAME` is intentionally stable because it determines the MariaDB named
volume name. With the default value the volume is:

```text
mediadrop-modern_mediadrop_db
```

Do not use `docker compose down -v` for a routine upgrade.

See [docs/DEPLOYMENT_RU.md](docs/DEPLOYMENT_RU.md) for the complete mount/permissions
procedure and verification commands.

## Databases

### Legacy-compatible MariaDB

The main database continues to use the relevant MediaDrop tables. Imported content and
new MediaDrop content live there, preserving the compatibility boundary.

### Modern sidecar SQLite

Modern-only configuration uses:

```text
MODERN_DATABASE_URL=sqlite:////data/modern/mediadrop-modern.db
```

With the default bind mount the physical host file is:

```text
/opt/mediadrop-modern-data/modern/mediadrop-modern.db
```

It stores modern auth/LDAP/account state and does not replace the legacy content DB.
Keep `SECRET_KEY` stable when moving/restoring it because the saved LDAP bind password
is encrypted with a key derived from `SECRET_KEY`.

## Thumbnails

Legacy previews are read directly from the old layout:

```text
images/media/<media_id>s.jpg
images/media/<media_id>m.jpg
images/media/<media_id>l.jpg
images/media/<media_id>orig.<ext>
```

New previews use the same compatible names with modern limits:

- `s`: up to 320x180
- `m`: up to 960x540
- `l`: up to 1920x1080

Small sources are not upscaled. JPEG, PNG and WebP are accepted.

## Authentication and LDAP

Admin -> Authentication supports:

- public site or mandatory sign-in;
- session lifetime 8 hours / 1 day / 1 week / 30 days;
- LDAP enable/disable;
- `ldap://` and `ldaps://`;
- service/bind account with encrypted password;
- Base DN and user search base;
- configurable filter and attribute names;
- internal CA certificate for LDAPS;
- Access, Editor and Admin group mappings.

Authentication order is LDAP first, then the local MediaDrop account. This preserves
local break-glass/admin accounts. LDAP identities are not synchronized into the legacy
`users` table.

Local passwords are authenticated with Argon2id. Existing legacy SHA-1 credentials are
upgraded lazily after successful login while the historical hash remains available for
the rollback window.

Role model:

- **Access**: sign in and view when authentication is required;
- **Editor**: Access + media/comment administration and uploads;
- **Admin**: Editor + settings, authentication, users, categories and podcasts.

Direct `memberOf` values are used for LDAP group mapping; nested groups are not expanded.

## Fresh installation

Copy and edit the environment file first:

```sh
cp .env.example .env
```

Set at least `DB_PASSWORD`, `DB_ROOT_PASSWORD` and `SECRET_KEY`. Verify the host storage
paths before starting containers.

For a **new empty** database only:

```sh
docker compose build
docker compose up -d db
docker compose run --rm app mediadrop init-db
docker compose run --rm app mediadrop create-admin admin admin@example.com
docker compose up -d
```

The app binds to `127.0.0.1:8080` by default. If the HTTPS reverse proxy runs on a
separate host, set `BIND_ADDR` to the private interface address reachable by that proxy
(or use `0.0.0.0` with a strict firewall). See `docs/DEPLOYMENT_RU.md`.

Do **not** run `mediadrop init-db` on an imported MediaDrop database.

## Legacy migration

For an existing MediaDrop installation:

1. back up the SQL dump and legacy file tree;
2. restore the legacy SQL to MariaDB;
3. put old `media/` and `images/` below `LEGACY_DATA_ROOT`;
4. keep those mounts read-only;
5. do not run `mediadrop init-db`;
6. run the read-only validation helpers;
7. start and smoke-test representative content.

Validation:

```sh
docker compose run --rm app python scripts/check_legacy_db.py
docker compose run --rm app python scripts/check_legacy_thumbnails.py
docker compose run --rm app python scripts/list_unplayable_media.py
```

For old FLV-only content use the offline migration helper described in
`MIGRATION_NOTES.md`; the legacy FLV source remains untouched until the rollback window
is closed.

## Reverse proxy and media delivery

Default:

```dotenv
MEDIA_SERVE_MODE=app
```

Keep this mode when nginx is an external reverse proxy and does not share the app's
filesystem. The app handles Range requests and nginx proxies the response.

Use `MEDIA_SERVE_MODE=nginx` only when nginx can read the media filesystem itself and
is explicitly configured for `X-Accel-Redirect`.

This distinction matters in deployments with a separate corporate nginx host: a
remote reverse proxy cannot resolve container paths such as `/data/media` by itself.

## Backup and restore

`scripts/backup.sh` runs on the Docker host and backs up:

- MariaDB as `mariadb.sql.gz`;
- modern SQLite using the SQLite backup API;
- new `/data/media` and `/data/images` as `files.tar.gz`;
- `.env` and `compose.yml`;
- SHA256 manifest.

The large read-only legacy archive is intentionally not duplicated by that script and
must have independent retention/backup.

See [docs/OPERATIONS_RU.md](docs/OPERATIONS_RU.md).

## Development

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Current expected result:

```text
43 passed
```

Additional checks used for releases:

```sh
python -m compileall app scripts
node --check app/static/player.js
node --check app/static/admin-media.js
```

## Release building

Use the release helper so runtime DBs, logs, secrets and caches are excluded:

```sh
./scripts/build_release.sh
```

Artifacts are written to `dist/` with SHA256 files.

## Upgrade safety checklist

Before replacing code:

```text
[ ] backup completed successfully
[ ] .env preserved
[ ] COMPOSE_PROJECT_NAME unchanged
[ ] MODERN_DATA_ROOT unchanged or deliberately migrated
[ ] LEGACY_DATA_ROOT unchanged or deliberately migrated
[ ] MariaDB named volume still resolves to the expected volume
[ ] no docker compose down -v
```

After upgrade:

```sh
docker compose build --no-cache app
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
pytest -q
```

Then verify login, LDAP if enabled, legacy playback, new upload, preview creation,
rapid seeking, comments, download setting and audit logging.

## License

GPL-3.0-or-later. See `LICENSE`.
