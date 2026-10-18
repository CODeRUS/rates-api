# AGENTS.md

Guidance for coding agents working in this repository. Human-facing command reference is `USAGE.md`. Deployment and environment variables are in `README.md`. This file describes how the system is put together and how to change it without breaking the shared pipeline.

## What this repository is

`rates-api` aggregates foreign-exchange quotes, mainly RUB per 1 THB, plus cash, card, and USDT reports. The name is historical. There is no public rates HTTP API.

Surfaces:

- `rates.py` is the CLI and the single implementation of the reports.
- `bot/` is a Telethon bot that renders those reports in Telegram.
- `userbot/` is a separate Telethon user client that reads Telegram channels into the shared cache.
- `chat_agent/` is an internal FastAPI service. The bot calls it for free-text questions. It plans a tool call and runs `rates.py`.
- `cron/` and `browser/` support the pipeline. They are not user-facing.

Docker images use Python 3.12. On this host, tests are run with `python3.9`. Prefer the interpreter that matches the environment you are in.

## Process map

```
Telegram user
    |  /command                         |  free text in private chat
    v                                   v
bot (Telethon)                          bot -> chat-agent (FastAPI)
    |                                   planner LLM -> whitelist tool
    v                                   v
summary_adapter / report modules        tools.py shells out to rates.py
    \_________________________________/
                     |
                     v
              rates.py and report modules
                     |
         +-----------+-----------+
         v           v           v
   source plugins  primitives   userbot L1 rows
         \           |           /
          v          v          v
              unified JSON cache
              prim / l1 / l2
```

Compose services (`docker-compose.yml`), all relevant ones on the host network:

| Service | Role |
|---|---|
| `rates-bot` | `python -m bot.main`. Repo bind-mounted at `/app`. |
| `userbot` | `python -m userbot.main`. Shares `.rates_cache` with the bot. |
| `chat-agent` | FastAPI. Depends on Redis and Postgres. Repo bind-mounted at `/app`. |
| `redis` | Chat-agent session store. `127.0.0.1:6379`. |
| `postgres` | Optional chat-agent audit. `127.0.0.1:5432`. |
| `rates-api` | One-shot CLI image, not the long-running bot. |

`rates-bot`, `userbot`, and `chat-agent` read the same unified cache file. A code change on the bind mount is visible immediately, but the bot and userbot are long-running processes: restart the container after changing imported modules. `docker restart rates-api-rates-bot-1` is enough for the bot. Do not rebuild the image for a Python-only change.

Secrets live in `.env` at the repo root. `env_loader.load_repo_dotenv` fills `os.environ` with `setdefault`, so a variable already set in the shell wins. Never commit `.env`, Telethon sessions, or cache files.

## Unified cache

`rates_unified_cache.py` owns one JSON document (default `.rates_unified_cache.json`, override `RATES_UNIFIED_CACHE_FILE`).

| Section | Contents |
|---|---|
| `prim` | Shared raw inputs fetched once and reused by several calculations. |
| `l1` | Per-source or per-cell payloads (`rs:*`, `chatcash:*`, cash cells, exchange snapshots). |
| `l2` | Rendered report payloads keyed by a digest of the request parameters. |
| `version_counter` | Bumped on write. L2 entries record the L1/prim versions they depend on. |

Writes are atomic (temp file plus replace) under a process lock. TTL is per entry, with env overrides such as `RATES_UNIFIED_TTL_RS` and `RATES_UNIFIED_TTL_L2_SUMMARY`. Bybit-like primitives use a shorter TTL than the general source TTL.

L2 is reused only when its dependency versions still match the current document. A cron refresh of a primitive invalidates a summary that depended on it. `--readonly` may return a stale snapshot and must not hit the network. `--refresh` bypasses L2 and refetches.

Do not delete or hand-edit the production cache to "fix" a report. It is live data shared by the bot, the userbot, and cron.

## Quote pipeline

`rates_sources.py` is the summary engine. `rates_categories.py` holds the category enum so plugins can import it without a cycle.

Categories:

- `TRANSFER` — remittances and calculated RUB/THB routes.
- `EXCHANGER` — exchangers.
- `CASH_RUB`, `CASH_USD`, `CASH_EUR`, `CASH_CNY` — cash blocks. USD/EUR/CNY rows are THB per 1 unit of fiat, not RUB per THB, and are not compared to the baseline.

A plugin is a module under `sources/` registered from `sources/__init__.py`.

- `PLUGIN_ORDER` is the summary order and the default set passed to `load_default_sources`.
- `registered_source_ids()` is that order plus any other imported plugin, sorted, for `rates.py <source_id>`.
- Each plugin module exposes `SOURCE_ID`, `EMOJI`, `IS_BASELINE`, `CATEGORY`, `summary(ctx) -> list[SourceQuote] | None`, `command(argv)`, and `help_text()`.
- `RateSource` is the runtime wrapper. `FetchContext` carries the request (reference amount, fees, optional `unified_doc`, `warnings`). `SourceQuote` is one numeric row. A quote may override the plugin's category, emoji, note, and whether it is compared to the baseline.

`summary()` must not raise for a normal upstream failure. Append a warning to `ctx.warnings` and return `None` or a partial list. The engine isolates exceptions per plugin and continues.

Run order for a summary:

1. Load the unified document.
2. `rates_primitives.ensure` fetches the shared `prim:*` keys required by the selected plugins. Plugins then read those primitives instead of repeating the same HTTP call.
3. `run_sources` executes plugin `summary` functions on a bounded thread pool (`rates_parallel.py`, `RATES_PARALLEL_MAX_WORKERS`).
4. Rows are deduplicated. For transfer and cash-RUB, the lower RUB-per-THB wins. For cash USD/EUR/CNY, the higher THB-per-unit wins.
5. Rows are grouped: baseline first, then other transfer rows by rate, then exchangers, then cash subcategories in `CASH_CATEGORIES_ORDER`.
6. The text renderer in `rates.py` prints percent versus the baseline where `compare_to_baseline` is set. The first registered baseline plugin must stay first and must be the Forex baseline. `run_sources` rejects a list that does not start with it.
7. The resulting rows, baseline, and warnings are stored as an L2 summary whose key includes the amount and fee parameters.

`rates_output_filters.py` can hide blocks after the rows exist (`--filter` / bot filter tokens). Filtering does not change what was fetched.

`rates_http.py` is the shared retry wrapper for `urllib` and `curl_cffi` (timeouts, connection resets, HTTP 408/429/502/503/504). New HTTP in a plugin should go through it instead of a one-off retry loop.

Some plugins only fill `chatcash:*` L1 rows and return no summary quotes. Some categories stored that way are intentionally omitted when the summary plugin reads the cache. Read `sources/userbot_cash` before assuming every cached cash row appears in `/rates`.

Plugins that are imported but absent from `PLUGIN_ORDER` still have a CLI entry (`rates.py <source_id>`). They are not part of the default summary. USDT and other reports may call them directly.

Do not document or special-case individual provider URLs, parsers, or fee formulas in shared modules. Keep that inside the plugin package.

### Adding or removing a summary plugin

1. Add the package under `sources/<id>/` with the exports above.
2. Import it in `sources/__init__.py` and insert it in `PLUGIN_ORDER` only if it belongs in the default summary.
3. If several plugins need the same upstream payload, add one `prim:*` key in `rates_primitives.py` and list it in `PRIMITIVE_KEYS_BY_SOURCE_ID`. Do not let each plugin fetch it.
4. If the bot's `/sources` list should show it, add a row to `bot/sources_catalog.py` under the category the user actually sees. That catalog is hand-maintained (names and public URLs). It is not derived from `SOURCE_ID`, because quote labels are dynamic and plugin URLs are often private APIs.
5. Cover the parser or formula with a unit test that does not hit the network. Mock the fetch.

## Reports outside the summary

These are separate modules. They share the unified cache and some primitives, but they are not "one more row in the summary."

| Module | CLI | What it produces |
|---|---|---|
| `cash_report.py` | `rates.py cash` | Cash-desk rates for a city. Aggregators are selected as a set (`banki`, `vbr`, `rbc`, `all`), not as summary plugins. |
| `exchange_report.py` | `rates.py exchange` | Branch ranking for a cash exchanger chain. |
| `usdt_report.py` | `rates.py usdt` | RUB/USDT and USDT/THB legs, then combined routes. Reuses `prim:*` where the summary already fetches them. |
| `calc_report.py` | `rates.py calc` | Compares channels for a RUB budget and a fiat rate the user supplies. |
| `sources/rshb_unionpay/card_fx_calculator.py` | `rates.py rshb` | Card-FX scenarios. The bot calls `build_rshb_text`. |

`bot/summary_adapter.py` is the bot's facade over these functions. It builds an argv or calls the report function and captures stdout. Keep the bot thin: argument parsing in `bot/*_args.py` or `bot/rates_tokens.py`, fetching and formatting in the report module.

## Telegram bot

`bot/main.py` is a Telethon client. Commands are regex handlers on `events.NewMessage`.

Public commands: `/start`, `/rates`, `/usdt`, `/cash`, `/exchange`, `/calc`, `/rshb`, `/sources`. `/refresh` (and `/refresh usdt`, `/refresh cash`) is limited to `BOT_ADMIN_ID`. `/gpt`, `/gpt_add`, and `/gpt_remove` manage access to the assistant.

`/sources` does not fetch anything. `bot/sources_catalog.py` formats a static list: one category block, then each source on its own line with URLs on the following lines. Link previews are off. Update that file when a public source is added, renamed, or dropped. Do not generate it by scraping plugin constants.

Long commands take a per-chat busy guard and a fetch timeout (`BOT_FETCH_TIMEOUT_SEC`, default 180s). They run the report in a worker thread.

Free text in a private chat from an allowed user goes to `chat_agent` when `CHAT_AGENT_URL` and `CHAT_AGENT_SHARED_SECRET` are set (`BOT_CHAT_AGENT_STREAM` selects the streaming route). Otherwise it uses `openai_gpt.py` directly.

`bot/guest.py` handles Telegram guest-chat queries. A new public command must be added to `_KNOWN` and handled in `prepare_guest_command`. `/refresh` and GPT commands stay denied. Guest replies are plain text with webpage previews disabled.

`bot/auto_leave.py` leaves chats the bot should not stay in. Do not weaken that path while editing update handlers.

## Userbot

`userbot/main.py` logs in as a user account, joins configured chats, and on each message runs the regex rules in `userbot/sources_config.py`. `userbot/parser.py` produces `ParsedRate` rows. `userbot/cache_writer.py` stores the latest row per currency under `chatcash:<source_id>` in L1.

The summary does not parse Telegram itself. A plugin reads those L1 rows. Channel id, display name, category, and pattern belong in `USERBOT_SOURCES`, not in the summary plugin.

Userbot credentials (`USERBOT_API_ID`, `USERBOT_API_HASH`, session volume) are separate from the bot token.

## Chat agent

`chat_agent/app/main.py` builds a FastAPI app with Redis, an LLM backend (`openai` or `google`), and an optional Postgres audit pool plus retention loop.

`POST /chat` and `POST /chat/stream` require `x-chat-agent-secret`. The orchestrator:

1. Asks the planner model for a JSON plan (`chat_agent/app/prompts/planner_system.py` and `commands_catalog.py`).
2. Rejects tool names outside `REGISTERED_TOOL_NAMES`.
3. `chat_agent/app/services/tools.py` validates arguments with pydantic models and executes a fixed `rates.py` invocation. The tool does not invent shell commands from user text beyond those models.
4. If `think` is set, a responder model writes the user-facing answer from the tool output. A direct report may return the tool text unchanged.
5. Out-of-scope plans get a fixed refusal. The agent does not answer general questions.

Redis holds the dialog. Postgres stores audit rows when audit is enabled. There is a small admin UI under `chat_agent/app/routes/admin.py`.

When adding a report the agent should be able to call, update the tool module, the planner catalog, and `REGISTERED_TOOL_NAMES` together. A catalog entry without a tool, or the reverse, is a bug.

## Scheduled jobs and browsers

`cron/refresh_expired_unified_cache.py` warms expired canonical L2 entries and selected L1 keys. It deliberately ignores orphan cash cells so a stale one-off city key does not trigger a refresh on every run.

`cron/refresh_sberbank_qr.py` writes one primitive. The matching summary plugin only reads that primitive. It does not call the upstream API.

`cron/refresh_proxy.py` maintains proxy lists used by plugins that cannot fetch directly. `cron/prune_old_cron_mail.py` is housekeeping.

`browser/` holds Chrome DevTools Protocol helpers for pages that block plain HTTP. They are launch-and-capture tools used by specific plugins and cron, not a general browser layer. Do not route a plugin through CDP unless plain HTTP is already known to fail.

`openrouter/proxy.py` is an optional, compose-disabled proxy in front of an LLM provider. It is not on the rates path.

## Tests and local checks

```
python3.9 -m unittest discover -s tests -q
```

Run a narrower module while iterating (`python3.9 -m unittest tests.test_rates_sources`). Tests must not call live provider APIs. Use the existing `unittest.mock` style.

`python3 rates.py env-status` prints whether expected variables are set, not their values. `python3 rates.py sources` prints registered source ids. `python3 rates.py --readonly` renders a summary from cache only.

## Conventions

- User-facing bot and CLI strings are Russian. Identifiers, this file, and commit messages are English.
- Match the surrounding style: `from __future__ import annotations`, type hints, no new framework.
- A failure in one plugin must not abort the summary. Warn and continue.
- Do not put provider-specific parsing in `rates.py`, `bot/main.py`, or the chat-agent orchestrator.
- Do not commit `.env`, `*.session`, `.rates_cache/`, or unified cache JSON.
- Do not widen `/refresh` or GPT admin commands beyond `BOT_ADMIN_ID`.
- After changing bot or userbot code on this machine, restart the corresponding container so the process reloads.
