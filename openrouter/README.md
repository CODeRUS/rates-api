## OpenRouter fallback proxy

Проксирует OpenAI-совместимый endpoint `/v1/chat/completions` в OpenRouter и
подставляет fallback-маршрут из `config.json`:

- `models`: список моделей в порядке fallback
- `route`: обычно `fallback`

### Env

- `OPENROUTER_API_KEY` (обязательно)
- `OPENROUTER_PROXY_CONFIG` (опционально, путь к JSON)
- `OPENROUTER_PROXY_HOST` (дефолт `0.0.0.0`)
- `OPENROUTER_PROXY_PORT` (дефолт `18790`)
- `OPENROUTER_PROXY_TIMEOUT_SEC` (дефолт `120`)
- `OPENROUTER_HTTP_REFERER` (опционально)
- `OPENROUTER_X_TITLE` (опционально)

### Local run

```bash
OPENROUTER_API_KEY=... python3 openrouter/proxy.py
```

### Docker run

```bash
docker run --rm -p 18790:18790 \
  -e OPENROUTER_API_KEY=... \
  -v "$PWD/openrouter:/app/openrouter:ro" \
  -w /app \
  python:3.12-slim \
  python3 openrouter/proxy.py
```
