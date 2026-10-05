# SMSocket

Self-hosted **LLM API aggregation gateway**: one OpenAI-compatible endpoint in front of
many providers and many API keys per provider — rotation, failover, usage accounting and a
built-in dashboard.

```
client (OpenAI SDK / curl / any app)
   │  POST /v1/chat/completions   Authorization: Bearer <master key>
   ▼
SMSocket ── key pool      (rotation, 401/429 cooldown, per-key max_rpm)
           ── routing       (priority | round_robin | weighted, retry + failover)
           ── adapters      (openai-compatible  <->  anthropic)
           ── gate + meter  (max_concurrency, queueing, live in-flight)
           ── batch/async   (parallel send, background jobs)
           ── discovery     (test-message probe -> models + parameters)
           ── usage store   (SQLite -> /stats + /v1/usage + dashboard)
   ▼
provider A / provider B / provider C ...
```

## Features (v0.3)

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
  health), `/v1/usage?days=14` (daily totals + estimated cost), dashboard at `/`
- **Estimated cost per call**: a `pricing:` table (per 1M tokens, `'*'` = default row) is
  applied to every logged call, so `/v1/usage` and the dashboard show spend per model,
  provider and day
- **Embeddings**: declare `embeddings:` on any OpenAI-compatible provider and the alias is
  published in `/v1/models` and served by `POST /v1/embeddings` with the same failover rules
- **Spec-faithful streaming**: every SSE chunk is rewritten to carry
  `object: "chat.completion.chunk"` plus the public alias, so strict OpenAI SDK clients work
- **Safe `POST /admin/reload`**: a missing or provider-less config is refused (409) instead of
  wiping live routing, and key health (fails / cooldown) carries over across the reload

### v0.3 — billing currency, discovery, parallel/async

- **Billing currency of record** (`billing:` block): choose any display currency, keep a rate
  table, and price each provider in *its own* currency — a USD-priced provider and a
  CNY-priced provider add up into one correct total. `GET /admin/billing/convert` dry-runs a
  conversion, `POST /admin/billing/rates/refresh` pulls a live rate table, and
  `/stats` / `/v1/usage` take `?currency=` to re-render any report on the fly
- **Model discovery by test message**: `POST /admin/discover` asks a provider what it really
  serves (`GET /v1/models`), sends a minimal chat per id to prove the key can use it,
  measures latency + token usage, then probes the parameter set (`temperature`, `top_p`,
  `tools`, `response_format`, `reasoning_effort`, `stream`, ...) and classifies each as
  supported / rejected / unknown. Run it as a background job and poll
  `/admin/discover/status`; the catalog is cached (`GET /admin/models`) and
  `POST /admin/models/apply` publishes probed ids as aliases — add-only, never overwriting
  an alias a client already depends on
- **Parallel sending**: `POST /v1/batch` fires N payloads at once under its own
  `concurrency` cap, keeps input order, prices every item, and reports per-item status
  instead of failing the whole call; `fail_fast` stops after the first error
- **Async processing**: `POST /v1/batch?async=1` returns a job id immediately; poll
  `GET /v1/batches/{job}` (progress, partial results, `wait=5` blocks until done),
  list with `GET /v1/batches`, cancel with `DELETE /v1/batches/{job}`
- **Concurrency metering + admission gate**: `GET /concurrency` reports live in-flight,
  peak, queue depth, average wait, rejections and a per-provider breakdown;
  `max_concurrency` / `queue_wait` / `per_provider_concurrency` cap simultaneous upstream
  calls, and overflow gets a clean `429` + `Retry-After` instead of a pile-up

## Launch entry points (kernel start / stop / status)

The gateway core is started through per-shell entry points in the repo root. Every one of
them resolves the interpreter in this order: `SMSSOCKET_PYTHON` -> `.venv` -> `python`,
and prints the web URL, the `/v1` base URL and the client key in effect. Detached runs
write `runtime/SMSocket.pid` + `runtime/SMSocket*.log` (gitignored).

| shell | start | stop | status |
|---|---|---|---|
| PowerShell | `./start.ps1 [-Background] [-Port 8011] [-Config f] [-Reload] [-NoKey] [-RotateKey]` | `./stop.ps1 [-Force]` | `./status.ps1 [-Tail 15]` |
| bash / zsh | `./start.sh [-d] [-p 8011] [-c f] [--reload] [--no-key] [--rotate-key]` | `./stop.sh` | `./status.sh` |
| cmd.exe | `start.cmd [any run.py flag]` | Ctrl-C (foreground) | - |
| python | `python run.py ...` / `python -m SMSocket ...` | Ctrl-C | - |

```powershell
./start.ps1 -Background    # detached kernel; banner + runtime\SMSocket.log
./status.ps1               # up (pid ...) + web/api/key + last log lines
./stop.ps1                 # stops the kernel and its --reload children
```

```bash
./start.sh -d && ./status.sh && ./stop.sh
```

### v0.3 examples

```bash
# billing: display in CNY, price providers in their own currency, re-render in EUR
curl -X PUT localhost:8000/admin/billing -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"currency":"CNY","base":"USD","rates":{"USD":1,"CNY":7.1,"EUR":0.92}}'
curl "localhost:8000/v1/usage?days=30&currency=EUR" -H "Authorization: Bearer $KEY"
curl "localhost:8000/admin/billing/convert?amount=10&frm=USD&to=CNY" -H "Authorization: Bearer $KEY"

# discovery: what does this provider really serve, and with which parameters
curl -X POST localhost:8000/admin/discover -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"providers":["openai"],"probe_params":true,"concurrency":8,"async":1}'
curl localhost:8000/admin/discover/status -H "Authorization: Bearer $KEY"
curl -X POST localhost:8000/admin/models/apply -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"providers":["openai"]}'

# parallel batch (waits) / background job (polls)
curl -X POST localhost:8000/v1/batch -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"concurrency":8,"requests":[{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]},
       {"model":"gpt-4o-mini","messages":[{"role":"user","content":"again"}]}]}'
curl -X POST "localhost:8000/v1/batch?async=1" -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"requests":[...],"concurrency":16}'   # -> 202 {"job":...}
curl "localhost:8000/v1/batches/job_xxx?wait=5" -H "Authorization: Bearer $KEY"
curl -X DELETE localhost:8000/v1/batches/job_xxx -H "Authorization: Bearer $KEY"

# live concurrency
curl localhost:8000/concurrency
```

`-Background` / `-d` refuses to double-start (it reports the live pid instead), and
`stop.*` also clears children so `--reload` does not leave an orphan worker behind.

## Renamed from `llm_router` (v0.2 → v0.3)

| old | new |
|---|---|
| package `llm_router/` | package `SMSocket/` |
| env `LLMROUTER_CONFIG` / `_NO_KEY` / `_MASTER_KEY` / `_KEY_FILE` | `SMSSOCKET_*` (old names still read as fallback) |
| key file `router.key` | `smsocket.key` (an existing `router.key` keeps working) |
| generated key prefix `sk-router-` | `sk-socket-` |
| stream header `x-router-upstream` | `x-socket-upstream` (both sent) |

Routes, config keys and the SQLite file are unchanged, so an existing deployment only has to
restart; `import llm_router` is what breaks, and that is the point of the rename.

## Quick start

```bash
pip install -r requirements.txt
copy config.example.yaml config.yaml      # Windows (cp on *nix), then edit providers
set OPENAI_API_KEY=***                    # ${...} is expanded from the environment
python run.py                             # http://127.0.0.1:8000  (dashboard at /)
```
On startup the console prints a banner: web dashboard URL, `.../v1` base URL, the
client key in effect, and provider/key/model counts. If `master_keys` is empty the
gateway mints a random `sk-socket-<32 bytes>` key and stores it in `smsocket.key`
next to the config, so restarts and `--reload` keep the same key (it is gitignored).
Resolution order: config `master_keys` > `SMSSOCKET_MASTER_KEY` > `smsocket.key`
(generated on first run). Flags: `--no-key` runs unauthenticated, `--rotate-key`
mints a fresh key and overwrites the file.

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-socket-..." -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

OpenAI SDK:

```python
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="***")
c.chat.completions.create(model="demo", messages=[{"role": "user", "content": "hi"}])
```
