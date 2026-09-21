# MediaDrop Modern migration notes

Current release: **0.5.4**  
Last updated: **2026-09-21**

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
data. The current automated suite contains **32 passing tests**.

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
- move password storage to Argon2id rehash-on-login;
- normalize remaining charset/index issues where useful;
- inventory then remove unused encoder/player/fulltext tables;
- optimize large-file delivery with nginx `X-Accel-Redirect` where useful.

## 9. Release 0.5.x migration-relevant changes

All 0.5.1-0.5.4 updates keep both the legacy schema and sidecar schema unchanged.

- **0.5.1**: production-style LDAP/auth settings UI and RU/EN wording cleanup.
- **0.5.2**: favicon, automatic editable slug, current-user author/email defaults,
  current publication time.
- **0.5.3**: hierarchical media category picker with column-major reading order;
  Podcast field follows the Podcasts feature setting without destroying old links.
- **0.5.4**: aligned Slug label and read-only unsupported-media inventory helper.

## 10. Release verification

Current archive verification:

```text
pytest -q
32 passed
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
