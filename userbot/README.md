# Userbot (Telegram user client)

`userbot` читает сообщения в указанных каналах/чатах, парсит курсы по regex и пишет их в unified cache (`chatcash:*`).

## Где вписать chat id, название и тип источника

Редактируйте файл `userbot/sources_config.py`.

Для каждого источника:

- `source_id` — стабильный id в кеше
- `name` — человекочитаемое имя в сводке
- `chat` — `@username` канала или числовой chat id (например `-1001234567890`)
- `currencies` — список валют и regex-шаблонов
  - `category`: `cash_rub` / `cash_usd` / `cash_eur` / `cash_cny`
  - `pattern` должен содержать именованную группу `(?P<rate>...)`

## Логин userbot

1. Заполните env:
   - `USERBOT_API_ID`
   - `USERBOT_API_HASH`
   - `USERBOT_PHONE` (например `+79990001122`)
2. Разово выполните логин:
   - локально: `python -m userbot.main --login`
   - в docker: `docker compose run --rm userbot python -m userbot.main --login`
3. Введите код из Telegram (и 2FA пароль, если включен).
4. После этого запускайте:
   - `docker compose up -d userbot`

Сессия хранится в `USERBOT_SESSION_DIR` (в compose это volume `userbot_session`).

