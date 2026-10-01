# llm-router

Self-hosted **LLM API aggregation gateway**: one OpenAI-compatible endpoint in front of
many providers and many API keys per provider — rotation, failover, usage accounting and a
built-in dashboard.

```
client (OpenAI SDK / curl / any app)
   │  POST /v1/chat/completions   Authorization: Bearer <master key>
   ▼
llm-router ── key pool      (rotation, 401/429 cooldown, per-key max_rpm)
           ── routing       (priority | round_robin | weighted, retry + failover)
           ── adapters      (openai-compatible  <->  anthropic)
           ── usage store   (SQLite -> /stats + dashboard)
   ▼
provider A / provider B / provider C ...
```

## Features (v0.1)

- **OpenAI-compatible API**: `POST /v1/chat/completions` (JSON + SSE streaming), `GET /v1/models`
- **Multi-key pools**: a failing key (`401/403/408/429/5xx/529`) is parked for `cooldown`
  seconds and the request fails over to the next candidate automatically
- **Routing strategies**: `priority` (fallback chain), `round_robin`, `weighted`
- **Alias mapping**: publish `gpt-4o-mini` while upstream ids stay private; the same alias on
  two providers = automatic failover between them
- **Anthropic bridge**: `style: anthropic` providers are served as OpenAI chat format,
  including SSE translation and token accounting
- **Per-key `max_rpm`** limiting and `${ENV_VAR}` secret expansion (no keys in the file)
- **Observability**: `/stats` (per provider / per model / recent calls), `/pool` (live key
  health), dashboard at `/`, `POST /admin/reload` to re-read config without restart

## Quick start

```bash
pip install -r requirements.txt
copy config.example.yaml config.yaml      # Windows (cp on *nix), then edit providers
set OPENAI_API_KEY=***                    # ${...} is expanded from the environment
python run.py                             # http://127.0.0.1:8000  (dashboard at /)
```

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-router-..." -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

OpenAI SDK:

```python
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="***")
c.chat.completions.create(model="demo", messages=[{"role": "user", "content": "hi"}])
```
