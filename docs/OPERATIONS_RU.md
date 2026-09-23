# Эксплуатация MediaDrop Modern

Документ актуален для **0.6.6** и предполагает deployment через `compose.yml`.

## 1. Ежедневная проверка

```sh
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

Логи приложения:

```sh
docker compose logs --tail=200 app
```

Логи MariaDB:

```sh
docker compose logs --tail=200 db
```

Audit log внутри контейнера:

```sh
docker compose exec app tail -n 100 /data/logs/audit.log
```

При default mounts тот же файл на хосте:

```text
/opt/mediadrop-modern-data/logs/audit.log
```

## 2. Backup

В комплект входит `scripts/backup.sh`. Скрипт запускается **на Docker-хосте** и
создаёт согласованный набор:

- `mariadb.sql.gz` — logical dump основной базы;
- `mediadrop-modern.db` — SQLite backup через SQLite backup API;
- `files.tar.gz` — новые `/data/media` и `/data/images`;
- `env.backup`;
- `compose.yml`;
- `SHA256SUMS`.

Пример:

```sh
cd /opt/mediadrop
PROJECT_DIR=/opt/mediadrop \
BACKUP_ROOT=/opt/backups/mediadrop \
RETENTION_DAYS=30 \
./scripts/backup.sh
```

Скрипт читает media/images **через контейнер**, поэтому он продолжает работать при
изменении `MODERN_DATA_ROOT` в `.env` и не зависит от конкретного host path.

### Что backup.sh намеренно не копирует

`/data/legacy-media` и `/data/legacy-images` не включаются в `files.tar.gz`.
Это большой read-only migration source и у него должен быть отдельный backup/retention.

## 3. Проверка backup

После каждого backup полезно проверять manifest:

```sh
cd /opt/backups/mediadrop/YYYYmmdd-HHMMSS
sha256sum -c SHA256SUMS
```

Проверка MariaDB dump без восстановления:

```sh
gzip -t mariadb.sql.gz
```

Проверка архива новых файлов:

```sh
gzip -t files.tar.gz
tar -tzf files.tar.gz | head
```

Проверка SQLite:

```sh
python3 - <<'PY'
import sqlite3
p = 'mediadrop-modern.db'
con = sqlite3.connect(p)
print(con.execute('PRAGMA integrity_check').fetchone()[0])
con.close()
PY
```

Ожидаемый результат: `ok`.

## 4. Restore — общая последовательность

Restore сначала делайте на отдельном хосте/стенде.

1. Восстановить `.env` и `compose.yml`.
2. Проверить `COMPOSE_PROJECT_NAME`, `MODERN_DATA_ROOT`, `LEGACY_DATA_ROOT`.
3. Поднять только MariaDB.
4. Восстановить `mariadb.sql.gz`.
5. Восстановить modern SQLite.
6. Распаковать новые media/images.
7. Подключить legacy read-only archive.
8. Запустить app и проверить `/healthz`.

### MariaDB

```sh
docker compose up -d db
zcat mariadb.sql.gz | docker compose exec -T db sh -c \
  'exec mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"'
```

### Modern SQLite

При default paths:

```sh
sudo install -m 0640 mediadrop-modern.db \
  /opt/mediadrop-modern-data/modern/mediadrop-modern.db
```

После restore убедитесь, что файл доступен UID/GID приложения.

### New media/images

```sh
sudo tar -C /opt/mediadrop-modern-data -xzf files.tar.gz
```

Архив содержит каталоги `media/` и `images/`.

## 5. Обновление приложения

Перед обновлением:

```sh
./scripts/backup.sh
docker compose ps
docker compose config --volumes
```

Убедитесь, что `.env` сохраняет:

```dotenv
COMPOSE_PROJECT_NAME=mediadrop-modern
MODERN_DATA_ROOT=/opt/mediadrop-modern-data
LEGACY_DATA_ROOT=/opt/mediadrop_data
```

После замены кода:

```sh
docker compose build --no-cache app
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

Не выполняйте `docker compose down -v`, если не собираетесь сознательно удалять данные.

## 6. Проверка storage после обновления

```sh
docker compose exec app sh -c '
  set -e
  test -d /data/media
  test -d /data/images
  test -f /data/modern/mediadrop-modern.db
  test -d /data/legacy-media
  test -d /data/legacy-images
  echo "storage mounts: OK"
'
```

Сравните mounts с ожидаемыми:

```sh
docker inspect "$(docker compose ps -q app)" \
  --format '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}'
```

Для production-default ожидается примерно:

```text
/opt/mediadrop-modern-data/media   -> /data/media          rw=true
/opt/mediadrop-modern-data/images  -> /data/images         rw=true
/opt/mediadrop-modern-data/modern  -> /data/modern         rw=true
/opt/mediadrop-modern-data/logs    -> /data/logs           rw=true
/opt/mediadrop_data/media          -> /data/legacy-media   rw=false
/opt/mediadrop_data/images         -> /data/legacy-images  rw=false
```

MariaDB будет отдельным named volume.

## 7. Видео и Range requests

Нормальное воспроизведение HTML5 video использует HTTP Range и ответы `206 Partial Content`.
При активной перемотке много последовательных `206` в access/app log — нормально.

Начиная с 0.6.4 DB session для `/files/*` закрывается до фактической отправки файла.
Ошибка вида:

```text
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached
```

при частой перемотке больше не должна возникать из-за длительной отдачи видео.

Если она снова появляется, соберите:

```sh
docker compose logs --since=10m app
docker compose logs --since=10m db
```

и отдельно проверьте, не появились ли новые endpoints, которые держат DB session на
время streaming response.

## 8. Внешний nginx

Если nginx находится на другом сервере и делает только `proxy_pass`, используйте:

```dotenv
MEDIA_SERVE_MODE=app
```

Увеличение `proxy_read_timeout` не заменяет исправление ошибок приложения и не должно
использоваться как способ скрыть DB timeout.

`MEDIA_SERVE_MODE=nginx` предназначен только для deployment, где nginx видит media
filesystem и настроен на `X-Accel-Redirect`.

## 9. LDAP и SECRET_KEY

`SECRET_KEY` нужно сохранять между обновлениями и restore. От него зависит:

- валидность session cookies;
- расшифровка сохранённого LDAP bind password.

Смена `SECRET_KEY` не повреждает legacy content, но завершает существующие сессии и
потребует заново сохранить LDAP bind password.

## 10. Что проверять после restore/cutover

Минимальный smoke test:

```text
[ ] /healthz = 200
[ ] главная страница открывается через production reverse proxy
[ ] local admin login работает
[ ] LDAP login работает, если включён
[ ] старый MP4 воспроизводится из legacy-media
[ ] новый upload попадает в /data/media
[ ] preview попадает в /data/images
[ ] быстрая перемотка не вызывает QueuePool timeout
[ ] комментарий авторизованного пользователя получает identity из учётной записи
[ ] download UI соответствует настройке сайта
[ ] audit.log получает новые события
[ ] backup завершается и SHA256SUMS проходит проверку
```
