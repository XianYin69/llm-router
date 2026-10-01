# llm-router

Self-hosted **LLM API aggregation gateway** (Free-LLM-API style): one OpenAI-compatible
endpoint in front of many upstream providers, with multi-key pools, automatic fallback,
cooldown on 429/401, and a tiny live dashboard.

```
client ──POST /v1/chat/completions──▶ llm-router ──▶ provider A (key1 key2 key3)
                                          │           provider B
                                          └─▶ fallback chain, per-key cooldown
```

## Features (v0.1)

- OpenAI-compatible `POST /v1/chat/completions` (JSON + SSE streaming) and `GET /v1/models`
- **Key pool per provider** — round-robin, keys parked for `cooldown` seconds on 401/403/429
- **Fallback chains** — one public name maps to an ordered list of `provider/model` targets
- **Weighted routing** — higher `weight` provider is tried first
- Transparent master-key auth (`Authorization: Bearer …`), or `anon_allowed: true` for local use
- `${ENV}` / `${ENV:default}` expansion in config — keep secrets out of the repo
- `/stats` (requests, errors, avg latency, tokens) + `/pool` (key health) + `/` dashboard
- Zero database: single process, in-memory state, container-friendly

## Quick start

```bash
git clone https://github.com/XianYin69/llm-router.git
cd llm-router
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy config.example.yaml config.yaml                  # add your upstream keys
python run.py --port 8000
```

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-local-dev" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'
```

Dashboard: <http://127.0.0.1:8000/>

## Configuration

| key | meaning |
|---|---|
| `providers.<name>.base_url` | OpenAI-compatible root, ends with `/v1` |
| `providers.<name>.api_keys` | pool of keys (round-robin) |
| `providers.<name>.weight` | higher = tried first |
| `providers.<name>.models` | upstream model ids (also auto-routed by their own name) |
| `routes.<public>` | ordered fallback chain, e.g. `[openrouter/openai/gpt-4o-mini, deepseek/deepseek-chat]` |
| `master_keys` | keys clients must present |
| `cooldown` | seconds a failing key is parked |

## Docker

```bash
docker build -t llm-router .
docker run -p 8000:8000 -e OPENROUTER_API_KEY_1=sk-or-... -v %cd%/config.yaml:/app/config.yaml llm-router
```

## Tests

```bash
pytest -q            # stubbed upstream, no network needed
```

## Roadmap (v0.2+)

per-key token budget & quota tracking · SQLite/Redis persistence · admin CRUD API ·
Anthropic/Gemini native adapters · cost accounting · load-balanced multi-worker mode.

## License

MIT
