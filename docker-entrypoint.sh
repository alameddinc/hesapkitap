#!/bin/sh
# Linux'ta bind mount edilen ./data klasörü yoksa Docker onu root sahipliğinde oluşturur;
# uygulama root olmayan kullanıcıyla çalıştığı için önce sahipliği düzelt, sonra yetkileri bırak.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data
  if [ "$(stat -c %u /app/data)" != "$(id -u app)" ]; then
    chown -R app:app /app/data
  fi
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
