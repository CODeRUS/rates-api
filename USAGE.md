# Руководство по CLI и скриптам (rates-api)

Документ описывает **все точки входа на Python вне каталога `cron/`**: `rates.py`, бот, userbot, вспомогательные модули в `scripts/` и `sources/**` с `argparse` / `if __name__ == "__main__"`.  
Запуск из **корня репозитория**; пример интерпретатора: `python3.9`.

---

## Содержание

1. [Главная точка входа: `rates.py`](#1-главная-точка-входа-ratespy)  
2. [Общие опции сводки (сводная таблица)](#2-общие-опции-сводки-сводная-таблица)  
3. [Каждая глобальная опция: пример команды и вывод](#3-каждая-глобальная-опция-пример-команды-и-вывод)  
4. [Встроенные команды `rates.py`](#4-встроенные-команды-ratespy)  
5. [Пресет `--filter`](#5-пресет---filter)  
6. [Один источник: `rates.py <source_id>`](#6-один-источник-ratespy-source_id)  
7. [Справочник CLI плагинов (все флаги и примеры)](#7-справочник-cli-плагинов-все-флаги-и-примеры)  
8. [Кеши и переменные окружения](#8-кеши-и-переменные-окружения)  
9. [Параллелизм](#9-параллелизм)  
10. [`bot/main.py`](#10-botmainpy)  
11. [`userbot/main.py`](#11-userbotmainpy)  
12. [`scripts/bkb_probe_latestfxrates.py`](#12-scriptsbkb_probe_latestfxratespy)  
13. [Модули только для импорта](#13-модули-только-для-импорта)  
14. [Тесты](#14-тесты)  
15. [Встроенный текст `rates.py --help`](#15-встроенный-текст-ratespy---help)

---

## 1. Главная точка входа: `rates.py`

Файл: **`rates.py`**.  
Подгружает `.env` из корня репо через `env_loader.load_repo_dotenv` (уже заданные в shell переменные **не перезаписываются**).

### Без аргументов — текстовая сводка RUB за 1 THB

```bash
python3.9 rates.py
```

Собирает строки из unified-кеша (L2 / L1 / примитивы) согласно логике `compute_summary_rows`; первая базовая строка — Forex (база для процентов в тексте).

**Пример фрагмента вывода** (данные из кеша, формат может отличаться):

```text
Карты UnionPay РСХБ
📈 2.458 | Forex
💳 2.452 | -0.3% | РСХБ UP CNY (брокер, оплата)
…

Перевод RUB ➔ THB
💸 2.463 | +0.2% | Bybit P2P (перевод) → Bitkub
…
```

### Справка

```bash
python3.9 rates.py --help
python3.9 rates.py -h
```

Печатает `argparse` help плюс блок «Команды» и краткое описание каждого зарегистрированного источника (как у `rates.py sources` + расшифровка).

---

## 2. Общие опции сводки (сводная таблица)

Задаются **до** подкоманды (парсер `build_arg_parser` в `rates.py`). Взаимоисключающая группа: **`--refresh` | `--readonly`** (в argparse).

| Опция | Тип / по умолчанию | Назначение |
|--------|-------------------|------------|
| `-h`, `--help` | флаг | Справка + блок команд и список источников. |
| `--refresh` | флаг | Игнорировать кеш L1/L2 при сборке сводки (полная пересборка, сеть). |
| `--readonly` | флаг | Только unified + legacy файлы, без HTTP. |
| `--json` | флаг | Сводка в JSON в stdout. |
| `--thb-ref` | float, `30000` | Нетто THB для строк сценария снятия (РСХБ / Korona в логике сводки). |
| `--atm-fee` | float, `250` | Комиссия банкомата, THB. |
| `--korona-small` | float | Порог «малых» сумм Korona (RUB); дефолт из `RUB_MIN_SENDING_FOR_BEST_TIER - 1` в `rates.py`. |
| `--korona-large-thb` | float, `40000` | THB для строки Korona «крупная передача». |
| `--avosend-rub` | float, `50000` | Порог RUB для строк Avosend. |
| `--unionpay-date` | строка или пусто | `YYYY-MM-DD` для UnionPay JSON. |
| `--moex-override` | float или пусто | Ручная подстановка MOEX CNY/RUB. |
| `--cache-file` | `Path` | Legacy файл сводки (миграция в unified); см. `RATES_CACHE_FILE`. |
| `--filter` | строка | Имя пресета постфильтрации (`rates_output_filters`). |
| `--gpt` | строка | Пользовательский промпт к Chat API; **без** хвоста подкоманд. |

---

## 3. Каждая глобальная опция: пример команды и вывод

Ниже для **каждой** опции: что делает, пример запуска и **образец результата** (значения чисел зависят от кеша и даты).

### `--help` / `-h`

```bash
python3.9 rates.py --help
```

Вывод: стандартный блок `usage:` + `optional arguments:` + текст «Команды:» + многострочный список источников (как в [разделе 15](#15-встроенный-текст-ratespy---help)).

### `--readonly` (сводка без сети)

```bash
python3.9 rates.py --readonly
```

**Пример начала вывода:**

```text
Карты UnionPay РСХБ
📈 2.458 | Forex
💳 2.452 | -0.3% | РСХБ UP CNY (брокер, оплата)
…
```

Если кеш пуст: сообщение в stderr «`--readonly: нет данных сводки в кеше.`», код выхода `1`.

### `--json`

```bash
python3.9 rates.py --readonly --json
```

**Пример фрагмента stdout:**

```json
{
  "baseline_rub_per_thb": 2.458223619602424,
  "rows": [
    {
      "rate": 2.458223619602424,
      "label": "Forex",
      "emoji": "📈",
      "note": "",
      "is_baseline": true,
      "category": "TRANSFER",
      "compare_to_baseline": true,
      "cash_rub_seq": 0,
      "merge_key": null
    }
  ],
  "warnings": []
}
```

### `--thb-ref` и `--atm-fee`

Меняют параметры сценариев «снятие THB + комиссия ATM» в строках РСХБ / Korona внутри сводки.

```bash
python3.9 rates.py --readonly --thb-ref 25000 --atm-fee 220
```

Вывод — тот же формат таблицы сводки, изменятся подписи вида «снятие 25000+220» и числовые курсы для этих строк.

### `--korona-small`, `--korona-large-thb`, `--avosend-rub`

Влияют на пороги сумм в подписях/логике строк Korona и Avosend.

```bash
python3.9 rates.py --readonly --korona-large-thb 50000 --avosend-rub 70000
```

### `--unionpay-date`

```bash
python3.9 rates.py --readonly --unionpay-date 2026-03-20
```

Подставляет дату при загрузке UnionPay JSON для ветки РСХБ; вывод — текст сводки с пересчитанными CNY-путями (при наличии данных за дату).

### `--moex-override`

```bash
python3.9 rates.py --readonly --moex-override 12.34
```

Фиксирует CNY/RUB для расчётов вместо живого MOEX (удобно для сверки с таблицами).

### `--cache-file`

Указывает **альтернативный** путь к legacy `.rates_summary_cache.json` для миграции в unified при первом обращении.

```bash
python3.9 rates.py --readonly --cache-file ./backup/.rates_summary_cache.json
```

### `--filter`

См. [раздел 5](#5-пресет---filter); пример сжатого вывода:

```bash
python3.9 rates.py --readonly --filter travelask
```

**Пример фрагмента** (строки с подстроками вроде «Bybit», «Korona» из пресета убраны, Forex остаётся):

```text
Карты UnionPay РСХБ
📈 2.458 | Forex
…
Перевод RUB ➔ THB
💱 2.577 | +4.8% | Avosend получение в Big C (от 50 000 RUB)
🤑 2.670 | +8.6% | askmoney (от 26 703 RUB)
```

### `--refresh`

Полная пересборка сводки с игнорированием кеша при чтении L1/L2 (много HTTP). Вывод по формату совпадает с обычной сводкой; отличие только в «свежести» чисел.

```bash
python3.9 rates.py --refresh
```

### `--gpt PROMPT`

```bash
python3.9 rates.py --gpt "2+2?"
```

При настроенных `OPENAI_API_KEY` и `OPENAI_API_URL`: ответ модели в stdout. При ошибке конфигурации: сообщение в stderr, ненулевой код (см. `openai_gpt.run_openai_gpt`). **Нельзя** добавлять подкоманду после `--gpt`.

---

## 4. Встроенные команды `rates.py`

### `sources`

```bash
python3.9 rates.py sources
```

Печатает по одному **`source_id`** на строку (порядок сводки + дополнительные плагины). Дополнительных опций нет.

```text
forex
rshb_unionpay
bybit_bitkub
…
htx_bitkub
```

### `env-status`

```bash
python3.9 rates.py env-status
```

Проверяет наличие `.env` и перечисляет ключи из `env_loader.ENV_STATUS_KEYS` (только **факт** «задано / нет», без значений).

**Пример вывода:**

```text
Файл /path/to/rates-api/.env: найден
При старте rates.py и bot уже вызван load_repo_dotenv …
Типичные ключи (только факт наличия в os.environ):
  BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY: задано
  TELEGRAM_BOT_TOKEN: задано
  …
```

### `save <файл>`

Синтаксис: `save <путь> [любые глобальные опции rates.py …]`.

Повторно парсятся из хвоста: `--refresh`, `--readonly`, `--json`, `--thb-ref`, `--atm-fee`, `--korona-small`, `--korona-large-thb`, `--avosend-rub`, `--unionpay-date`, `--moex-override`, `--cache-file`, `--filter`, … — они **перезаписывают** одноимённые поля уже распарсенного namespace (см. `rates.main` цикл `setattr`).

```bash
python3.9 rates.py save out.txt
python3.9 rates.py save out.json --json --refresh
python3.9 rates.py save ta.txt --filter travelask --readonly
```

**Результат:** файл `out.txt` с тем же текстом, что дала бы сводка в stdout; при `--json` — JSON сводки. Ошибка записи → stderr и код `1`.

### `usdt`

Опции **только после** слова `usdt` (локальный `ArgumentParser` в `rates.main`): `--refresh`, `--json`, `--cache-file`. Глобальный `rates.py --refresh` тоже включает refresh USDT, если нет `--readonly`.

| Опция | Описание |
|--------|-----------|
| `--refresh` | Пересобрать ветки `usdt:l1:*` и L2 USDT. |
| `--json` | Печать `print_usdt_report_json` (структура с `rub_per_usdt`, `thb_per_usdt`, `full_paths_rub_per_thb`, `warnings`). |
| `--cache-file` | Путь к legacy JSON USDT (миграция); дефолт `usdt_report.USDT_CACHE_FILE`. |

```bash
python3.9 rates.py usdt
python3.9 rates.py usdt --refresh
python3.9 rates.py usdt --json
python3.9 rates.py usdt --cache-file /path/to/usdt_cache.json
python3.9 rates.py --readonly usdt
```

**Пример `--json`** (фрагмент):

```json
{
  "rub_per_usdt": { "bybit_cash": 80.5, … },
  "thb_per_usdt": { "bitkub_highest_bid": 32.63, … },
  "full_paths_rub_per_thb": [ { "label": "…", "rub_per_thb": 2.45 }, … ],
  "warnings": []
}
```

**Пример текста:**

```text
Отчёт USDT: P2P RUB/USDT и USDT/THB.

RUB за 1 USDT (P2P, лучшая цена)
  80.10 | Bybit (перевод)
  …

THB за 1 USDT
  32.63 | Binance TH (bid)
  …
```

Справка: `python3.9 rates.py usdt --help` → текст из `usdt_report.usdt_subcommand_help()`.

### `rshb`

```bash
python3.9 rates.py rshb
python3.9 rates.py rshb 30000 250
python3.9 rates.py rshb 30000 20000 10000 250
python3.9 rates.py --readonly rshb
```

- Без аргументов: одна сумма **30000** THB, комиссия ATM **250** THB.  
- Два числа: `(THB,)`, `ATM_FEE`.  
- Три и больше: несколько нетто-снятий THB, **последнее** число — комиссия ATM.

### `cash`

Парсер: `cash_report._parse_cash_argv` (все флаги только **после** подкоманды `cash`).  
Источники данных: **РБК** (только Москва и Санкт-Петербург), **Banki.ru**, **Выберу.ру (VBR)**. Список из **8 городов** при вызове без номера всегда полный (нумерация 1…8).

| Параметр | Тип / дефолт | Описание |
|----------|----------------|----------|
| `city_n` | целое, опционально | Номер города из списка. |
| Позиционно после `city_n` | `banki` / `vbr` / `rbc` / `all` | Явный набор источников (см. ниже). Можно **до** флагов: `cash 1 banki`, `cash 1 vbr 10`. |
| `--top` | int, `10` | Сколько строк курсов по выбранному городу. |
| `--sources SPEC` | строка | То же, что слово-источник: `all`, `banki`, `vbr`, `rbc` или несколько через запятую (`rbc,banki`, `banki,vbr`, …). **Перекрывает** `--no-banki` и `--no-vbr`. |
| `--fiat USD` / `EUR` / `CNY` | строка | Только выбранная валюта в отчёте (остальные блоки USD/EUR/CNY не выводятся). **Только вместе с номером города** `N`; без `N` команда печатает список городов — тогда `--fiat` недопустим. |
| `--no-banki` | флаг | Убрать Banki из режима «все источники» (остаются РБК + VBR, если не отключены). |
| `--no-vbr` | флаг | Убрать VBR из режима «все источники». |
| `--refresh` | флаг | Проброс с глобального `rates.py --refresh`. |
| `--readonly` | флаг | Только кеш unified (в т.ч. истёкший TTL), без HTTP. |
| `-h`, `--help` | флаг | Текст `cash_subcommand_help()`. |

**Режимы `--sources` / позиционного слова:** `all` — РБК (где есть) + Banki + VBR; `banki` — только Banki; `vbr` — только VBR; `rbc` — только РБК (фактически только города с `city_id` РБК). Комбинации: `--sources rbc,vbr` и т.д.  
Глобальное отключение РБК: переменная **`RATES_DISABLE_RBC`**; отключение VBR: **`RATES_DISABLE_VBR`** (см. [§8](#8-кеши-и-переменные-окружения)).

Порядок аргументов: удобно **`cash N [источник] [топ]`** или **`cash N --top K --sources banki`**. Источник **после** `--top` в конце строки `argparse` может не распознать — используйте **`--sources`** или поставьте слово источника **перед** `--top`.

```bash
python3.9 rates.py cash
python3.9 rates.py cash 1
python3.9 rates.py cash 1 --top 10
python3.9 rates.py cash 1 banki
python3.9 rates.py cash 1 vbr 15
python3.9 rates.py cash 2 --sources rbc,banki
python3.9 rates.py cash 2 --no-banki
python3.9 rates.py cash 1 --fiat USD
python3.9 rates.py cash 1 --fiat eur --top 5
python3.9 rates.py cash --refresh
python3.9 rates.py --readonly cash 1
```

Отчёт «наличные ➔ THB» (TT Exchange) и кеш **`cash_thb:*`** остаются в `cash_report`; отдельная команда **`rates.py cash-thb` отключена** — при таком первом аргументе скрипт печатает подсказку использовать **`cash`**. Парсер `main_cash_thb_cli` в коде использует те же флаги, что и `cash`, на случай повторного подключения точки входа.

**Пример без номера города** (`rates.py cash`):

```text
Доступные города:
1. Москва
2. Санкт-Петербург
3. Казань
…
```

**Пример с городом** — многострочный отчёт по банкам/валютам (зависит от кеша); при `--top 1` — одна строка на валюту в рамках логики отчёта.

### `exchange`

Парсер: `exchange_report._parse_exchange_argv`.

| Параметр | Дефолт | Описание |
|----------|--------|----------|
| `--top` | `10` | Число филиалов TT в таблице. |
| `--lang` | `ru` | Язык подписей API филиалов. |
| `--timeout` | `28.0` | Таймаут HTTP на запрос (сек). |
| `--refresh` | — | Заново запросить TT API / обновить unified. |
| `--readonly` | — | Только кеш. |
| `--fiat USD` / `EUR` / `CNY` | — | Только эта валюта в таблице; сортировка по убыванию THB за 1 ед.; филиалы без курса по валюте не показываются. |
| `-h`, `--help` | — | Краткая справка подкоманды. |

```bash
python3.9 rates.py exchange
python3.9 rates.py exchange --top 5 --lang ru --timeout 28
python3.9 rates.py exchange --fiat EUR
python3.9 rates.py exchange --refresh
python3.9 rates.py --readonly exchange
```

**Пример начала вывода** (числа с площадки):

```text
Обмен наличные → THB (TT Exchange), THB за 1 ед. валюты

    USD       EUR       CNY  Филиал
  32.42     37.35      4.71  …
```

С `--fiat EUR` (или USD/CNY) — заголовок «только EUR», одна числовая колонка и сортировка по ней.

### `calc`

Парсер: `calc_report._parse_calc_argv`.

| Параметр | Дефолт | Описание |
|----------|--------|----------|
| `rub` | обязателен | Бюджет в рублях (строка, парсится в число). |
| `fiat` | обязателен | `usd` / `eur` / `cny`. |
| `fx` | обязателен | Ваш курс: **₽ за 1 ед.** покупаемой валюты для сценария TT. |
| `--atm-fee` | `250` | Комиссия банкомата THB (РСХБ). |
| `--lang` | `ru` | Язык списка филиалов TT. |
| `--timeout` | `28.0` | Таймаут HTTP. |
| `--refresh` | — | Обновить L1 TT при расчёте. |
| `--readonly` | — | Только кеш UnionPay/РСХБ и L1 TT. |
| `--unionpay-date` | — | `YYYY-MM-DD`. |
| `--moex-override` | — | Ручной MOEX. |
| `-h`, `--help` | — | `calc_subcommand_help()`. |

```bash
python3.9 rates.py calc 100000 usd 83
python3.9 rates.py calc 100000 eur 92 --atm-fee 300 --unionpay-date 2026-04-01
python3.9 rates.py calc 50000 cny 11.5 --refresh
python3.9 rates.py --readonly calc 100000 usd 83
```

**Пример фрагмента вывода** (таблица каналов):

```text
Сравнение RUB→THB …

#  Курс    THB    Δ THB  Δ RUB  Канал
1  2.xxx  …      …      …      …
…
```

### `--gpt PROMPT`

```bash
python3.9 rates.py --gpt "Кратко опиши курс THB/RUB"
```

Требуется `OPENAI_API_KEY`, `OPENAI_API_URL`; опционально `OPENAI_PROMPT`, `OPENAI_MODEL`, `OPENAI_HTTP_TIMEOUT_SEC`, `OPENAI_GPT_USER`. Реализация: `openai_gpt.py` (`run_openai_gpt`).

### Один источник: `summary`

```bash
python3.9 rates.py forex summary
python3.9 rates.py ex24 summary --refresh
```

С `--readonly` **запрещено** (сообщение в stderr). Иначе — сеть / логика конкретного плагина + общие параметры сводки (`--thb-ref`, …).

---

## 5. Пресет `--filter`

Модуль: `rates_output_filters.py`.

Имена пресетов (регистр для поиска строки — через `.lower()` у имени фильтра):

| Имя | Заметки |
|-----|---------|
| `travelask` | Убирает строки, где в `label`/`note` встречаются подстроки из пресета (Forex не трогается). |
| `ta` | Алиас `travelask`. |
| `-1001835014897` | Исторический алиас для чата. |

Неизвестное имя — **без изменений**, без ошибки.

```bash
python3.9 rates.py --filter travelask
```

---

## 6. Один источник: `rates.py <source_id>`

Любой `source_id` из `rates.py sources`, кроме зарезервированных слов (`save`, `usdt`, …).

```bash
python3.9 rates.py <source_id> --help
python3.9 rates.py <source_id> [подкоманды и опции модуля]
```

Передаётся в `plugin.command(argv)` соответствующего пакета `sources/<name>/`.

Сводка по плагинам и куда смотреть за полным CLI:

| `source_id` | CLI / заметки |
|-------------|----------------|
| `forex` | `xe` (Xe.com), `er` (ExchangeRate-API). По умолчанию без подкоманды — как `xe`. `rates.py forex --help` печатает оба парсера. |
| `rshb_unionpay` | Подкоманды: `cardfx`, `unionpay`, `moex`, `rshb-offline`, `rshb-online`, `reports`. |
| `bybit_bitkub` | `bitkub …` или аргументы **Bybit P2P** (`sources/bybit_bitkub/bybit_p2p_usdt_rub.py`). |
| `bybit_novawallet`, `bybit_binanceth` | Аналогично веткам Bybit / Bitkub / Binance в соответствующих модулях. |
| `htx_bitkub`, `htx_binanceth` | HTX OTC CLI: `sources/htx_bitkub/htx_p2p_usdt_rub.py`. |
| `korona` | `sources/korona/koronapay_tariffs.py` — подкоманды `compare-100k`, `query`. |
| `avosend` | `sources/avosend/avosend_commission.py`. |
| `avosend_bkb` | См. help модуля; нужен `BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY`. |
| `ex24` | `sources/ex24/ex24_rub_thb.py`. |
| `kwikpay` | `sources/kwikpay/kwikpay_rates.py`. |
| `askmoney` | `sources/askmoney/askmoney_rub_thb.py`. |
| `ttexchange` | Клиент API: логика `sources/ttexchange/ttexchange_api.py` (подкоманды `stores`, `rates`, …). |
| `rbc_ttexchange` | Отдельного богатого CLI нет — данные для сводки. |
| `tbank` | См. `sources/tbank/__init__.py`. |
| `unired_bkb` | Справка без ключа BKB; с ключом — данные API. |
| `userbot_cash` | См. `sources/userbot_cash/__init__.py`. |

---

## 7. Справочник CLI плагинов (все флаги и примеры)

Все команды ниже эквивалентны **`python3.9 rates.py <id> …`** из корня репозитория (кроме прямого пути к `sources/.../ttexchange_api.py`).

### `ex24` (`rates.py ex24 …`)

**Справка (`rates.py ex24 --help`):**

```text
usage: … [--real-rate REAL_RATE] [--fetch-rate] [--from-type FROM_TYPE] [--table] [--round-rate N] [amount_rub]
```

| Опция / аргумент | Назначение |
|------------------|------------|
| `amount_rub` | Сумма в RUB (опционально). |
| `--real-rate` | Базовый realRate без наценки (иначе дефолт из кода или с сайта). |
| `--fetch-rate` | Скачать главную и взять курс из `payload.rates` (RUB→THB). |
| `--from-type` | Значение `fromType` у записи RUB→THB; пусто — любая подходящая. |
| `--table` | Только таблица наценок и курсов (без сети к калькулятору для суммы). |
| `--round-rate N` | Округление отображаемого курса до N знаков. |

**Пример `--table`:**

```bash
python3.9 rates.py ex24 --table
```

```text
realRate = 2.7014

Диапазон RUB     markup   курс (RUB/THB)
     500      10.0%    2.97154
    1000      10.0%    2.97154
…
   14950       0.0%    2.7014
```

### `askmoney` (`rates.py askmoney …`)

| Опция | Назначение |
|--------|------------|
| `rub` | Сумма в RUB. |
| `--fetch` | Скачать https://askmoney.pro/ и взять параметры `b2,f2,h2,b4`. |
| `--html-file` | Путь к сохранённому HTML вместо сети. |
| `--show-formula` | Печать формулы из HTML. |
| `--json-params` | Вывести параметры JSON. |
| `--max-rate` | Наихудший курс (max RUB/THB в модели ступеней). |
| `--max-rate-float` | То же с дробными рублями на верхней ступени. |
| `--min-rate` | Самый выгодный курс (min RUB/THB) до `--rub-cap`. |
| `--rub-cap` | Верхняя граница поиска для `--min-rate` (по умолчанию 50 млн RUB). |

Пример вывода числового режима зависит от параметров; для `--json-params` — одна-две строки JSON в stdout.

### `bybit_bitkub` / `bybit_binanceth` / `bybit_novawallet` (ветка Bybit P2P)

Через `rates.py bybit_bitkub [опции]` (без подкоманды `bitkub`) аргументы идут в `bybit_p2p_usdt_rub.build_arg_parser()`:

| Опция | По умолчанию | Назначение |
|--------|--------------|------------|
| `--min-completion` | `99.0` | Минимум `recentExecuteRate`, %. |
| `--target-usdt` | см. `DEFAULT_TARGET_USDT` в модуле | `lastQuantity` и `minAmount` ≥ объёму. |
| `--min-usdt` | — | Синоним `--target-usdt` (переопределяет). |
| `--size` | `20` | Размер страницы item/online. |
| `--verification-filter` | `0` | Поле в теле запроса API. |
| `--payments-only` | — | Только справочник способов оплаты (JSON). |
| `--json` | — | Итог в JSON (cash / bank transfer ветки). |
| `--max-pages` | — | Лимит страниц при обходе. |
| `--full-scan` | — | Все страницы + счётчики matched (медленнее). |

**Пример текстового режима (фрагмент):**

```text
Ранний обход страниц item/online … lastQuantity≥100, minAmount≥100·price, completion≥99 %

=== Cash Deposit to Bank (18) ===
  Ранний обход: без полного счёта объявлений (см. --full-scan)
  Минимальная цена: 80.5 RUB за 1 USDT
  …
```

### `htx_bitkub` / `htx_binanceth` (ветка HTX OTC)

| Опция | По умолчанию | Назначение |
|--------|--------------|------------|
| `--json` | — | JSON с лучшими cash / non_cash. |
| `--max-pages` | `30` | Максимум страниц пагинации. |
| `--target-usdt` | из модуля | `tradeCount` и `minTradeLimit`. |
| `--min-usdt` | — | Синоним `--target-usdt`. |
| `--full-scan` | — | Полный обход до `--max-pages` + счётчики. |

**Пример без `--json` (фрагмент):**

```text
Ранний обход (API по возрастанию цены), до 30 стр.: tradeCount ≥ 100, minTradeLimit ≥ 100·price
  мин. цена (наличные):     82.1 RUB/USDT  id=…
  мин. цена (перевод): 80.9 RUB/USDT  id=…
```

### `korona` (`rates.py korona compare-100k|query …`)

**`compare-100k`:** `--payment` (дефолт `debitCard`), `--receiving` (дефолт `accountViaDeeMoney`).

**Пример вывода `compare-100k`:**

```text
Сравнение порога 100 000 RUB (отправка, копейки в API):

  До порога:   … RUB → rate=…, THB=…
  На пороге:  … RUB → rate=…, THB=…
```

**`query`:** обязательно одно из `--sending-rub` или `--receiving-thb`; те же `--payment` / `--receiving`; `--raw` — полный JSON первого тарифа.

### `avosend` (`rates.py avosend [--json] [--raw] {cash,bank,card} amount`)

| Позиция | Значения |
|---------|-----------|
| Канал | `cash` (наличные THB), `bank` (счёт THB), `card` (карта USD). |
| `amount` | Сумма отправки в RUB. |

С `--json` — полный JSON ответа; с `--raw` — сырой текст ответа сервера.

### `kwikpay` (`rates.py kwikpay …`)

| Опция | Назначение |
|--------|------------|
| `--amounts` | Список `amount` через запятую (см. docstring модуля). |
| `--country` | По умолчанию `THA`. |
| `--currency` | По умолчанию `THB`. |
| `--json` | Вывод JSON. |

### `ttexchange` (`rates.py ttexchange [--lang LANG] <подкоманда> …`)

Глобально: `--lang` (код языка API).

| Подкоманда | Доп. опции | Вывод |
|------------|------------|--------|
| `stores` | `--group`, `--hq` | JSON |
| `store-groups` | — | JSON |
| `rates` | `--branch` (иначе авто из stores) | таблица курсов |
| `banners`, `promotions`, `news`, `abouts`, `about-facts`, `landing-pages`, `place-types`, `places`, `faqs`, `safe-box-branches`, `testimonials` | — | JSON |

Прямой запуск того же CLI: `python3.9 sources/ttexchange/ttexchange_api.py --lang ru stores`.

### `forex xe` (`rates.py forex xe …`)

- **`midmarket`** `from_ccy to_ccy [amount]` — опционально `--raw` (полный JSON ответа).  
  **Пример:** `rates.py forex xe midmarket THB RUB 1`

```json
{
  "from": "THB",
  "to": "RUB",
  "amount": 1.0,
  "result": 2.458716773712278,
  "timestamp": 1775157660000,
  "rate_from_per_usd": 32.6271570483,
  "rate_to_per_usd": 80.2209383132
}
```

- **`convert`** `from_ccy to_ccy [amount]` — платный API; `--interval` (`daily` | `hourly` | …).

### `forex er` (`rates.py forex er …`)

**Справка верхнего уровня:** `rates.py forex er --help` — подкоманды `latest`, `convert`, `rate`, `matrix`.

**`latest`:** опция `--base` (база API, по умолчанию USD). Вывод — сырой JSON.

**`convert`:** позиционные `amount from_ccy to_ccy`, опция `--base`.

**`rate`:** `from_ccy to_ccy`, `--base`.

**`matrix`:** только `--base`.

| Подкоманда | Аргументы | Пример вывода |
|------------|-----------|----------------|
| `latest` | `--base` (дефолт USD) | JSON |
| `convert` | `amount from_ccy to_ccy` + `--base` | `100 USD = … THB` |
| `rate` | `from_ccy to_ccy` + `--base` | `1 THB = 2.47134646 RUB` |
| `matrix` | `--base` | таблица 3×3 RUB/THB/USD |

**Пример `matrix` (фрагмент):**

```text
                 RUB         THB         USD
RUB         1.000000    0.404638    0.012434
THB         2.471346    1.000000    0.030729
…
Смысл: умножьте сумму в строке FROM на число в колонке TO.
```

### `rshb_unionpay cardfx` (`rates.py rshb_unionpay cardfx …`)

| Опция | По умолчанию |
|--------|----------------|
| `--date` | UnionPay + РСХБ на дату |
| `--thb` | `30000` |
| `--atm-fee` | `250` |
| `--moex-override` | ручной MOEX |
| `--example1` | режим «как пример 1» (оплата + снятие, эмодзи, %%) |

### `rshb_unionpay unionpay`

`--date`, `--trans`, `--base` — см. `unionpay_rates.py`.

### `rshb_unionpay moex`

Без подкоманд: одна строка CNY/RUB TOM (см. вывод при запуске).

### `rshb_unionpay reports`

| Опция | Назначение |
|--------|------------|
| `--sections` | Номера через запятую, напр. `1,3,5` |
| `--all` | Разделы 1–5 подряд |
| `--date`, `--moex-override`, `--thb`, `--atm-fee` | контекст отчётов |
| позиционные `N` | Номера разделов 1–5 |

### `bitkub` / `binance_th` (подкоманды у `bybit_bitkub`, `htx_*`, `bybit_binanceth`)

- `rates.py bybit_bitkub bitkub --help` → опция `--json` и др. из `bitkub_usdt_thb.py`.  
- `… binance_th --help` → `--json` в `usdt_thb_book.py`.

### `tbank`, `unired_bkb`, `userbot_cash`

Без пользовательских флагов: `rates.py tbank` печатает `help_text()` (URL API). `unired_bkb` / `userbot_cash` — текст справки из плагина.

### `avosend_bkb`

Нет отдельного argparse; нужны переменные окружения (см. `help_text` плагина).

---

## 8. Кеши и переменные окружения

### Файлы по умолчанию

| Назначение | Переменная | Файл по умолчанию |
|------------|------------|-------------------|
| Legacy сводка | `RATES_CACHE_FILE` | `.rates_summary_cache.json` |
| Единый кеш L1/L2/prim | `RATES_UNIFIED_CACHE_FILE` | `.rates_unified_cache.json` |
| USDT legacy + unified ветки | `RATES_USDT_CACHE_FILE` | `.rates_usdt_cache.json` |

Основные переменные TTL (см. `rates_unified_cache.py`, значения по умолчанию; переопределение через `os.environ`):

| Переменная | Дефолт (сек) | Назначение |
|------------|----------------|------------|
| `RATES_UNIFIED_TTL_RS` | 1800 | L1 источники сводки `rs:*` (кроме bybit). |
| `RATES_UNIFIED_TTL_RS_BYBIT` | 60 | L1 для id, начинающихся с `bybit`. |
| `RATES_UNIFIED_TTL_USDT` | 60 | Ветки `usdt:l1:*`. |
| `RATES_UNIFIED_TTL_L2_SUMMARY` | 1800 | L2 текст сводки. |
| `RATES_UNIFIED_TTL_L2_USDT` | 60 | L2 USDT. |
| `RATES_UNIFIED_TTL_EX_TT_STORES` | 1800 | `ex:l1:stores:*` |
| `RATES_UNIFIED_TTL_EX_TT_CUR` | 1800 | `ex:l1:cur:*` |
| `RATES_UNIFIED_TTL_L2_EXCHANGE` | 1800 | L2 exchange |
| … | … | cash / cash_thb / ex24 / примитивы — см. файл модуля |

`usdt_report.USDT_CACHE_TTL_SEC` = 60 для legacy-файла; фактическое L2 unified для USDT задаётся `TTL_L2_USDT_SEC` в `rates_unified_cache`.

### Проверка ключей (без секретов)

Список ключей для `rates.py env-status` — `env_loader.ENV_STATUS_KEYS` (Bangkok Bank, Telegram, Avosend, …).

### Наличные (`cash` / unified)

| Переменная | Назначение |
|------------|------------|
| `RATES_DISABLE_RBC` | Значения `1` / `true` / `yes` / `on` — не запрашивать РБК в отчёте наличных. |
| `RATES_DISABLE_VBR` | То же — не запрашивать Выберу.ру (VBR). |

### OpenAI (`--gpt`)

- `OPENAI_API_KEY`, `OPENAI_API_URL` (обязательны для запроса)  
- `OPENAI_PROMPT` — системный промпт  
- `OPENAI_MODEL` (по умолчанию `gpt-4o-mini`)  
- `OPENAI_HTTP_TIMEOUT_SEC` (минимум 30, иначе дефолт 300)  
- `OPENAI_GPT_USER` — поле `user` в API (обрезка до 64 символов)

---


## 9. Параллелизм

`RATES_PARALLEL_MAX_WORKERS` — размер пула потоков для сводки, cash, usdt, exchange и т.д.  
По умолчанию **12** (`rates_parallel.default_max_workers()`).

**Пример:** `export RATES_PARALLEL_MAX_WORKERS=8` — затем любой тяжёлый запуск (`rates.py --refresh`, `rates.py exchange`, …) использует до 8 потоков.

---

## 10. `bot/main.py` — Telegram-бот

Запуск (из корня, после настройки `.env`):

```bash
python3.9 bot/main.py
```

Нужны как минимум `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN` (см. заголовок `bot/main.py`). Сессия: `bot/rates_bot.session`.

### Команды (из `/start`)

| Команда | Описание |
|---------|-----------|
| `/rates` | Сводка; можно `filter ta` / `ta` — пресет как в CLI (`parse_rates_command_tokens`). |
| `/usdt` | Отчёт USDT, кеш. |
| `/cash` | Без аргументов — список городов (8 шт.). Далее: `/cash N` и при необходимости одно из слов `banki`, `vbr`, `rbc`, `all`, затем при необходимости топ `K` (до 50). Фильтр по одной валюте: в CLI `rates.py cash N --fiat USD` (или EUR/CNY); в chat-agent — аргумент `cash_fiat`. Примеры: `/cash 1 banki`, `/cash 2 vbr`, `/cash 1 all 10`. |
| `/exchange` | Топ TT; опционально `/exchange 5` (число филиалов, до 50). Одна валюта: CLI `rates.py exchange --fiat USD`; в chat-agent — `exchange_fiat`. |
| `/rshb` | Как CLI: суммы THB и комиссия ATM. |
| `/calc` | `RUB usd|eur|cny КУРС` (см. `bot/calc_args.parse_calc_command_args`). |

### Админ

- `BOT_ADMIN_ID` — числовой id для `/refresh`.  
- `/refresh` — обновить сводку с `refresh=True`.  
- `/refresh usdt`, `/refresh cash` — точечное обновление.  
- GPT: список пользователей в файле (см. код), команды добавления/удаления для админа.

Таймаут запросов: `BOT_FETCH_TIMEOUT_SEC`.

**Пример ответа бота на `/rates`** (по смыслу совпадает с `rates.py` без фильтра): тот же текст сводки, что в CLI.  
**`/rates ta`** или **`/rates filter ta`** — применяется пресет `travelask` (как `--filter ta`).

---

## 11. `userbot/main.py`

Запуск из **корня репозитория** (чтобы импортировались `rates_unified_cache`, `userbot.*`). Нужен пакет **`telethon`**.

| Опция | Описание |
|--------|-----------|
| `--login` | Только интерактивная авторизация и запись файла сессии, затем выход. |
| `--phone` | Номер для `--login`, напр. `+79990001122`. |

```bash
cd /path/to/rates-api
python3.9 userbot/main.py --login --phone +79990001122
python3.9 userbot/main.py
```

При отсутствии Telethon: `ModuleNotFoundError: No module named 'telethon'` — установите зависимость в окружение.  
Настройки чатов и лимитов: `userbot/config.py`, `userbot/sources_config.py`.

**Пример лога при старте** (фрагмент): `userbot logged in`, затем строки `bootstrap: <source_id> matched …`.

---

## 12. `scripts/bkb_probe_latestfxrates.py`

Диагностика Bangkok Bank `GetLatestfxrates`.

**Справка:**

```text
usage: bkb_probe_latestfxrates.py [-h] [--referer] [--timeout TIMEOUT]
```

| Опция | Дефолт | Описание |
|--------|--------|----------|
| `--referer` | выкл | Добавить `Referer: https://www.bangkokbank.com/` |
| `--timeout` | `60` | Таймаут чтения HTTP (сек) |

```bash
export BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY="…"
python3.9 scripts/bkb_probe_latestfxrates.py
python3.9 scripts/bkb_probe_latestfxrates.py --referer
python3.9 scripts/bkb_probe_latestfxrates.py --timeout 90
```

**Пример успешного вывода** (фрагмент): строка с распарсенным `USD50` / `TT` или JSON stderr при ошибке API.

---

## 13. Модули только для импорта

Следующие файлы **не** предполагают полноценного пользовательского CLI в рамках этой инструкции (или служат библиотеками):  
`rates_sources.py`, `rates_primitives.py`, `rates_unified_cache.py`, `rates_http.py`, `rates_output_filters.py`, `cash_report.py` / `exchange_report.py` / `calc_report.py` / `usdt_report.py` как **модули** (CLI вызывается через `rates.py`), `bot/summary_adapter.py`, `env_loader.py`, большинство `sources/*` без `__main__`.

Исключение: интерактив/одноразовые скрипты в `tests/` — см. раздел 14.

---

## 14. Тесты

Каталог `tests/` — `unittest` / `pytest`. Примеры:

```bash
python3.9 -m unittest discover -s tests -q
```

Отдельные модули: `tests/test_rates_summary_cli.py`, `test_usdt_report.py`, `test_exchange_report.py`, и т.д.

---

## 15. Встроенный текст `rates.py --help`

Ниже — полный вывод `python3.9 rates.py --help` на момент сборки документа.

```text
usage: rates.py [-h] [--refresh | --readonly] [--json] [--thb-ref THB_REF]
                [--atm-fee ATM_FEE] [--korona-small KORONA_SMALL]
                [--korona-large-thb KORONA_LARGE_THB]
                [--avosend-rub AVOSEND_RUB] [--unionpay-date UNIONPAY_DATE]
                [--moex-override MOEX_OVERRIDE] [--cache-file CACHE_FILE]
                [--filter NAME] [--gpt PROMPT]

Сводка RUB/THB из скриптов проекта (кеш 30 мин)

optional arguments:
  -h, --help            show this help message and exit
  --refresh             Игнорировать кеш
  --readonly            Без сетевых запросов: только данные из unified- и
                        файловых кешей (в т.ч. L2 с истёкшим TTL).
                        Несовместимо с --refresh.
  --json                JSON в stdout
  --thb-ref THB_REF     Нетто THB для сценариев снятия
  --atm-fee ATM_FEE     Комиссия банкомата, THB
  --korona-small KORONA_SMALL
  --korona-large-thb KORONA_LARGE_THB
                        Сумма получения THB для строки Korona (крупная)
  --avosend-rub AVOSEND_RUB
  --unionpay-date UNIONPAY_DATE
                        YYYY-MM-DD для JSON UnionPay
  --moex-override MOEX_OVERRIDE
  --cache-file CACHE_FILE
                        Файл кеша
  --filter NAME         Пресет постфильтрации вывода (например travelask).
                        Неизвестное имя — без эффекта.
  --gpt PROMPT          Запрос к OpenAI Chat: OPENAI_API_KEY, OPENAI_API_URL;
                        опц. OPENAI_PROMPT, OPENAI_MODEL,
                        OPENAI_HTTP_TIMEOUT_SEC.

Команды:
  (сводка) Опции: --refresh | --readonly, --json, --filter NAME — пресет постфильтрации строк (неизвестное имя игнорируется). --readonly — без HTTP, только кеш.
  --gpt PROMPT     Chat API: OPENAI_API_KEY, OPENAI_API_URL; OPENAI_PROMPT; OPENAI_MODEL; OPENAI_HTTP_TIMEOUT_SEC.
  sources              Список id доступных источников.
  env-status           Файл .env и типичные переменные (без значений).
  save <файл>          Записать текстовую сводку в файл (те же опции, что и для сводки).
  usdt [--refresh] [--json] [--cache-file ПУТЬ]  Отчёт P2P RUB/USDT и USDT/THB (отдельный кеш).
  rshb [THB …] [ATM_FEE]  Отчёт THB/RUB РСХБ UnionPay; 3+ числа — несколько снятий, последнее — комиссия ATM.
  cash [N] [banki|vbr|rbc|all] [K] [--top K] [--sources SPEC] [--fiat USD|EUR|CNY] [--no-banki] [--no-vbr] [--refresh]  Без N — список городов; с N — курсы города (K или --top — число строк). --fiat — только одна валюта в выводе.
  exchange [--top N] [--lang ru] [--fiat USD|EUR|CNY]   Топ филиалов TT (USD/EUR/CNY→THB); --fiat — одна колонка.
  calc RUB usd|eur|cny КУРС [--atm-fee THB]  Сравнение RUB→THB; КУРС — ₽ за 1 ед. валюты (TT).
  <source_id> summary [--refresh]  Только этот источник (те же --korona-*, --avosend-rub, …).
  <source_id> --refresh          То же, если других аргументов у id нет.
  <source_id> [args]   Иные подкоманды источника (см. python ... <id> --help).

Параллельные запросы: переменная RATES_PARALLEL_MAX_WORKERS (сводка источников, cash, usdt, exchange; по умолчанию см. rates_parallel).

Источники (кратко; полное: <id> --help):
  forex
      Курс THB→RUB для сводки — XE midmarket. CLI: подкоманда «xe» — клиент Xe.com; «er» — ExchangeRate-API (open.er-api.com). Полные опции: forex xe --help   и   forex er --help
  rshb_unionpay
      РСХБ / UnionPay / MOEX: сводка через card_fx_calculator. Подкоманды CLI (полные опции: rshb_unionpay <подкоманда> --help):   cardfx       Калькулятор THB/RUB/CNY (бывший card_fx_calculator.py)   unionpay     UnionPay daily JSON (unionpay_rates.py)   moex         CNY/RUB с MOEX одной строкой (moex_fx.py)   rshb-offline РСХБ offline HTML (rshb_offline_rates.py)   rshb-online  РСХБ online HTML (rshb_online_rates.py)   reports      Отчёты разделы 1–5 (fx_reports.py)
  bybit_bitkub
      Bybit P2P USDT/RUB + Bitkub THB/USDT.   bitkub …  — тикер Bitkub (подкоманда, см. bybit_bitkub bitkub --help)   иначе     — аргументы передаются в Bybit P2P CLI (bybit_p2p_usdt_rub)
  bybit_novawallet
      Bybit P2P (мин. цена среди cash deposit 18 и bank transfer 14 без 18) + курс NovaWallet THB/USDT (api.novawallet.org). Используется в общей сводке ``rates.py`` / ``summary``.
  korona
      KoronaPay API — тарифы RUB→THB. Полный список подкоманд и опций: korona --help
  avosend
      Avosend API (comission.php). Полные опции: avosend --help
  avosend_bkb
      Avosend (карта USD) × Bangkok Bank GetLatestfxrates USD50 TT → RUB/THB.   Нужны BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY; для Avosend опционально AVOSEND_COOKIE.   Курс: usd = (avosend_rub - fee) * convertRate, затем THB как у unired_bkb (TT USD50).
  ex24
      ex24.pro RUB→THB. Полные опции: ex24 --help
  kwikpay
      KwikPay котировки. Полные опции: kwikpay --help
  askmoney
      askmoney.pro калькулятор. Полные опции: askmoney --help
  ttexchange
      TT Exchange: наличные RUB/THB по API филиала; CLI — полный клиент ttexchange_api (stores, rates, …). См. ttexchange --help.
  rbc_ttexchange
      РБК/Banki cash (Москва, СПб) min sell × TT Exchange THB/USD|EUR|CNY → implied RUB/THB. См. rbc_ttexchange (без отдельного CLI).
  tbank
      Т-Банк: наличные RUB→THB, категория ATMCashoutRateGroup, поле buy (https://www.tbank.ru/api/common/v1/currency_rates?from=RUB&to=THB).
  unired_bkb
      Unired (из userbot cache) VISA USD/RUB + Bangkok Bank GetLatestfxrates USD50 TT → RUB/THB.   Нужен env BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY.   Иначе: только справка (без подкоманд).
  userbot_cash
      Котировки из Telegram-каналов (userbot → unified cache). Категория задается в конфиге userbot.
  bybit_binanceth
      Bybit P2P USDT/RUB + Binance TH spot USDT/THB (bid).   binance_th …  — bookTicker (см. bybit_binanceth binance_th --help)   иначе         — Bybit P2P CLI (bybit_p2p_usdt_rub)
  htx_binanceth
      HTX P2P USDT/RUB + Binance TH spot USDT/THB (bid).   binance_th …  — bookTicker (см. htx_binanceth binance_th --help)   иначе         — HTX OTC CLI (htx_p2p_usdt_rub)
  htx_bitkub
      HTX P2P USDT/RUB + Bitkub THB/USDT.   bitkub …  — тикер Bitkub (подкоманда, см. htx_bitkub bitkub --help)   иначе     — HTX OTC trade-market (htx_p2p_usdt_rub)
```

## Папка `cron/`

Скрипты расписания в **`cron/`** в этот документ **не входят** (по запросу: описаны только скрипты **кроме** `cron/`). При необходимости см. docstring’и в `cron/*.py`.

---

*Документ сгенерирован по состоянию кода репозитория; при расхождении с кодом приоритет у исходников и `rates.py --help`.*
