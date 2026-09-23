#!/usr/bin/env bash
set -Eeuo pipefail

# MediaDrop legacy FLV -> modern MP4 migration.
# Runs natively on the host, but uses:
#   * docker compose db  - to read/update MariaDB
#   * a separate ffmpeg container - to probe/remux/transcode media
#
# Safe defaults:
#   * legacy FLV files are never changed or deleted;
#   * existing browser-playable variants are skipped;
#   * output MP4 names are deterministic, so interrupted runs can be resumed;
#   * DB row is added only after ffprobe validates the finished MP4;
#   * default mode is --dry-run. Use --run to modify files/DB.

PROJECT_DIR="${PROJECT_DIR:-/opt/mediadrop-modern}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/compose.yml}"
LEGACY_MEDIA_ROOT="${LEGACY_MEDIA_ROOT:-/opt/mediadrop_data/media}"
MODERN_MEDIA_ROOT="${MODERN_MEDIA_ROOT:-/opt/mediadrop-modern-data/media}"
LOG_FILE="${LOG_FILE:-/opt/mediadrop-modern-data/logs/flv-migration.log}"
FFMPEG_IMAGE="${FFMPEG_IMAGE:-linuxserver/ffmpeg:latest}"
FFMPEG_CPUS="${FFMPEG_CPUS:-2.0}"
APP_UID="${APP_UID:-100}"
APP_GID="${APP_GID:-101}"
CRF="${CRF:-20}"
PRESET="${PRESET:-fast}"
AUDIO_BITRATE="${AUDIO_BITRATE:-160k}"

MODE="dry-run"
LIMIT=0
ONLY_MEDIA_ID=""
FORCE_TRANSCODE=0

usage() {
  cat <<'USAGE'
Usage:
  mediadrop-migrate-flv.sh [--dry-run|--run] [options]

Options:
  --dry-run            Show what would be migrated (default).
  --run                Perform conversion and register MP4 in MariaDB.
  --limit N            Process at most N candidates.
  --media-id N         Process only one MediaDrop media ID.
  --force-transcode    Always encode H.264/AAC; do not use stream copy.
  -h, --help           Show this help.

Environment overrides:
  PROJECT_DIR          /opt/mediadrop-modern
  COMPOSE_FILE         $PROJECT_DIR/compose.yml
  LEGACY_MEDIA_ROOT    /opt/mediadrop_data/media
  MODERN_MEDIA_ROOT    /opt/mediadrop-modern-data/media
  LOG_FILE             /opt/mediadrop-modern-data/logs/flv-migration.log
  FFMPEG_IMAGE         linuxserver/ffmpeg:latest
  FFMPEG_CPUS          2.0
  CRF                  20
  PRESET               fast
  AUDIO_BITRATE        160k
  APP_UID / APP_GID    100 / 101

Examples:
  ./mediadrop-migrate-flv.sh --dry-run
  ./mediadrop-migrate-flv.sh --run --limit 2
  ./mediadrop-migrate-flv.sh --run --media-id 5
  ./mediadrop-migrate-flv.sh --run
USAGE
}

while (($#)); do
  case "$1" in
    --dry-run) MODE="dry-run" ;;
    --run) MODE="run" ;;
    --limit)
      shift
      [[ ${1:-} =~ ^[0-9]+$ ]] || { echo "--limit requires an integer" >&2; exit 2; }
      LIMIT="$1"
      ;;
    --media-id)
      shift
      [[ ${1:-} =~ ^[0-9]+$ ]] || { echo "--media-id requires an integer" >&2; exit 2; }
      ONLY_MEDIA_ID="$1"
      ;;
    --force-transcode) FORCE_TRANSCODE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Required command not found: $1" >&2; exit 1; }
}

require_cmd docker
require_cmd stat
require_cmd awk
require_cmd sed
require_cmd date

[[ -d "$PROJECT_DIR" ]] || { echo "PROJECT_DIR not found: $PROJECT_DIR" >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { echo "Compose file not found: $COMPOSE_FILE" >&2; exit 1; }
[[ -d "$LEGACY_MEDIA_ROOT" ]] || { echo "Legacy media root not found: $LEGACY_MEDIA_ROOT" >&2; exit 1; }
mkdir -p "$MODERN_MEDIA_ROOT" "$(dirname "$LOG_FILE")"

compose() {
  docker compose --project-directory "$PROJECT_DIR" -f "$COMPOSE_FILE" "$@"
}

log() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  echo "$line"
  if [[ "$MODE" == "run" ]]; then
    echo "$line" >> "$LOG_FILE"
  fi
}

db_sql() {
  local sql="$1"
  printf '%s\n' "$sql" | compose exec -T db sh -c \
    'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE" --batch --skip-column-names'
}

ffprobe_value() {
  local input="$1"
  shift
  docker run --rm --entrypoint ffprobe \
    -v "$LEGACY_MEDIA_ROOT:/input:ro" \
    -v "$MODERN_MEDIA_ROOT:/output:ro" \
    "$FFMPEG_IMAGE" -v error "$@" "$input" 2>/dev/null | head -n 1 | tr -d '\r'
}

probe_legacy() {
  local uid="$1" field="$2"
  case "$field" in
    video_codec)
      ffprobe_value "/input/$uid" -select_streams v:0 -show_entries stream=codec_name -of default=nw=1:nk=1
      ;;
    audio_codec)
      ffprobe_value "/input/$uid" -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1
      ;;
  esac
}

probe_output() {
  local name="$1" field="$2"
  case "$field" in
    video_codec)
      ffprobe_value "/output/$name" -select_streams v:0 -show_entries stream=codec_name -of default=nw=1:nk=1
      ;;
    audio_codec)
      ffprobe_value "/output/$name" -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1
      ;;
    width)
      ffprobe_value "/output/$name" -select_streams v:0 -show_entries stream=width -of default=nw=1:nk=1
      ;;
    height)
      ffprobe_value "/output/$name" -select_streams v:0 -show_entries stream=height -of default=nw=1:nk=1
      ;;
    bitrate)
      ffprobe_value "/output/$name" -show_entries format=bit_rate -of default=nw=1:nk=1
      ;;
    duration)
      ffprobe_value "/output/$name" -show_entries format=duration -of default=nw=1:nk=1
      ;;
  esac
}

numeric_or_null() {
  local value="${1:-}"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s' "$value"
  else
    printf 'NULL'
  fi
}

# The DB must already be up; do not let a migration unexpectedly initialize a new DB.
if ! compose ps --status running db 2>/dev/null | grep -q .; then
  echo "MariaDB compose service 'db' is not running. Start the production stack first." >&2
  exit 1
fi

# Pull/resolve the image before the candidate loop, so failures happen early.
log "Checking ffmpeg image: $FFMPEG_IMAGE"
docker image inspect "$FFMPEG_IMAGE" >/dev/null 2>&1 || docker pull "$FFMPEG_IMAGE" >/dev/null

storage_id="$(db_sql "SELECT id FROM storage WHERE engine_type='LocalFileStorage' AND enabled=1 ORDER BY id LIMIT 1;")"
if [[ -z "$storage_id" ]]; then
  if [[ "$MODE" == "dry-run" ]]; then
    log "No enabled LocalFileStorage row exists; --run would create it."
    storage_id="DRYRUN"
  else
    db_sql "INSERT INTO storage (engine_type, display_name, enabled, created_on, modified_on, data) VALUES ('LocalFileStorage', 'Local File Storage', 1, NOW(), NOW(), '{}');"
    storage_id="$(db_sql "SELECT id FROM storage WHERE engine_type='LocalFileStorage' AND enabled=1 ORDER BY id LIMIT 1;")"
    [[ "$storage_id" =~ ^[0-9]+$ ]] || { echo "Failed to create/find LocalFileStorage" >&2; exit 1; }
  fi
fi

media_filter=""
if [[ -n "$ONLY_MEDIA_ID" ]]; then
  media_filter="AND m.id = $ONLY_MEDIA_ID"
fi

# Fields: media_id, source_file_id, source_unique_id.
# Select only FLV rows where the media item still has no browser-playable A/V variant.
readarray -t candidates < <(db_sql "
SELECT m.id, mf.id, mf.unique_id
FROM media AS m
JOIN media_files AS mf ON mf.media_id = m.id
WHERE LOWER(COALESCE(mf.container,'')) = 'flv'
  AND mf.type IN ('video','audio')
  AND mf.unique_id IS NOT NULL
  AND mf.unique_id <> ''
  $media_filter
  AND NOT EXISTS (
    SELECT 1
    FROM media_files AS p
    WHERE p.media_id = m.id
      AND p.type IN ('video','audio')
      AND LOWER(COALESCE(p.container,'')) IN ('mp4','m4v','webm','ogv','ogg','mp3','m4a','aac','oga','wav','flac')
  )
ORDER BY m.id, mf.id;
")

if ((${#candidates[@]} == 0)); then
  log "Nothing to migrate. No FLV-only media items found."
  exit 0
fi

log "Found ${#candidates[@]} FLV candidate row(s). Mode: $MODE"

processed=0
converted=0
remuxed=0
partial=0
failed=0
skipped=0

for row in "${candidates[@]}"; do
  IFS=$'\t' read -r media_id source_file_id source_uid <<< "$row"

  if (( LIMIT > 0 && processed >= LIMIT )); then
    break
  fi
  ((processed+=1))

  # Refuse unsafe paths from legacy DB.
  if [[ "$source_uid" == /* || "$source_uid" == *".."* ]]; then
    log "SKIP media=$media_id file=$source_file_id unsafe unique_id=$source_uid"
    ((failed+=1))
    continue
  fi

  source_path="$LEGACY_MEDIA_ROOT/$source_uid"
  output_name="legacy-flv-${source_file_id}.mp4"
  output_path="$MODERN_MEDIA_ROOT/$output_name"
  part_name=".${output_name}.part.mp4"
  part_path="$MODERN_MEDIA_ROOT/$part_name"

  if [[ ! -f "$source_path" ]]; then
    log "ERROR media=$media_id file=$source_file_id source missing: $source_path"
    ((failed+=1))
    continue
  fi

  # Re-check immediately before work, making reruns safe if another process added MP4.
  existing_playable="$(db_sql "SELECT id FROM media_files WHERE media_id=$media_id AND type IN ('video','audio') AND LOWER(COALESCE(container,'')) IN ('mp4','m4v','webm','ogv','ogg','mp3','m4a','aac','oga','wav','flac') ORDER BY id LIMIT 1;")"
  if [[ -n "$existing_playable" ]]; then
    log "SKIP media=$media_id already has playable file_id=$existing_playable"
    ((skipped+=1))
    continue
  fi

  vcodec="$(probe_legacy "$source_uid" video_codec || true)"
  acodec="$(probe_legacy "$source_uid" audio_codec || true)"
  if [[ -z "$vcodec" ]]; then
    log "ERROR media=$media_id file=$source_file_id ffprobe found no video stream"
    ((failed+=1))
    continue
  fi

  strategy="transcode"
  if (( FORCE_TRANSCODE == 0 )); then
    if [[ "$vcodec" == "h264" && ( -z "$acodec" || "$acodec" == "aac" ) ]]; then
      strategy="remux"
    elif [[ "$vcodec" == "h264" ]]; then
      strategy="copy-video"
    fi
  fi

  log "PLAN media=$media_id file=$source_file_id src=$source_uid video=$vcodec audio=${acodec:-none} strategy=$strategy -> $output_name"

  if [[ "$MODE" == "dry-run" ]]; then
    continue
  fi

  # If an interrupted run already produced a valid deterministic MP4, reuse it.
  valid_existing=0
  if [[ -s "$output_path" ]]; then
    out_vcodec="$(probe_output "$output_name" video_codec || true)"
    if [[ "$out_vcodec" == "h264" ]]; then
      log "RESUME media=$media_id using existing validated output $output_name"
      valid_existing=1
    else
      log "WARN media=$media_id removing invalid existing output $output_name"
      rm -f -- "$output_path"
    fi
  fi

  if (( valid_existing == 0 )); then
    rm -f -- "$part_path"
    ffmpeg_args=(
      --rm
      --cpus "$FFMPEG_CPUS"
      -v "$LEGACY_MEDIA_ROOT:/input:ro"
      -v "$MODERN_MEDIA_ROOT:/output"
      "$FFMPEG_IMAGE"
      -hide_banner -nostdin -y
      -i "/input/$source_uid"
      -map 0:v:0 -map '0:a:0?'
      -map_metadata 0
    )

    case "$strategy" in
      remux)
        ffmpeg_args+=( -c copy -movflags +faststart "/output/$part_name" )
        ;;
      copy-video)
        ffmpeg_args+=( -c:v copy -c:a aac -b:a "$AUDIO_BITRATE" -movflags +faststart "/output/$part_name" )
        ;;
      transcode)
        ffmpeg_args+=( -c:v libx264 -preset "$PRESET" -crf "$CRF" -pix_fmt yuv420p -c:a aac -b:a "$AUDIO_BITRATE" -movflags +faststart "/output/$part_name" )
        ;;
    esac

    log "START media=$media_id strategy=$strategy"
    if ! docker run "${ffmpeg_args[@]}"; then
      log "ERROR media=$media_id ffmpeg failed; partial file removed"
      rm -f -- "$part_path"
      ((failed+=1))
      continue
    fi

    part_vcodec="$(probe_output "$part_name" video_codec || true)"
    part_duration="$(probe_output "$part_name" duration || true)"
    if [[ "$part_vcodec" != "h264" || -z "$part_duration" || "$part_duration" == "N/A" ]]; then
      log "ERROR media=$media_id output validation failed video=${part_vcodec:-none} duration=${part_duration:-none}"
      rm -f -- "$part_path"
      ((failed+=1))
      continue
    fi

    mv -f -- "$part_path" "$output_path"
    chown "$APP_UID:$APP_GID" "$output_path" || true
    chmod 0644 "$output_path" || true

    case "$strategy" in
      remux) ((remuxed+=1)) ;;
      copy-video) ((partial+=1)) ;;
      transcode) ((converted+=1)) ;;
    esac
  fi

  out_vcodec="$(probe_output "$output_name" video_codec || true)"
  out_acodec="$(probe_output "$output_name" audio_codec || true)"
  width="$(numeric_or_null "$(probe_output "$output_name" width || true)")"
  height="$(numeric_or_null "$(probe_output "$output_name" height || true)")"
  bitrate="$(numeric_or_null "$(probe_output "$output_name" bitrate || true)")"
  duration_raw="$(probe_output "$output_name" duration || true)"
  duration_seconds="$(awk -v d="$duration_raw" 'BEGIN { if (d ~ /^[0-9]+([.][0-9]+)?$/) printf "%d", d + 0.5; else printf "0" }')"
  size="$(stat -c '%s' "$output_path")"

  if [[ "$out_vcodec" != "h264" ]]; then
    log "ERROR media=$media_id final validation unexpectedly failed; DB unchanged"
    ((failed+=1))
    continue
  fi

  # Deterministic names contain only [a-z0-9.-], so values below do not require SQL escaping.
  # INSERT...SELECT + NOT EXISTS makes the registration idempotent.
  db_sql "
INSERT INTO media_files
  (media_id, storage_id, type, container, display_name, unique_id, size, created_on, modified_on, bitrate, width, height)
SELECT
  $media_id, $storage_id, 'video', 'mp4', '$output_name', '$output_name', $size, NOW(), NOW(), $bitrate, $width, $height
WHERE NOT EXISTS (
  SELECT 1 FROM media_files
  WHERE media_id=$media_id
    AND type IN ('video','audio')
    AND LOWER(COALESCE(container,'')) IN ('mp4','m4v','webm','ogv','ogg','mp3','m4a','aac','oga','wav','flac')
);
UPDATE media
SET encoded=1,
    duration=CASE WHEN duration=0 AND $duration_seconds > 0 THEN $duration_seconds ELSE duration END
WHERE id=$media_id;
"

  registered="$(db_sql "SELECT id FROM media_files WHERE media_id=$media_id AND unique_id='$output_name' AND container='mp4' ORDER BY id DESC LIMIT 1;")"
  if [[ -z "$registered" ]]; then
    # Most likely another playable row appeared between re-check and INSERT.
    other="$(db_sql "SELECT id FROM media_files WHERE media_id=$media_id AND type IN ('video','audio') AND LOWER(COALESCE(container,'')) IN ('mp4','m4v','webm','ogv','ogg','mp3','m4a','aac','oga','wav','flac') ORDER BY id LIMIT 1;")"
    if [[ -n "$other" ]]; then
      log "SKIP media=$media_id another playable file_id=$other appeared; removing unregistered $output_name"
      rm -f -- "$output_path"
      ((skipped+=1))
      continue
    fi
    log "ERROR media=$media_id could not verify DB registration; output kept for inspection: $output_path"
    ((failed+=1))
    continue
  fi

  log "DONE media=$media_id new_file_id=$registered output=$output_name size=$size video=$out_vcodec audio=${out_acodec:-none}"
done

log "SUMMARY processed=$processed remuxed=$remuxed video_copy_audio_transcode=$partial full_transcode=$converted skipped=$skipped failed=$failed"

if (( failed > 0 )); then
  exit 2
fi
exit 0
