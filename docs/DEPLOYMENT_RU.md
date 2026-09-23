# Развёртывание MediaDrop Modern

Документ актуален для **MediaDrop Modern 0.6.6**.

Главная идея текущего deployment: legacy-данные остаются совместимыми с исходным
MediaDrop, старый файловый архив монтируется только для чтения, а всё, что создаёт
новый runtime, хранится отдельно.

## 1. Реальная схема хранения

`compose.yml` использует два корня на Docker-хосте и один Docker named volume.
Значения ниже являются production-default и могут быть переопределены в `.env`.

| Что хранится | На Docker-хосте | В контейнере `app` | Режим |
| --- | --- | --- | --- |
| Новые media uploads | `/opt/mediadrop-modern-data/media` | `/data/media` | rw |
| Новые previews | `/opt/mediadrop-modern-data/images` | `/data/images` | rw |
| Modern SQLite | `/opt/mediadrop-modern-data/modern` | `/data/modern` | rw |
| Audit log | `/opt/mediadrop-modern-data/logs` | `/data/logs` | rw |
| Старые media | `/opt/mediadrop_data/media` | `/data/legacy-media` | **ro** |
| Старые images | `/opt/mediadrop_data/images` | `/data/legacy-images` | **ro** |
| Legacy-compatible MariaDB | Docker volume `mediadrop-modern_mediadrop_db` | `/var/lib/mysql` в `db` | rw |

В `.env` это задаётся так:

```dotenv
COMPOSE_PROJECT_NAME=mediadrop-modern
MODERN_DATA_ROOT=/opt/mediadrop-modern-data
LEGACY_DATA_ROOT=/opt/mediadrop_data
```

`compose.yml` разворачивает эти корни в конкретные mounts:

```yaml
volumes:
  - "${MODERN_DATA_ROOT:-/opt/mediadrop-modern-data}/media:/data/media"
  - "${MODERN_DATA_ROOT:-/opt/mediadrop-modern-data}/images:/data/images"
  - "${MODERN_DATA_ROOT:-/opt/mediadrop-modern-data}/modern:/data/modern"
  - "${MODERN_DATA_ROOT:-/opt/mediadrop-modern-data}/logs:/data/logs"
  - "${LEGACY_DATA_ROOT:-/opt/mediadrop_data}/media:/data/legacy-media:ro"
  - "${LEGACY_DATA_ROOT:-/opt/mediadrop_data}/images:/data/legacy-images:ro"
```

### Почему `COMPOSE_PROJECT_NAME` важен

MariaDB хранится в named volume. Docker Compose включает имя проекта в имя volume.
Если распаковать новый релиз в каталог с другим именем и не зафиксировать project name,
Compose может создать **новый пустой volume**, что выглядит как «пропала база».

Поэтому в production держим:

```dotenv
COMPOSE_PROJECT_NAME=mediadrop-modern
```

Тогда volume стабильно называется:

```text
mediadrop-modern_mediadrop_db
```

Проверка:

```sh
docker compose config --volumes
docker volume inspect mediadrop-modern_mediadrop_db
```

Не копируйте содержимое `/var/lib/docker/volumes/...` на работающей MariaDB как способ
backup. Для базы используйте логический dump, см. `docs/OPERATIONS_RU.md`.

## 2. Что находится в каждом каталоге

### `${MODERN_DATA_ROOT}/media`

Файлы, загруженные уже через MediaDrop Modern, а также новые MP4, добавленные при
безопасной миграции старых FLV.

Приложение имеет право создавать и удалять здесь файлы.

### `${MODERN_DATA_ROOT}/images`

Новые previews/thumbnails. Используется совместимая схема имён `s/m/l`.

### `${MODERN_DATA_ROOT}/modern`

Modern-only SQLite sidecar:

```text
mediadrop-modern.db
```

В нём находятся настройки, которых нет в legacy schema: параметры LDAP/auth,
локальное состояние учётных записей и другие modern-only значения.

Основной контент MediaDrop в этот SQLite **не переносится**.

### `${MODERN_DATA_ROOT}/logs`

По умолчанию:

```text
audit.log
```

Это JSON-lines audit событий входа/выхода, изменений в admin и playback audit.
Обычные HTTP page views остаются задачей access log reverse proxy.

### `${LEGACY_DATA_ROOT}/media`

Старый файловый архив MediaDrop. Он должен быть доступен контейнеру, но всегда
монтируется `:ro`.

MediaDrop Modern сначала ищет файл в `/data/media`, затем в `/data/legacy-media`.
Так новый runtime может работать со старым архивом, не изменяя его.

### `${LEGACY_DATA_ROOT}/images`

Старые MediaDrop thumbnails/previews, также только для чтения.

## 3. Подготовка каталогов на новом хосте

Сначала создайте `.env`:

```sh
cp .env.example .env
```

Минимально задайте надёжные:

```dotenv
DB_PASSWORD=...
DB_ROOT_PASSWORD=...
SECRET_KEY=...
```

Для production оставьте стабильный project name и реальные пути:

```dotenv
COMPOSE_PROJECT_NAME=mediadrop-modern
MODERN_DATA_ROOT=/opt/mediadrop-modern-data
LEGACY_DATA_ROOT=/opt/mediadrop_data
```

Создайте writable-каталоги:

```sh
sudo mkdir -p \
  /opt/mediadrop-modern-data/media \
  /opt/mediadrop-modern-data/images \
  /opt/mediadrop-modern-data/modern \
  /opt/mediadrop-modern-data/logs
```

Соберите image и узнайте UID/GID пользователя приложения вместо жёсткого числового UID:

```sh
docker compose build app
APP_UID="$(docker compose run --rm --no-deps --entrypoint id app -u)"
APP_GID="$(docker compose run --rm --no-deps --entrypoint id app -g)"
echo "$APP_UID:$APP_GID"
```

Выдайте права только на modern writable root:

```sh
sudo chown -R "$APP_UID:$APP_GID" /opt/mediadrop-modern-data
```

Legacy root владельца менять обычно не требуется. Важно, чтобы контейнер мог его читать.
Проверка на хосте:

```sh
sudo test -d /opt/mediadrop_data/media
sudo test -d /opt/mediadrop_data/images
```

## 4. Проверка mounts до запуска

Посмотрите итоговый Compose после подстановки `.env`:

```sh
docker compose config
```

После старта:

```sh
docker compose exec app sh -c '
  echo "modern media:";  ls -ld /data/media;
  echo "modern images:"; ls -ld /data/images;
  echo "modern db:";     ls -ld /data/modern;
  echo "logs:";          ls -ld /data/logs;
  echo "legacy media:";  ls -ld /data/legacy-media;
  echo "legacy images:"; ls -ld /data/legacy-images
'
```

Проверка read-only legacy mounts:

```sh
docker compose exec app sh -c '
  touch /data/legacy-media/.write-test 2>/dev/null && {
    echo "ERROR: legacy media is writable" >&2; rm -f /data/legacy-media/.write-test; exit 1;
  } || echo "OK: legacy media is read-only"
'
```

Проверка writable roots:

```sh
docker compose exec app sh -c '
  touch /data/media/.write-test && rm /data/media/.write-test &&
  touch /data/images/.write-test && rm /data/images/.write-test &&
  touch /data/modern/.write-test && rm /data/modern/.write-test &&
  touch /data/logs/.write-test && rm /data/logs/.write-test
'
```

## 5. Fresh installation

Для **новой пустой** установки:

```sh
cp .env.example .env
# Отредактировать .env.

docker compose build
docker compose up -d db
docker compose run --rm app mediadrop init-db
docker compose run --rm app mediadrop create-admin admin admin@example.com
docker compose up -d
```

`mediadrop init-db` нельзя запускать поверх импортированной legacy database.

Проверка:

```sh
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

## 6. Перенос существующего MediaDrop

Для миграции существующей установки:

1. Сделать backup SQL и старого каталога данных.
2. Восстановить legacy SQL в MariaDB volume.
3. Скопировать старые `media/` и `images/` в `${LEGACY_DATA_ROOT}`.
4. Убедиться, что Compose монтирует их `:ro`.
5. Не запускать `mediadrop init-db`.
6. Запустить read-only проверки.
7. Только после этого запускать приложение.

Проверки:

```sh
docker compose run --rm app python scripts/check_legacy_db.py
docker compose run --rm app python scripts/check_legacy_thumbnails.py
docker compose run --rm app python scripts/list_unplayable_media.py
```

Эти helpers предназначены для проверки и не должны менять legacy source.

Дополнительные migration notes: `MIGRATION_NOTES.md`.

## 7. Reverse proxy и отдача видео

По умолчанию `.env.example` использует безопасный local-only bind:

```dotenv
BIND_ADDR=127.0.0.1
HTTP_PORT=8080
MEDIA_SERVE_MODE=app
```

Если nginx находится **на другом сервере**, `127.0.0.1` не подходит: порт приложения
должен быть привязан к private-интерфейсу Docker-хоста, доступному только reverse proxy
и административной сети. Например:

```dotenv
BIND_ADDR=10.0.63.135
HTTP_PORT=8080
MEDIA_SERVE_MODE=app
```

Можно использовать `0.0.0.0`, но тогда доступ к `8080/tcp` должен быть ограничен
firewall/security group. Предпочтительнее привязаться к конкретному private IP.

`MEDIA_SERVE_MODE=app` — правильный режим, когда nginx является **внешним корпоративным
reverse proxy** и не имеет общего filesystem с Docker-хостом MediaDrop. Nginx принимает
HTTPS и proxy_pass на Uvicorn, а byte-range/206 response формирует приложение.

Пример:

```nginx
location / {
    proxy_pass http://APP_HOST:8080;
    proxy_http_version 1.1;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

Начиная с 0.6.4 video range requests не удерживают DB connection на время передачи
файла, поэтому частая перемотка не должна исчерпывать SQLAlchemy connection pool.

### Когда использовать `MEDIA_SERVE_MODE=nginx`

Только если nginx может физически прочитать те же media files и настроен
`X-Accel-Redirect`/`internal location`.

Если nginx находится на отдельном корпоративном сервере и видит приложение только по
HTTP, оставляйте `MEDIA_SERVE_MODE=app`.

## 8. Обновление версии без потери данных

Код и runtime data разделены. Обновление не должно заменять:

- `.env`;
- MariaDB named volume;
- `${MODERN_DATA_ROOT}`;
- `${LEGACY_DATA_ROOT}`.

Рекомендуемая последовательность:

```sh
# 1. Backup до обновления.
./scripts/backup.sh

# 2. Распаковать новый код/заменить working tree, сохранив .env.

# 3. Проверить, что project name и пути остались прежними.
grep -E '^(COMPOSE_PROJECT_NAME|MODERN_DATA_ROOT|LEGACY_DATA_ROOT)=' .env
docker compose config --volumes

# 4. Пересобрать и перезапустить.
docker compose build --no-cache app
docker compose up -d

# 5. Проверить.
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

**Не используйте `docker compose down -v` в обычном upgrade.** Опция `-v` удаляет
named volumes и может удалить MariaDB volume.

## 9. Что нужно бэкапить

Обязательно:

- MariaDB — логическим dump;
- `${MODERN_DATA_ROOT}/modern/mediadrop-modern.db`;
- `${MODERN_DATA_ROOT}/media`;
- `${MODERN_DATA_ROOT}/images`;
- `.env` и актуальный `compose.yml`.

Audit logs зависят от требований хранения логов; обычно их сохраняют отдельно или
через системный log backup.

Legacy media/images не входят в `scripts/backup.sh`, потому что это отдельный большой
migration source. Их backup/retention должен существовать независимо.

Подробно: `docs/OPERATIONS_RU.md`.
