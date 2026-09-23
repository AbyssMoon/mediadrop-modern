# MediaDrop Modern migration notes

Current release: **0.6.6**  
Last updated: **2026-09-22**

This document records the compatibility contract and the safe way to move an existing
MediaDrop installation to the modern runtime. It is intentionally more conservative
than a normal green-field deployment because preserving rollback to the old system is
a primary requirement.

## 1. Compatibility boundary

The modern runtime replaces the old Python 2 / Pylons application layer. It does **not**
attempt to port the old framework stack.

The compatibility boundary is instead:

- the relevant existing MediaDrop SQL tables/columns;
- publication semantics (`reviewed`, `encoded`, `publishable`, `publish_on`);
- `media_files.unique_id` for local storage;
- existing categories, tags, podcasts, comments and useful settings;
- local users/groups/permissions during the transition;
- main public URL shapes;
- the legacy thumbnail naming/layout.

Changed intentionally:

- FastAPI/Starlette replaces Pylons/Paste;
- Jinja2 replaces Genshi;
- SQLAlchemy 2 replaces the old SQLAlchemy stack;
- native HTML5 playback replaces the old player plugin hierarchy;
- `encoded=true` means a browser-ready file is attached; no encoder worker runs;
- normal SQL matching is used instead of legacy search helper tables;
- useful settings have a compact modern admin UI;
- hierarchy editing uses the existing `categories.parent_id`;
- local user/group assignment reuses existing legacy RBAC tables;
- modern-only authentication settings live in a separate sidecar database.

Removed intentionally:

- encoding/transcoding queues, workers, profiles and presets;
- Flash/RTMP playback;
- legacy player-selection logic;
- Pylons/Paste/ToscaWidgets/Genshi framework machinery;
- the old plugin runtime and obsolete encoder/player configuration UI.

## 2. Data separation

The migration-safe deployment keeps the old source archive read-only:

```text
/data/legacy-media   imported old media, read-only
/data/legacy-images  imported old images, read-only
/data/media          all new media uploads, writable
/data/images         all new previews, writable
/data/modern         modern-only settings sidecar, writable
```

The production Compose mapping is configurable with two host roots and a stable
Compose project name:

```dotenv
COMPOSE_PROJECT_NAME=mediadrop-modern
MODERN_DATA_ROOT=/opt/mediadrop-modern-data
LEGACY_DATA_ROOT=/opt/mediadrop_data
```

With those defaults, the host/container mapping is:

```text
/opt/mediadrop-modern-data/media   -> /data/media          rw
/opt/mediadrop-modern-data/images  -> /data/images         rw
/opt/mediadrop-modern-data/modern  -> /data/modern         rw
/opt/mediadrop-modern-data/logs    -> /data/logs           rw
/opt/mediadrop_data/media          -> /data/legacy-media   ro
/opt/mediadrop_data/images         -> /data/legacy-images  ro
```

MariaDB remains in the Docker named volume `mediadrop-modern_mediadrop_db` with the
default project name. Keeping `COMPOSE_PROJECT_NAME` stable prevents a versioned
release directory from silently creating a new empty DB volume. See
`docs/DEPLOYMENT_RU.md` for the complete deployment procedure.

The main MariaDB database remains legacy-compatible. New media rows/files created by
the modern application use the existing MediaDrop tables.

Modern-only authentication configuration is separate:

```text
MODERN_DATABASE_URL=sqlite:////data/modern/mediadrop-modern.db
```

The sidecar currently contains a small `modern_settings` key/value table. It can be
created from scratch without changing the imported legacy database.

Migration-safe defaults when the sidecar is empty:

- mandatory site authentication: off;
- LDAP: off.

## 3. Fresh installation vs. legacy import

### Fresh installation

Use `mediadrop init-db` only for a new empty database:

```sh
docker compose up -d db
docker compose run --rm app mediadrop init-db
docker compose run --rm app mediadrop create-admin admin admin@example.com
docker compose up -d
```

Fresh installations default to English (`LOCALE=en`).

### Legacy import

For an imported MediaDrop database:

1. Back up the SQL dump and old data directory.
2. Restore the SQL dump to MariaDB.
3. **Do not run `mediadrop init-db`.**
4. Configure `LEGACY_DATA_ROOT` to point to the copied old data tree.
5. Keep the legacy media/images mounts read-only.
6. Run the read-only checks below.
7. Start the application and verify representative workflows.

Read-only checks:

```sh
docker compose run --rm app python scripts/check_legacy_db.py
docker compose run --rm app python scripts/check_legacy_thumbnails.py
docker compose run --rm app python scripts/list_unplayable_media.py
```

These helpers do not modify the legacy database or files.

## 4. Real legacy installation already checked

The imported installation used during development contained:

- 511 referenced local media files;
- 0 missing referenced media files;
- 511 recognized legacy previews;
- 0 missing previews;
- 443 MP4 files;
- 67 FLV files;
- 1 WebM file;
- no remote YouTube/Vimeo/etc. storage entries in active use.

The main public/admin flows have also been manually exercised against that imported
data. The current automated suite contains **43 passing tests**.

The runtime should therefore be treated as working software, while the remaining FLV
conversion and later security/schema cleanup are separate operational tasks.

## 5. FLV cleanup strategy

Modern browsers do not natively play FLV and this project intentionally does not bring
back Flash or a permanent transcoding service.

Find imported media without a browser-ready variant:

```sh
docker compose run --rm app python scripts/list_unplayable_media.py
```

The report includes media id/title/slug, media-file id, container, `unique_id`, resolved
path and whether the source file exists.

### Safe conversion procedure

Do not overwrite or delete the source FLV. Convert to a separate temporary file:

```sh
ffmpeg \
  -i /path/to/source.flv \
  -map 0:v:0 -map 0:a:0? \
  -c:v libx264 \
  -preset medium \
  -crf 20 \
  -pix_fmt yuv420p \
  -c:a aac \
  -b:a 160k \
  -movflags +faststart \
  /tmp/converted.mp4
```

Inspect streams and duration:

```sh
ffprobe -v error \
  -show_entries format=duration:stream=codec_type,codec_name,width,height \
  -of json \
  /tmp/converted.mp4
```

Read the whole output to catch decode/container errors:

```sh
ffmpeg -v error -i /tmp/converted.mp4 -f null -
```

After validation, attach the MP4 to the **same media item** in Admin. The modern runtime
stores it under `/data/media` using a new generated `unique_id` and adds a new
`media_files` row. The original FLV and its legacy row remain untouched.

The player prefers a browser-supported media variant when both old FLV and new MP4 are
present. This is the recommended transition because it repairs playback without
destroying the migration source.

Do not delete/archive the legacy FLV until the old application rollback window is
closed and the new MP4 has been verified in production.

## 6. Thumbnail compatibility

Old thumbnails are read directly from:

```text
images/media/<media_id>s.jpg
images/media/<media_id>m.jpg
images/media/<media_id>l.jpg
images/media/<media_id>orig.<ext>
```

New thumbnail uploads keep the same naming convention but use larger generated sizes:

- `s` <= 320x180
- `m` <= 960x540
- `l` <= 1920x1080

The source image is never upscaled. Legacy images are never deleted by editing/removing
modern files.

## 7. Authentication / LDAP migration behavior

LDAP configuration does not require any new table in the legacy database. It is stored
in the modern sidecar.

Authentication order:

1. if LDAP is enabled, try LDAP first;
2. if LDAP does not authenticate those credentials, try the local legacy account.

This intentionally keeps local break-glass/admin accounts available. LDAP identities
are session-only and are not inserted into the legacy `users` table.

Role mapping:

- Access -> authenticated viewer;
- Editor -> Access + Media/Comments administration;
- Admin -> Editor + Settings/Auth/Users/Categories/Podcasts.

Editor/Admin groups implicitly grant Access. Direct `memberOf` is used; nested LDAP
groups are not expanded.

The historical local group name `admins` is treated as full admin even if that old
database only assigned the `edit` permission. Fresh `mediadrop create-admin` accounts
receive both `edit` and `admin`.

The session TTL is configurable as 8 hours, 1 day, 1 week or 30 days. The LDAP bind
password is encrypted with a key derived from `SECRET_KEY`.

When moving/restoring the sidecar, preserve `SECRET_KEY`. If it changes, legacy content
remains valid, but existing sessions are invalidated and the saved LDAP bind password
must be entered again.

## 8. Schema policy

The current release deliberately avoids mandatory changes to the imported legacy
schema. This is what keeps legacy import and rollback simple.

Do not casually remove or rewrite legacy tables during the migration window.

After rollback to the old application is no longer needed, planned cleanup may include:

- introduce Alembic for project-owned schema changes;
- after the rollback window, remove the retained legacy SHA-1 hashes;
- normalize remaining charset/index issues where useful;
- inventory then remove unused encoder/player/fulltext tables;
- optimize large-file delivery with nginx `X-Accel-Redirect` where useful.

## 9. Migration-relevant release history

The 0.5.x and 0.6.x releases through 0.6.6 keep the imported legacy content schema unchanged. Modern-only state continues to use the sidecar SQLite database.

- **0.5.1**: production-style LDAP/auth settings UI and RU/EN wording cleanup.
- **0.5.2**: favicon, automatic editable slug, current-user author/email defaults,
  current publication time.
- **0.5.3**: hierarchical media category picker with column-major reading order;
  Podcast field follows the Podcasts feature setting without destroying old links.
- **0.5.4**: aligned Slug label and read-only unsupported-media inventory helper.
- **0.6.0**: local-account Argon2id authentication with lazy migration from legacy SHA-1; local-user enable/disable state in the modern sidecar; improved Admin -> Users form layout and status display.

## 10. Release verification

Current archive verification:

```text
pytest -q
43 passed
```

Recommended additional checks before deployment:

```sh
python -m compileall app scripts
node --check app/static/player.js
node --check app/static/admin-media.js
```

For a real deployment, also verify login/session cookies behind the intended HTTPS
reverse proxy and perform a live LDAP bind against the target directory if LDAP will
be enabled.

### 0.6.1 operational hardening

- Added JSON-lines audit logging for login/logout and state-changing admin actions.
- Normal GET/page-view traffic remains in nginx access logs and is not duplicated.
- Audit log defaults to `/data/logs/audit.log` and is bind-mounted from the host.
- Confirmed SQLite as the permanent store for the small modern-only sidecar database.
- Added `scripts/build_release.sh` so runtime DBs, secrets, logs and caches cannot enter releases.
- Added `scripts/backup.sh` for cron-based MariaDB + SQLite + writable-media backups.

## 11. Production handoff status — 2026-09-21

Production cutover is complete and the current runtime is operational.

Verified on the production VM:

- `docker compose` resolves MariaDB storage to `mediadrop-modern_mediadrop_db`;
- MariaDB starts healthy and the imported `mediadrop` database is present;
- the imported schema includes the expected MediaDrop tables and `SELECT COUNT(*) FROM media` returned 511;
- the modern SQLite database was copied from its former named volume to `/opt/mediadrop-modern-data/modern/mediadrop-modern.db`, with matching SHA-256 before/after copy;
- application container sees the SQLite file at `/data/modern/mediadrop-modern.db`;
- legacy media is visible read-only at `/data/legacy-media` (about 145 GiB) and legacy images at `/data/legacy-images` (about 482 MiB);
- new writable `/data/media` and `/data/images` were empty at cutover, which is expected until new uploads are made;
- audit logging is live at `/data/logs/audit.log` -> `/opt/mediadrop-modern-data/logs/audit.log`;
- nginx HTTPS access reaches the application and unauthenticated requests redirect to `/login`;
- LDAP is working in production;
- VM monitoring is connected to Zabbix.

The intended production storage model is now fixed as:

1. legacy application data/metadata: MariaDB named volume;
2. modern-only state: SQLite bind mount;
3. legacy media/images: existing host directories, mounted read-only;
4. new media/images: dedicated writable bind mounts under `/opt/mediadrop-modern-data`;
5. audit logs: dedicated bind mount under `/opt/mediadrop-modern-data/logs`.

A production backup run using `/opt/backup.sh` was tested successfully. The generated set contained `mariadb.sql.gz`, `mediadrop-modern.db`, `files.tar.gz`, protected environment/compose copies and `SHA256SUMS`. At cutover, `files.tar.gz` is intentionally tiny because the new writable media/image directories are still empty; the 145 GiB legacy media tree is not duplicated into that archive.

Do not remove the legacy media tree or old Docker volumes solely because the bind-mount migration is complete. Keep rollback material until the rollback window is intentionally closed.

### 0.6.2 legacy FLV migration helper

Added `scripts/migrate_legacy_flv.sh` for the final legacy-video cleanup phase.
Production discovery currently reports 67 media items that have FLV source files but
no browser-playable variant. The host-side script uses a separate ffmpeg container,
leaves the legacy archive untouched, writes replacement MP4 files to the modern media
root, validates them with ffprobe, and only then adds a new `media_files` row pointing
to the same media item.

The conversion path avoids unnecessary quality loss: compatible H.264/AAC FLV is
remuxed, H.264 with incompatible audio copies video while transcoding audio to AAC,
and other video codecs are transcoded to H.264/AAC. Deterministic output names and
pre-insert checks make interrupted runs resumable without duplicate playable rows.

### 0.6.3 real playback audit

- HTML5 video players now emit `media.play` only after 5 cumulative seconds of actual `playing` time.
- Paused, buffering (`waiting`/`stalled`), ended, or emptied time is not counted toward the threshold.
- The event is emitted at most once per rendered page/player instance.
- The browser POSTs to `/api/media/{media_id}/play` with the existing session CSRF token; playback continues normally if audit delivery fails.
- The server writes the event to the existing rotating audit log with authenticated username/backend when available, client IP, media id, slug, and title.
- This does not change the legacy `media.views` page-view counter; it is an independent audit signal for actual playback.

### 0.6.4 video seek connection-pool fix

- `/files/*` now uses a function-scoped database dependency, so the SQLAlchemy
  session is closed immediately after the file response is constructed instead of
  remaining checked out for the full duration of video delivery.
- This prevents rapid HTML5 video seek/range requests from exhausting the default
  SQLAlchemy `QueuePool` (`pool_size=5`, `max_overflow=10`) and causing 30-second
  `QueuePool` timeouts followed by HTTP 500 responses.
- `MEDIA_SERVE_MODE=app` remains supported and is appropriate when an external nginx
  is only a reverse proxy and does not share the application's media filesystem.

### 0.6.5 authenticated comments and player controls

- Signed-in commenters now use their authenticated display name and email automatically; the fields are read-only in the browser and the server overwrites submitted identity values with the authenticated principal.
- Anonymous visitors retain editable Name/Email fields when anonymous site access is permitted.
- The custom player toolbar now exposes mute/unmute and fullscreen buttons, with state synchronized to native media/fullscreen events.
- When `appearance_show_download` is disabled, HTML5 video elements receive `controlslist="nodownload"`, which hides the native Download action in supporting browsers without context-menu blocking or other hacks.

### 0.6.6 deployment documentation and mount hardening

- `compose.yml` now consumes `MODERN_DATA_ROOT` and `LEGACY_DATA_ROOT` instead of
  hard-coding the bind-mount source paths independently of `.env`.
- Compose project naming is explicitly stable (`mediadrop-modern` by default), which
  keeps the MariaDB named-volume identity stable across versioned release directories.
- Production host/container mounts, permissions, backup/restore, external-nginx mode
  and upgrade safety are documented in `docs/DEPLOYMENT_RU.md` and
  `docs/OPERATIONS_RU.md`.
- The backup helper documentation now correctly describes media/images as bind mounts.
- No legacy or modern database schema change is required for 0.6.6.
