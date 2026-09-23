#!/usr/bin/env bash
set -euo pipefail

# Run from cron on the Docker host.
# Required: Docker Compose v2, tar, gzip, sha256sum.
# The script backs up MariaDB, modern SQLite, new media/images and .env.

PROJECT_DIR="${PROJECT_DIR:-/opt/mediadrop}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups/mediadrop}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"

cd "$PROJECT_DIR"
mkdir -p "$DEST"
chmod 700 "$BACKUP_ROOT" "$DEST"

# 1) Consistent MariaDB dump from inside the DB container.
docker compose exec -T db sh -c \
  'exec mariadb-dump --single-transaction --quick --routines --triggers -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"' \
  | gzip -9 > "$DEST/mariadb.sql.gz"

# 2) Consistent SQLite backup using Python's sqlite3 backup API in the app container.
APP_CID="$(docker compose ps -q app)"
docker compose exec -T app python - <<'PY'
import sqlite3
src = sqlite3.connect('/data/modern/mediadrop-modern.db')
dst = sqlite3.connect('/tmp/mediadrop-modern.db.backup')
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
docker cp "$APP_CID:/tmp/mediadrop-modern.db.backup" "$DEST/mediadrop-modern.db"
docker compose exec -T app rm -f /tmp/mediadrop-modern.db.backup

# 3) New writable media and images from their current bind mounts.
# Read them through the app container so the backup does not depend on host-path values.
docker compose exec -T app tar -C /data -cf - media images \
  | gzip -1 > "$DEST/files.tar.gz"

# 4) Deployment config. Keep it protected: it contains secrets.
cp -p .env "$DEST/env.backup"
chmod 600 "$DEST/env.backup"
[ -f compose.yml ] && cp -p compose.yml "$DEST/compose.yml"

# Integrity manifest.
(
  cd "$DEST"
  sha256sum mariadb.sql.gz mediadrop-modern.db files.tar.gz env.backup compose.yml 2>/dev/null || true
) > "$DEST/SHA256SUMS"

# Only remove old timestamped backup directories after this run succeeded.
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -print -exec rm -rf -- {} +

printf 'MediaDrop backup completed: %s\n' "$DEST"
