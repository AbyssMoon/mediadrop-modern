# Architecture / migration notes

## Compatibility boundary

The modern application treats the old SQL schema, media filenames and main URL
shapes as the compatibility boundary. The web/runtime layer is new.

Kept:

- core MediaDrop table and column names used by content/authentication
- `reviewed && encoded && publishable && publish_on` publication semantics
- local storage `media_files.unique_id`
- existing password validation during transition
- categories, tags, podcasts, comments and useful site settings
- main public URL shapes

Changed:

- `encoded` now means a web-ready file is attached; no encoder runs
- Jinja2 replaces Genshi
- FastAPI/Starlette replaces Pylons/Paste
- SQLAlchemy 2 replaces the legacy SQLAlchemy stack
- native HTML5 playback replaces the player plugin hierarchy
- search currently uses normal SQL matching rather than legacy helper tables
- a compact modern settings UI writes useful values into the existing
  `settings(key,value)` table

Removed:

- encoding queues/profiles/workers
- Flash/RTMP player behavior
- transcoding
- old player/encoder/storage configuration UI that is no longer relevant
- Pylons/Paste/ToscaWidgets framework machinery

## UI strategy

The 0.4 homepage deliberately preserves the classic MediaDrop information
architecture — featured/most-popular on the left, recent items alongside,
popular items below, plus categories/search — while using a responsive modern
layout. This reduces user retraining without carrying old Bootstrap-era markup
or assets forward.

## Language strategy

The language selector exposes the historical locale set already identified in
upstream MediaDrop. Russian and English cover the full current UI. Other
locales use migrated common strings and English fallback until the remaining
legacy gettext catalog strings are mapped to the new template keys. RTL is
supported for Arabic/Hebrew.

## After functional parity

- own schema changes with Alembic
- normalize utf8mb4/InnoDB/indexes where needed
- Argon2id rehash-on-login
- remove unused encoder/player/fulltext tables after installation inventory
- migrate remaining historical gettext translations
- keep the legacy thumbnail compatibility layer while old data is in service
- optionally move media delivery to nginx `X-Accel-Redirect`
