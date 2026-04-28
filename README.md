# rates-api

`rates-api` - проект для агрегации и публикации курсов RUB/THB из разных источников.  
В репозитории есть:
- CLI для сводок и отчетов (`rates.py`);
- Telegram-бот (`bot/main.py`);
- userbot для сбора данных из Telegram-каналов (`userbot/main.py`);
- chat-agent (FastAPI + Redis + LLM) для диалога через бота;
- Docker-конфигурация для совместного деплоя сервисов.

## Что делает проект

Основной сценарий - получить актуальную сводку "сколько RUB за 1 THB" и смежные отчеты:
- сводка каналов перевода/обмена (`rates.py`);
- отчет по USDT-маршрутам (`rates.py usdt`);
- отчеты по наличным и TT Exchange (`rates.py cash`, `rates.py exchange`);
- расчетные сценарии (`rates.py calc`, `rates.py rshb`).

Данные кешируются в JSON-файлах в `.rates_cache` (или по путям из переменных окружения), чтобы снизить нагрузку на внешние API и ускорить ответы.

## Структура компонентов

- `rates.py` - основной CLI и единая точка входа по курсам.
- `bot/main.py` - Telegram-бот с командами `/rates`, `/usdt`, `/cash`, `/exchange`, `/calc`, `/rshb`.
- `userbot/main.py` - сбор котировок из Telegram-каналов в кеш.
- `chat_agent/` - HTTP API для диалогов (поддержка OpenAI/Google, Redis-сессии, аудит в PostgreSQL).
- `sources/` - интеграции конкретных источников курсов.
- `docker-compose.yml` - оркестрация сервисов для деплоя.

## Требования

- Python 3.12 (рекомендуется, совпадает с Docker-образами);
- Linux-сервер/VPS для продакшн-деплоя;
- Docker + Docker Compose (для контейнерного запуска);
- Redis и PostgreSQL (нужны для `chat-agent`, в compose поднимаются автоматически).

## Быстрый старт (локально без Docker)

1. Создайте и активируйте виртуальное окружение.
2. Установите зависимости:
   - базовые: `pip install -r requirements.txt`
   - для бота: `pip install -r requirements-bot.txt`
   - для chat-agent: `pip install -r requirements-chat_agent.txt`
3. Создайте `.env` в корне (можно взять за основу `bot/.env.example`).
4. Проверьте запуск CLI:
   - `python3 rates.py`
   - `python3 rates.py --help`

Пример запуска отдельных компонентов:
- CLI: `python3 rates.py`
- бот: `python3 -m bot.main`
- userbot: `python3 -m userbot.main`
- chat-agent: `uvicorn chat_agent.app.main:app --host 0.0.0.0 --port 18880`

## Способы деплоя

## 1) Docker Compose (рекомендуемый)

Проект уже содержит `docker-compose.yml` со следующими сервисами:
- `rates-api` - CLI контейнер;
- `rates-bot` - Telegram-бот;
- `userbot` - userbot;
- `chat-agent` - FastAPI сервис;
- `redis` и `postgres` - инфраструктура для chat-agent.

Шаги:
1. Подготовьте `.env` в корне.
2. (Опционально) задайте `HOST_UID` и `HOST_GID`, чтобы корректно писать кеши и сессии на хосте.
3. Запустите:
   - `docker compose up -d --build`
4. Проверьте логи:
   - `docker compose logs -f rates-bot`
   - `docker compose logs -f chat-agent`

Остановка:
- `docker compose down`

## 2) Раздельный деплой сервисов (без Compose)

Если нужен более гранулярный контроль (например, разные хосты/контейнеры):
- соберите `Dockerfile.bot` для `rates-bot` и `userbot`;
- соберите `Dockerfile.chat_agent` для `chat-agent`;
- отдельно поднимите Redis/PostgreSQL;
- передайте одинаковый `.env` (или эквивалентный набор env-переменных) в каждый сервис.

Этот вариант удобен для Kubernetes, Nomad или ручной systemd-оркестрации контейнеров.

## 3) Нативный деплой на сервере (systemd + venv)

Подходит, если Docker не используется:
- разверните код на сервере;
- создайте Python venv, установите зависимости;
- создайте `.env` в корне;
- поднимите процессы `bot`, `userbot`, `chat-agent` как systemd-сервисы;
- Redis/PostgreSQL установите как отдельные системные сервисы.

Рекомендуется добавить рестарт-политику и логирование через `journalctl`.

## Конфигурация (.env)

Файл `.env` читается при старте `rates.py`, `bot` и `chat-agent` (уже заданные в shell переменные имеют приоритет).

Ниже - основные группы переменных.

## Telegram bot

Обязательные:
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_BOT_TOKEN`

Опциональные:
- `BOT_ADMIN_ID` - кто может вызывать `/refresh`;
- `BOT_FETCH_TIMEOUT_SEC` - таймаут фоновой сборки;
- `TELETHON_SESSION_DIR` - директория сессии Telethon;
- `BOT_GPT_USERS_FILE`, `BOT_GPT_PENDING_FILE` - файлы доступа к GPT-режиму.

## Chat-agent

Обязательные для работы API:
- `CHAT_AGENT_SHARED_SECRET`
- `REDIS_URL`

Сетевые настройки:
- `CHAT_AGENT_HOST` (по умолчанию `0.0.0.0`)
- `CHAT_AGENT_PORT` (по умолчанию `18880`)

Провайдер LLM:
- `CHAT_AGENT_LLM_PROVIDER=openai|google`
- для OpenAI: `OPENAI_API_KEY`, `OPENAI_API_URL`, `OPENAI_MODEL` (опционально planner/responder модели);
- для Google: `GOOGLE_API_KEY` или `GEMINI_API_KEY`, `GEMINI_MODEL`.

Аудит (PostgreSQL):
- `CHAT_AGENT_AUDIT_ENABLED`
- `CHAT_AGENT_DATABASE_URL` (или набор `CHAT_AGENT_PG_USER`, `CHAT_AGENT_PG_PASSWORD`, `CHAT_AGENT_PG_HOST`, `CHAT_AGENT_PG_PORT`, `CHAT_AGENT_PG_DB`)

## Кеши и производительность

- `RATES_CACHE_FILE` - legacy-кеш сводки;
- `RATES_USDT_CACHE_FILE` - legacy USDT-кеш;
- `RATES_UNIFIED_CACHE_FILE` - unified-кеш (основной);
- `RATES_PARALLEL_MAX_WORKERS` - число потоков для параллельных запросов;
- `RATES_HTTP_MAX_ATTEMPTS`, `RATES_HTTP_BACKOFF_BASE` - общие retry-параметры HTTP.

## Источники и интеграции

- `BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY` - доступ к Bangkok Bank API;
- `BANGKOKBANK_HTTP_TIMEOUT_SEC`, `BANGKOKBANK_HTTP_MAX_ATTEMPTS`, `BANGKOKBANK_HTTP_BACKOFF_BASE`;
- `RATES_DISABLE_RBC`, `RATES_DISABLE_BANKI`, `RATES_DISABLE_VBR` - отключение отдельных источников cash-отчета.
- `SBER_QR_HOSTNAME`, `SBER_QR_UFS_TOKEN`, `SBER_QR_UFS_SESSION`, `SBER_QR_LINK` - параметры запроса для cron-обновления источника Сбербанк QR;
- `SBER_QR_TIMEOUT_SEC`, `SBER_QR_VERIFY_SSL` - таймаут и проверка SSL для Sberbank QR cron.

### Sberbank QR refresh cron

Source `sberbank_qr` does not call external API directly.  
It reads `prim:sber_qr_transfer` from unified cache.

Refresh command:

`python cron/refresh_sberbank_qr.py`

Crontab example:

`*/10 * * * * cd /home/coderus/rates-api && /usr/bin/python3 cron/refresh_sberbank_qr.py >> /var/log/sber_qr_refresh.log 2>&1`

## Минимальный пример `.env`

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_BOT_TOKEN=
BOT_ADMIN_ID=

CHAT_AGENT_SHARED_SECRET=
REDIS_URL=redis://127.0.0.1:6379/0
CHAT_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_API_URL=

BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY=
RATES_UNIFIED_CACHE_FILE=.rates_cache/.rates_unified_cache.json
```

## Полезные команды

- `python3 rates.py --help` - список команд CLI;
- `python3 rates.py sources` - доступные источники;
- `python3 rates.py env-status` - проверка наличия ключевых переменных;
- `python3 -m unittest discover -s tests -q` - запуск тестов.

## Документация в репозитории

- `USAGE.md` - подробное руководство по всем CLI-командам и опциям;
- `openrouter/README.md` - документация openrouter-прокси;
- `userbot/README.md` - детали по userbot.
