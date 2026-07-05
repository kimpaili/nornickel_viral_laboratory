#!/usr/bin/env bash
# Запуск стека на Linux-сервере (прод-профиль).
# UI -> http://<сервер>:${APP_PORT:-80}, API/Swagger -> http://<сервер>:8000/docs
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "============================================"
echo "   ЛАБОРАТОРИЯ ГИПОТЕЗ  —  START (server)"
echo "   dir: $(pwd)"
echo "============================================"

# --- 1) Docker ---
if ! docker version >/dev/null 2>&1; then
  echo "[ERROR] Docker недоступен. Установи Docker Engine + compose-plugin и запусти демон."
  exit 1
fi
echo "[ok] Docker доступен."

# --- 2) .env ---
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[warn] Создан .env из .env.example — впиши реальные ключи и СМЕНИ POSTGRES_PASSWORD."
fi

# --- 3) Проверка ключей Yandex (только предупреждение) ---
if grep -Eq '^YANDEX_API_KEY=.+' .env && grep -Eq '^YANDEX_FOLDER_ID=.+' .env; then
  echo "[ok] Ключи Yandex Cloud найдены в .env."
else
  echo "[warn] YANDEX_API_KEY / YANDEX_FOLDER_ID не заданы — вкладка «Литература» и LLM-карточки"
  echo "       уйдут в fallback. Числовой движок работает без них."
fi

# --- 4) Сборка и запуск ---
echo "[run] Сборка образа и запуск: db + api + frontend ..."
$COMPOSE up -d --build

# --- 5) Разовые задачи ---
echo "[db] Наполнение demo-данными (2 фабрики, правила, матрицы) ..."
$COMPOSE --profile tools run --rm seed

echo "[rag] Индексация корпуса (первый прогон может занять 1-2 минуты) ..."
$COMPOSE --profile tools run --rm corpus-index

# --- 6) Ожидание готовности API ---
echo "[wait] Ожидание готовности API ..."
for i in $(seq 1 30); do
  if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
    echo "[ok] API готов."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "[warn] API не ответил за ~60с. Смотри логи: $COMPOSE logs api"
  fi
  sleep 2
done

APP_PORT="$(grep -E '^APP_PORT=' .env | cut -d= -f2 || true)"
APP_PORT="${APP_PORT:-80}"

echo "============================================"
echo "   UI:            http://<сервер>:${APP_PORT}"
echo "   API / Swagger:  http://<сервер>:8000/docs"
echo "   Остановить:     ./stop.sh   (данные БД сохраняются)"
echo "============================================"
