# MCP GPS Fleet

[![CI](https://github.com/PartORG/MCP-gps-fleet/actions/workflows/ci.yml/badge.svg)](https://github.com/PartORG/MCP-gps-fleet/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server that gives an AI assistant access to a
(synthetic) GPS fleet: live vehicle data from a database **and** the company's know-how
from a searchable knowledge base. Ask *"Which vehicle had the most recent GPS jump, and
what could have caused it?"* and the assistant looks up the alert, reads the vehicle's
GPS history, finds similar past incidents, and explains with both sources.

It works with Claude (Code / Desktop), other MCP clients, or **fully offline** with a
local model through Ollama.

```text
                                       ┌─> db.py  (read-only SQL)          ──> data/fleet.db   facts
AI client ──MCP──> server.py (9 tools) ┤
 (stdio or HTTP)                       └─> rag.py (vectors + keywords      ──> data/kb.db      knowledge
                                                   + reranker, via Ollama)
```

**The idea:** structured facts ("where is FM-0977, how fast was it?") belong in SQL;
unstructured knowledge ("what causes GPS jumps, have we seen this before?") belongs in
a RAG index; MCP is the interface that lets any AI use both.

What's inside:
- **Fleet database** (SQLite): 50 vehicles, 40 drivers, ~800 trips and ~10k GPS samples
  over the last 7 days between 15 German cities, with deliberately injected anomalies
  (GPS jumps, speeding, fuel drops, long idling), each with a matching alert.
- **Knowledge base** (`kb/`): 8 guides (alerts, GPS, fuel, drivers, safety, maintenance)
  and 20 past incident reports about the same vehicles.
- **Hybrid RAG**: vector search (Ollama embeddings + sqlite-vec) and keyword search
  (SQLite FTS5) fused, then reranked by a small cross-encoder, all local, measured
  by an eval.
- **Offline chat client** (Pydantic AI + qwen3 via Ollama), an end-to-end model eval,
  token auth, health probes, Docker, Helm, CI, and OpenTelemetry tracing.

All data and documents are fictional.

## Tools

| Tool | What it answers | Parameters (`?` = optional) |
|---|---|---|
| `get_vehicle_status` | Where is a vehicle, who drives it, fuel, odometer, last speed | `registration` |
| `list_vehicles` | Which vehicles are offline / idle / in maintenance, and where | `status?` |
| `get_vehicle_history` | GPS samples of one vehicle in a time window (position, speed, fuel, ignition) | `registration`, `since?`, `until?`, `limit?` |
| `get_fleet_statistics` | Vehicles per status, trips and km in the last 24 h, alerts per type, average fuel | – |
| `find_idle_vehicles` | Vehicles that have not moved for N hours | `hours?` |
| `find_speed_violations` | Samples above a speed limit, **fastest first**, with the trip's driver | `min_speed_kmh?`, `since_hours?`, `limit?` |
| `find_anomalies` | Alerts (gps_jump, speeding, fuel_drop, long_idle), **newest first** | `registration?`, `type?`, `since_hours?`, `limit?` |
| `search_fleet_knowledge` | Guides and policies: what an alert means, speed limits, how to investigate | `query`, `limit?` |
| `search_similar_incidents` | Past incident reports with cause and resolution | `description`, `limit?` |

All tools are read-only (the database is opened read-only) and return typed JSON
(Pydantic models, published as output schemas). Plates are `FM-` plus four digits.
The search tools return a 0–1 relevance score; near 0 means "nothing relevant".

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.14 itself)
- [Ollama](https://ollama.com) running locally, for the embeddings (and the offline chat)
- Optional: Docker, Helm/kubectl for deployment; Node for the MCP Inspector

## 1. Run it locally, fully offline

```bash
git clone https://github.com/PartORG/MCP-gps-fleet.git && cd MCP-gps-fleet
uv sync
uv run fleet-seed     # creates data/fleet.db: synthetic data for the 7 days up to now
uv run fleet-ingest   # indexes kb/ into data/kb.db; the first run pulls the embedding model
                      # (nomic-embed-text, 274 MB) into Ollama and the reranker (22 MB)
```

The data is relative to the moment of seeding, so re-run `fleet-seed` when it is a few
days old. Re-run `fleet-ingest` after editing `kb/`.

**Chat with it, offline** (`fleet-chat`, a Pydantic AI agent with a local model):

```bash
ollama create fleet-qwen3 -f Modelfile   # once: qwen3:8b with a 12k context window
uv run fleet-chat                        # interactive; empty line or Ctrl-D quits
uv run fleet-chat "Which vehicles are offline right now, and where are they?"
```

It prints every tool call the model makes. Expect 15–90 s per question on an 8 GB
laptop GPU. Other models: `FLEET_CHAT_MODEL=<ollama model>` (needs tool support; see
[Chat model comparison](#chat-model-comparison)).

**Inspect the tools by hand** in the browser: `npx @modelcontextprotocol/inspector uv run fleet-mcp`

## 2. Use it from Claude Code or another AI client

### Claude Code (stdio, nothing to start)

`.mcp.json` in this repo registers the server. Start `claude` in the project folder,
approve the `fleet` server, and ask, e.g.:

> Which vehicles had fuel drops today, what does the data show, and what could have caused it?

Claude Code starts `uv run fleet-mcp` itself and talks to it over stdin/stdout.

### Claude Desktop (stdio)

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "fleet": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/MCP-gps-fleet", "run", "fleet-mcp"]
    }
  }
}
```

### Over HTTP (any MCP client, also on another machine)

Run the server as a long-lived HTTP service (Streamable HTTP):

```bash
export FLEET_API_TOKEN=$(openssl rand -hex 32)
FLEET_TRANSPORT=http uv run fleet-mcp   # http://127.0.0.1:8000/mcp, needs Authorization: Bearer <token>
```

Then point a client at it:

```bash
# Claude Code
claude mcp add --transport http fleet-http http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $FLEET_API_TOKEN"

# fleet-chat (offline client) against the HTTP server instead of starting its own
FLEET_MCP_URL=http://127.0.0.1:8000/mcp uv run fleet-chat
```

Other clients take the same two things, the URL and the header. For example
Cursor (`.cursor/mcp.json`) or VS Code (`.vscode/mcp.json`):

```jsonc
// Cursor
{ "mcpServers": { "fleet": { "url": "http://127.0.0.1:8000/mcp",
                             "headers": { "Authorization": "Bearer <token>" } } } }
// VS Code
{ "servers": { "fleet": { "type": "http", "url": "http://127.0.0.1:8000/mcp",
                          "headers": { "Authorization": "Bearer <token>" } } } }
```

Clients that only speak stdio (or only OAuth for remote servers, like Claude
Desktop's connectors) can use the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
bridge as a stdio command: `npx mcp-remote <url> --header "Authorization: Bearer <token>"`.
In your own code, any MCP SDK works (see `src/fleet_mcp/chat.py` for Pydantic AI).

The server refuses to listen on anything but localhost without a token, and tokens
must be at least 32 characters.

## 3. Deploy it to a server

What runs on the server is small: `fleet-mcp` (~400 MiB RAM) plus Ollama for the
**embeddings only** (~450 MiB, CPU is enough). The chat model never runs there: the
AI client brings its own (Claude, or `fleet-chat` on your laptop). A 2 vCPU / 2–4 GB
VM is enough.

Every option below exposes the same endpoints:
- `/mcp`: the MCP endpoint, requires `Authorization: Bearer <FLEET_API_TOKEN>`
- `/healthz`: liveness (the process answers), no token
- `/readyz`: readiness (both databases in place, else 503), no token

| Strategy | Good for | You manage |
|---|---|---|
| **A. Docker Compose + reverse proxy** | one VM, simplest | Docker, a TLS proxy (e.g. Caddy) |
| **B. Kubernetes (k3s) + Helm** | production-like, probes, rollouts, several services | a cluster, an Ingress |
| **C. systemd + uv, no containers** | a plain Linux box, smallest footprint | Ollama install, a unit file, a TLS proxy |

### A. Docker Compose + reverse proxy (recommended for one server)

```bash
git clone https://github.com/PartORG/MCP-gps-fleet.git && cd MCP-gps-fleet
echo "FLEET_API_TOKEN=$(openssl rand -hex 32)" > .env
docker compose up -d --build   # Ollama + fleet-mcp; ready after ~1 min (first start pulls models)
curl -s http://127.0.0.1:8000/readyz
```

Compose publishes the port on `127.0.0.1` only, so put a TLS-terminating proxy in
front. With [Caddy](https://caddyserver.com) (automatic Let's Encrypt certificates),
the whole `Caddyfile` is:

```
mcp.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Clients then use `https://mcp.example.com/mcp` with the token. With nginx, disable
response buffering for `/mcp` (`proxy_buffering off;`) because MCP streams responses.

### B. Kubernetes (k3s) + Helm

The chart (`charts/fleet-mcp`) deploys fleet-mcp and an Ollama; CI publishes the image
to `ghcr.io/partorg/mcp-gps-fleet`.

```bash
kubectl create namespace fleet-mcp
kubectl -n fleet-mcp create secret generic fleet-mcp-token --from-literal=token=$(openssl rand -hex 32)
helm install fleet charts/fleet-mcp -n fleet-mcp
kubectl -n fleet-mcp port-forward svc/fleet 8000:8000   # quick test
```

An init container seeds the data and builds the index (Kubernetes retries it until
Ollama is up), then the server runs non-root on a read-only root filesystem with
liveness/readiness probes and requests/limits measured under load. The chart ships no
Ingress: add one for your controller (k3s ships Traefik) with TLS, pointing at service
`fleet`, port 8000. Useful values: `ollama.enabled=false` + `ollama.url=...` (use an
existing Ollama), `extraEnv` (e.g. tracing), `image.tag` (pin a commit SHA).

### C. systemd + uv (no containers)

Install uv and [Ollama](https://ollama.com/download) on the server, clone to
`/opt/MCP-gps-fleet`, run `uv sync`, and create `/etc/fleet-mcp.env`:

```bash
FLEET_TRANSPORT=http
FLEET_HOST=127.0.0.1   # behind a TLS proxy (see A); use 0.0.0.0 only with a firewall
FLEET_API_TOKEN=<openssl rand -hex 32>
```

and `/etc/systemd/system/fleet-mcp.service` (adjust the `uv` path, e.g. `~/.local/bin/uv`):

```ini
[Unit]
Description=Fleet MCP server
After=network-online.target ollama.service

[Service]
User=fleet
WorkingDirectory=/opt/MCP-gps-fleet
EnvironmentFile=/etc/fleet-mcp.env
# fresh synthetic data and the index on every start
ExecStartPre=/usr/local/bin/uv run fleet-seed
ExecStartPre=/usr/local/bin/uv run fleet-ingest
ExecStart=/usr/local/bin/uv run fleet-mcp
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`systemctl enable --now fleet-mcp`, then put Caddy in front as in A.

### Operating notes (all strategies)

- **Fresh data**: the synthetic week ages; restart to reseed (`docker compose restart
  fleet-mcp`, `kubectl rollout restart deploy/fleet`, `systemctl restart fleet-mcp`),
  e.g. weekly. Nothing needs a backup: all data is generated or rebuilt from `kb/`.
- **Token**: one shared token; rotate by changing it and restarting. For per-user
  access, switch to the MCP SDK's OAuth support (see `require_token` in `server.py`).
- **One instance**: MCP sessions live in process memory, so don't run replicas behind
  a load balancer without sticky sessions.
- **Monitoring**: probe `/readyz`; for traces set `OTEL_EXPORTER_OTLP_ENDPOINT` (below).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FLEET_TRANSPORT` | `stdio` | `stdio` (the client starts the server) or `http` (long-running server) |
| `FLEET_HOST` | `127.0.0.1` | HTTP bind address; anything but localhost requires `FLEET_API_TOKEN` |
| `FLEET_PORT` | `8000` | HTTP port |
| `FLEET_API_TOKEN` | *(unset)* | Require `Authorization: Bearer <token>` (min. 32 chars); `fleet-chat` sends it too |
| `FLEET_DB` | `data/fleet.db` | Fleet database |
| `FLEET_KB_DB` | `data/kb.db` | Knowledge-base index |
| `FLEET_MODELS_DIR` | `data/models` | Reranker model location |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama: embeddings, and the `fleet-chat` model |
| `FLEET_CHAT_MODEL` | `fleet-qwen3` | `fleet-chat`: Ollama model to chat with |
| `FLEET_MCP_URL` | *(unset = stdio)* | `fleet-chat`: connect to this HTTP server instead of starting one |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(unset = off)* | OTLP/HTTP collector for traces, e.g. `http://localhost:4318` |

Settings can live in a `.env` file (gitignored): `uv run --env-file .env fleet-mcp`.

## Quality and evaluation

### Search quality

The knowledge-base search runs in three stages:

1. **Two retrievers**, 20 candidates each: vector search (meaning) and keyword search
   (BM25). Vectors handle paraphrases; keywords handle exact tokens like plates,
   incident numbers, "561/2006".
2. **Fusion** of both rankings with Reciprocal Rank Fusion → top 20.
3. **Reranking** by a small cross-encoder (flashrank MiniLM-L-12, CPU, no PyTorch),
   which reads the question and each chunk together.

`uv run fleet-eval` scores each stage on 22 questions (`tests/eval_questions.json`,
written before tuning, in user words):

| mode | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| vector | 0.82 | 0.91 | 0.95 | 0.87 |
| keyword | 0.82 | 0.95 | 1.00 | 0.90 |
| hybrid | 0.82 | 1.00 | 1.00 | 0.91 |
| **rerank** (used by the tools) | **0.95** | 1.00 | 1.00 | **0.98** |

hit@k = share of questions with the right document in the top k; MRR = mean of
1/rank. Reranking costs ~250 ms per search. `pytest` fails if the rerank mode drops
below hit@1 0.9 / hit@3 0.95 or any stage ranks worse than the one before.

### Chat model comparison

`uv run fleet-agent-eval [--runs N] [variant ...]` runs the `fleet-chat` agent with
several local models (same 12k context each) on 10 questions and checks every answer:
**tools** (called what the question needs), **facts** (contains the expected facts,
computed from `fleet.db` at eval time) and **invented** (plates that appear in no tool
result). A run passes with all three. Missing variants are created automatically from
their base model (`ollama pull qwen3:4b` etc. first).

First comparison (6 questions, one run each; RTX 4070 Laptop, 8 GB):

| model | passed | median time | notes |
|---|---|---|---|
| **qwen3:8b** (default) | 6/6 | 38 s | |
| qwen3:8b, thinking off | 5/6 | 26 s | reported the newest speeding alert instead of the fastest |
| qwen3:4b | 6/6 | 34 s | half the memory, but the most context (9.2k of 12k) |
| llama3.2:3b | 3/6 | 2 s | ~20× faster; tool call written as text, skipped searches, once invented a plate |

After that, the tool descriptions were sharpened (`find_speed_violations` "fastest
first" vs `find_anomalies` "newest first"; no example plate the model could copy). A
10-question, 3-run comparison was cut short by low memory: in the completed runs qwen3:8b
passed 19/20 and qwen3:8b with thinking off 19/19 (including the newest-vs-fastest
question it failed before). qwen3:4b vs qwen3:8b is still undecided, so the default
stays qwen3:8b.

### Tracing (OpenTelemetry)

Off by default. With `OTEL_EXPORTER_OTLP_ENDPOINT` set, every question becomes **one
trace across both processes**: the agent, each model request, each MCP tool call inside
the server, the search stages (`rag.vector`, `rag.keyword`, `rag.rerank`) and every
Ollama HTTP call.

```bash
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/jaeger:2.21.0
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run fleet-chat "why did FM-0977 jump?"
# open http://localhost:16686, service "fleet-chat"
```

Most spans come from the libraries: Pydantic AI, the MCP SDK (which passes the trace
context in each request's `_meta`, joining the server's spans to the client's trace even
over stdio) and the httpx instrumentation. Our code adds only the search-stage spans
and the exporter setup (`telemetry.py`). Query texts are not recorded.

## Development

```bash
uv run pytest            # 34 tests; the RAG quality tests are skipped without Ollama
uv run ruff check && uv run ruff format --check
helm lint charts/fleet-mcp
```

CI (`.github/workflows/ci.yml`) runs the same on every push and pull request, builds the
image, and publishes it to `ghcr.io/partorg/mcp-gps-fleet` from `master`.

| Path | What it is |
|---|---|
| `src/fleet_mcp/server.py` | The MCP tools, health endpoints, token auth, transports |
| `src/fleet_mcp/db.py` | Read-only connection and every SQL query |
| `src/fleet_mcp/models.py` | Pydantic models = the tools' output schemas |
| `src/fleet_mcp/schema.sql`, `seed.py` | Database schema; synthetic data + anomaly injection (`fleet-seed`) |
| `src/fleet_mcp/rag.py` | Chunking, embeddings, vector + FTS5 index, hybrid search, reranking (`fleet-ingest`) |
| `src/fleet_mcp/chat.py` | Offline Pydantic AI client (`fleet-chat`) |
| `src/fleet_mcp/rag_eval.py`, `agent_eval.py` | Retrieval eval (`fleet-eval`), chat model eval (`fleet-agent-eval`) |
| `src/fleet_mcp/telemetry.py` | OpenTelemetry setup |
| `kb/`, `kb/incidents/` | Knowledge base: guides (one chunk per `##` section), incident reports (one chunk each) |
| `tests/` | pytest suite; `eval_questions.json` = retrieval eval set |
| `Modelfile` | `fleet-qwen3`: qwen3:8b with a 12k context |
| `Dockerfile`, `compose.yaml`, `charts/fleet-mcp/` | Image, local/server stack, Helm chart |

### Deliberate limits

Kept simple on purpose (each marked with a `ponytail:` comment in the code, with the
upgrade path): one server instance; one shared token instead of OAuth; Ollama models
re-downloaded when its Kubernetes pod is replaced; the whole knowledge base embedded
in one request; "nearest city" chosen from 15 hard-coded cities.

### How it was built

v1 database + tools → v1.5 stdio/HTTP → v2 RAG → v3 offline Pydantic AI client →
v4 hybrid search + retrieval eval → v4.5 chat model eval → v5 reranking →
v6 deployment (auth, Docker, Helm, CI) → OpenTelemetry tracing. Each step is one
commit with its measurements in the message.
