# USAGE-AGENT (read-only)

Агент с **rates-api** использует **только readonly-данные**: флаг **`--readonly`** — чтение уже лежащего в репозитории unified/legacy кеша, **без сетевых запросов**. Остальные режимы `rates.py` агенту не предназначены.

Корень репо: `python3.9 rates.py`.

| Задача | Команда |
|--------|---------|
| Сводка RUB/THB | `rates.py --readonly` [ `--json` \| `--filter` travelask \| `--filter` ta ] |
| USDT | `rates.py --readonly usdt` [ `--json` ] |
| РСХБ / UnionPay | `rates.py --readonly rshb` — параметры сценария при необходимости: `rates.py rshb -h` |
| Наличные | `rates.py --readonly cash` [ N ] [ banki\|vbr\|rbc\|all ] [ `--top` K \| `--sources` SPEC \| `--no-banki` \| `--no-vbr` ] |
| TT Exchange | `rates.py --readonly exchange` [ `--top` N ] |
| Сравнение каналов | `rates.py --readonly calc` RUB usd\|eur\|cny КУРС_₽_за_1_ед |

`cash`: не указывать источник **после** `--top` — задать источник **до** `--top` или через `--sources`.

Пустая сводка в кеше → stderr, код выхода 1.

Справка: `rates.py -h`, `rates.py sources`, `rates.py <source_id> -h`, `USAGE.md`.
