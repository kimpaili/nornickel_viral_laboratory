#!/usr/bin/env bash
# Остановка стека на сервере. Данные БД и корпус сохраняются в volume.
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "============================================"
echo "   ЛАБОРАТОРИЯ ГИПОТЕЗ  —  STOP (server)"
echo "============================================"

if ! docker version >/dev/null 2>&1; then
  echo "[warn] Docker недоступен — останавливать нечего."
  exit 0
fi

echo "[stop] Останавливаю и удаляю контейнеры. Данные БД сохраняются ..."
$COMPOSE down

echo "Готово. Полный сброс с очисткой данных:  $COMPOSE down -v"
