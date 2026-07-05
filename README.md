# Лаборатория гипотез

MVP для обогатительных фабрик: показывает, **где теряется металл** в хвостах, генерирует
и оценивает **гипотезы улучшения**, строит план эксперимента и учится на результатах опытов.

Ключевой принцип: **все числа (эффект, деньги, ранжирование, конфликты) считает
детерминированный движок, а не LLM.** LLM отвечает только за текст карточек и поиск по
литературе (RAG). Это и есть главный аргумент «не чёрный ящик».

---

## Как это работает (поток)

```
Матрица потерь → Диагноз → Гипотезы → Оценка (движок) → Карточка → Дорожная карта → Лаборатория
    (XLSX)      где теряем   система/    эффект в т и $   для НИОКР   граф этапов    опыт калибрует
                            эксперт     + ранжирование                             правило, копит тупики
```

- **Матрица потерь** — `класс крупности × минеральная форма × металл`. Загружается из XLSX.
- **Движок** оценивает каждую гипотезу по правилам: коэффициент возврата берётся из
  **кривой извлечения** (лог-нормаль по крупности × раскрытие), тонны → деньги по цене металла.
- **Ранжирование** — взвешенная сумма (эффект, вероятность, покрытие, реализуемость,
  штрафы за риск/тупик/конфликт за ячейку). Веса настраиваются в конфиге.
- **Лаборатория** — результат реального опыта возвращается в систему: успех калибрует
  коэффициент правила (взвешенно, `η=1/√(N+1)`), провал заносит **тупик**, который
  переносится и на другие фабрики.
- **RAG (Литература)** — поиск обоснований в локальном корпусе PDF/DOCX через Yandex Cloud.

---

## Архитектура

| Компонент | Что это | Технологии |
|---|---|---|
| `backend/` | API, движок оценки, генерация, RAG, экспорт | FastAPI, SQLAlchemy 2 |
| `backend/engine/` | детерминированный расчёт (кривая, ранжирование, калибровка, портфель) | чистый Python |
| `backend/rag/` | корпус, эмбеддинги, поиск | Yandex Cloud Foundation Models |
| `frontend/` | UI по стадиям | Streamlit |
| `db/` | схема + demo-seed | PostgreSQL + pgvector |

Docker-стек (`docker-compose.yml`):

- **db** — PostgreSQL с pgvector → `localhost:5433`
- **api** — FastAPI → `localhost:8000` (Swagger на `/docs`)
- **frontend** — Streamlit UI → `localhost:8501`
- **seed** — разовое наполнение demo-данными (профиль `tools`)
- **corpus-index** — разовая индексация литературы (профиль `tools`)

---

## Быстрый запуск

Нужен **Docker Desktop** (свободные порты 5433, 8000, 8501).

```powershell
.\start.bat
```

Скрипт создаёт `.env`, поднимает `db + api + frontend`, наполняет demo-данные,
индексирует корпус и открывает UI. После старта: **UI — http://localhost:8501**,
API/Swagger — http://localhost:8000/docs.

Остановить (данные БД сохраняются): `.\stop.bat`

### Вручную

```powershell
Copy-Item .env.example .env
docker compose up -d                                  # db + api + frontend
docker compose --profile tools run --rm seed          # demo: 2 фабрики, правила, матрицы
docker compose --profile tools run --rm corpus-index  # индексация корпуса (нужны ключи Yandex)
```

---

## RAG требует ключей Yandex Cloud

Числовой контур (диагноз, оценка, ранжирование, карточки) работает **без** LLM.
Ключи нужны только для вкладки **Литература** и генерации текста карточек через YandexGPT.
Пропиши в `.env`:

```dotenv
YANDEX_API_KEY=<ключ>
YANDEX_FOLDER_ID=<folder id>
```

Корпус литературы лежит в папке **`Задача 1`** в корне проекта (монтируется в контейнеры
как `/corpus`). Без ключей UI и движок всё равно запускаются; поиск по корпусу и
LLM-карточки уходят в fallback. `start.bat` предупредит, если ключей нет в `.env`.

---

## Структура проекта

```text
nornikel/
├─ backend/
│  ├─ main.py            # FastAPI-эндпоинты
│  ├─ engine/            # движок: base, common (кривая), rank, learn, portfolio, модули
│  ├─ rag/               # yandex_client, indexer, retriever, loader
│  ├─ export_pdf.py      # PDF-дашборды (портфель / карточка / матрица)
│  ├─ ingest.py          # разбор XLSX-матрицы
│  ├─ docingest.py       # разбор DOCX-гипотез
│  └─ models.py / schemas.py / config.py / db.py
├─ frontend/app.py       # Streamlit UI (стадии + единый визуальный язык)
├─ db/                   # schema.sql, seed.py
├─ Задача 1/             # корпус литературы для RAG
├─ docker-compose.yml / Dockerfile / requirements.txt
├─ start.bat / stop.bat  # запуск/остановка на Windows
└─ .env.example
```

---

## Основные эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/plants/{id}/diagnosis` | матрица и извлекаемость потерь |
| POST | `/plants/{id}/generate` | генерация гипотез по тяжёлым ячейкам |
| POST | `/plants/{id}/evaluate` | оценка движком + пересбор рейтинга |
| GET | `/plants/{id}/ranking` | рейтинг с мини-отчётами модулей, тупиками, конфликтами |
| POST | `/plants/{id}/ingest-bundle` | единый вход: общий промт + пары файл/промт |
| GET | `/hypotheses/{id}/card` | текстовая карточка (числа из движка) |
| POST | `/hypotheses/{id}/roadmap` | дорожная карта эксперимента |
| POST | `/roadmap/{step}/artifact` | результат опыта → калибровка / тупик |
| GET | `/export/{portfolio\|matrix}.{csv\|pdf}` | экспорт дашбордов |
| POST | `/corpus/ask` · GET `/corpus/search` | RAG по литературе |

Полная спецификация — Swagger на http://localhost:8000/docs.

---

## Разработка (быстрые итерации)

```powershell
docker compose up -d db                     # только БД
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m db.seed
uvicorn backend.main:app --reload           # backend
streamlit run frontend/app.py               # frontend (в другом окне)
```

Backend требует PostgreSQL с pgvector (SQLite не подойдёт). API и frontend в compose
смонтированы с `--reload`, поэтому правки в коде подхватываются без пересборки образа.

---

## Ограничения MVP

Правила и коэффициенты — в demo-логике, seed-данные синтетические, набор модулей
ограничен (`regrind`, `classification`, `fine_flotation`), RAG работает по локальной папке.
Числовой контур при этом полностью детерминированный и не зависит от LLM.
