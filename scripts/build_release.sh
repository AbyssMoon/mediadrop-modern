#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VERSION="${1:-$(python3 - <<'PY'
from pathlib import Path
import re
text = Path('pyproject.toml').read_text()
m = re.search(r'^version = "([^"]+)"', text, re.M)
print(m.group(1) if m else 'unknown')
PY
)}"
NAME="mediadrop-modern-v${VERSION}"
OUT="${ROOT}/dist"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT" "$STAGE/$NAME"

# Build from the working tree, but explicitly exclude runtime/secrets/cache files.
tar -C "$ROOT" \
  --exclude='./dist' \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./__pycache__' \
  --exclude='*/__pycache__' \
  --exclude='./.pytest_cache' \
  --exclude='./.ruff_cache' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='*.db' \
  --exclude='*.sqlite' \
  --exclude='*.sqlite3' \
  --exclude='*.log' \
  -cf - . | tar -C "$STAGE/$NAME" -xf -

ARCHIVE="$OUT/$NAME.tar.gz"
ZIP_ARCHIVE="$OUT/$NAME.zip"
tar -C "$STAGE" -czf "$ARCHIVE" "$NAME"
( cd "$STAGE" && zip -qr "$ZIP_ARCHIVE" "$NAME" )
(
  cd "$OUT"
  sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256"
  sha256sum "$(basename "$ZIP_ARCHIVE")" > "$(basename "$ZIP_ARCHIVE").sha256"
)
printf 'Created %s\n' "$ARCHIVE"
cat "$ARCHIVE.sha256"
printf 'Created %s\n' "$ZIP_ARCHIVE"
cat "$ZIP_ARCHIVE.sha256"
