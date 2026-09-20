# MediaDrop Modern

Modernized fork of the original MediaDrop project.

The project preserves compatibility with existing MediaDrop databases
and media libraries while replacing the legacy Python 2 / Pylons stack
with Python 3, FastAPI, SQLAlchemy 2 and Docker.

Original project:
https://github.com/mediadrop/mediadrop

# MediaDrop modern runtime 0.4

A deliberately small Python 3 replacement runtime for the legacy MediaDrop
application. It targets the **existing MediaDrop database schema and media
files** instead of porting Pylons, Paste, ToscaWidgets, Genshi, repoze.who,
the old player framework, or the transcoding pipeline.

The goal is operational: keep the familiar MediaDrop product and URLs, run it
on a supported stack, and make the codebase small enough to maintain normally.

## Stack

- Python 3.13
- FastAPI / Starlette
- SQLAlchemy 2.x
- Jinja2 + plain CSS (no Node/npm frontend build)
- Uvicorn
- MariaDB 11.4 in Docker Compose
- Native browser `<video>` / `<audio>` playback
- pytest

## 0.4 UI / thumbnail compatibility pass

The public UI keeps the information hierarchy of classic MediaDrop without
copying the old visual design: a featured/most-popular item, a popular list,
a compact latest list, categories, media library, podcasts and search.

Public sections:

- `/` overview
- `/media` media library and search
- `/categories` category browser
- `/tags` tag browser
- `/podcasts` podcasts
- media/podcast view pages, comments, ratings and downloads

The admin now includes Media, Comments, Categories, Podcasts, Users and
**Settings**. The settings screen intentionally restores only useful product
settings and drops player/transcoding-era options:

- site name and default language
- featured category
- comments and moderation
- public uploads and size limit
- podcasts
- download / like / dislike controls
- RSS / sitemap
- accent color and footer text

These values are stored in the legacy `settings(key, value)` table and use
legacy setting keys where practical, so an imported MediaDrop database can
carry useful configuration forward.

## Languages

The language picker includes the historical MediaDrop locale set currently
ported here: Russian, English, Arabic, Bulgarian, Czech, German, Greek,
Spanish, Finnish, French, Hebrew, Hungarian, Italian, Japanese, Polish,
Brazilian Portuguese, Romanian, Slovak, Slovenian, Swedish, Turkish,
Ukrainian and Simplified Chinese.

Russian and English have complete translations for the current modern UI.
The other languages already translate the main shell/navigation/settings
labels and currently fall back to English for strings that have not yet been
mapped from the old gettext catalogs. Arabic and Hebrew render RTL.

Default language can be selected in **Admin -> Settings**. `LOCALE=ru` remains
the environment fallback for a fresh database.

## Preserved compatibility

- Existing core `media`, `media_files`, `storage`, `podcasts`, `categories`,
  `tags`, comments, settings, users/groups/permissions tables.
- Existing MediaDrop password hashes for transition compatibility.
- Legacy-shaped media URLs such as `/media/<slug>/view` and
  `/podcasts/<podcast>/<slug>/view`.
- Legacy-shaped local file URLs.
- `media_files.unique_id` local-file layout.
- Publication rule based on reviewed/encoded/publishable/date fields.
- Podcast/latest feeds and sitemap when enabled.
- JSON API under `/api`.

## Intentionally removed

- Encoding/transcoding workers and FFmpeg orchestration.
- Encoding profiles, presets and queues.
- Flash/RTMP and old player-selection logic.
- Automatic conversion and probing pipeline.
- Pylons/Paste/Routes/ToscaWidgets/Genshi runtime and legacy plugin runtime.

An uploaded file is expected to already be browser-ready. It is saved as-is
and immediately marked `encoded = true`.

## Fresh start

```sh
cp .env.example .env
# edit passwords and SECRET_KEY

docker compose build
docker compose up -d db
docker compose run --rm app mediadrop init-db
docker compose run --rm app mediadrop create-admin admin admin@example.com
docker compose up -d
```

The app binds to `127.0.0.1:8080` by default. Put nginx/Caddy in front for TLS
and public traffic. For direct HTTP testing set `COOKIE_SECURE=false`.

## Upgrade an existing modern-port install

Do **not** run `init-db` again. Back up `.env`, unpack the new source over the
old tree, then rebuild:

```sh
cp .env .env.backup
docker compose build
docker compose up -d
docker compose logs --tail=100 app
```

The 0.4 thumbnail update requires no database schema migration.

## Moving an existing legacy MediaDrop installation

1. Dump the old database and archive the old `media_dir`.
2. Restore the dump into a test MariaDB instance.
3. Do **not** run `mediadrop init-db` against that imported database.
4. Check compatibility read-only:

```sh
docker compose run --rm app python scripts/check_legacy_db.py
```

5. Point `LEGACY_DATA_ROOT` at the copied legacy data tree. With the layout
   `/opt/mediadrop_data/media` and `/opt/mediadrop_data/images` use:

```env
LEGACY_DATA_ROOT=/opt/mediadrop_data
```

6. Start the app and compare representative public/admin workflows before
   switching production traffic.

The Compose file keeps the copied legacy archive read-only at
`/data/legacy-media` and `/data/legacy-images`. New uploads are written to
separate Docker volumes (`/data/media` and `/data/images`). This prevents the
modern application from modifying the migration source while still allowing
new media and preview uploads.

Legacy thumbnails are read directly from the original MediaDrop layout:
`images/media/<media_id>s.jpg`, `...m.jpg`, `...l.jpg` and `...orig.<ext>`.
No database migration is required for previews.


## Preview images

Existing MediaDrop preview images are used as-is from the legacy `images/media`
directory. In Admin -> Media, a JPEG/PNG/WebP preview can also be uploaded when
creating or editing a media item. The modern runtime keeps the original and
generates 16:9 JPEG variants (`s`, `m`, `l`) compatible with the legacy naming
scheme. The same optional preview field is available on the public upload form
when public uploads are enabled.

Run this after mounting the legacy data to see how many old previews are found:

```sh
docker compose run --rm app python scripts/check_legacy_thumbnails.py
```

## Password migration

Legacy SHA1 hashes are verified only so existing administrators can log in
immediately. After functional parity is accepted, migrate successful logins to
Argon2id and widen/change the password storage in a separate schema migration.
Doing that later keeps rollback to the legacy application possible during the
comparison phase.

## Development

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
uvicorn app.main:app --reload
```

## License

GPL-3.0-or-later, matching the upstream MediaDrop license.
