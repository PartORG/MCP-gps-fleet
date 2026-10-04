# Fleet MCP

An MCP server that lets an LLM (Claude, or a fully local Ollama model) query a
synthetic fleet-management database: 50 vehicles driving between German cities,
with injected anomalies (GPS jumps, speeding, fuel drops, long idling), plus a
knowledge base of guides and past incident reports searched with RAG.

```text
                                        ┌─> db.py  (read-only SQL)          ──> data/fleet.db   facts
Claude ──MCP──> server.py (9 tools) ────┤
                                        └─> rag.py (vectors + keywords)     ──> data/kb.db     knowledge
```

Structured facts ("where is FM-0977, how fast was it?") come from SQL;
unstructured know-how ("what causes GPS jumps, have we seen this before?")
comes from the knowledge base in `kb/`.

## Quick start

```bash
uv sync
uv run fleet-seed              # (re)creates data/fleet.db, ~10k telemetry rows ending "now"
uv run fleet-ingest            # indexes kb/*.md into data/kb.db (re-run after editing kb/);
                               # Ollama must be running; the first run pulls the embedding
                               # model into it and downloads the reranker model (~22 MB)
uv run fleet-eval              # retrieval quality per search mode (see "Search quality")
uv run fleet-agent-eval        # chat models compared end to end (see "Chat model comparison")
uv run pytest                  # RAG tests are skipped if Ollama is not running
```

The data is relative to the time of seeding ("last 24 hours"), so re-run
`fleet-seed` when it gets old.

### Use it from Claude Code

`.mcp.json` in this folder registers the server; start `claude` here and approve
the `fleet` server.  Then ask e.g. *"Which vehicles had fuel drops today, what
does the data show, and what could have caused it?"*

### Use it fully offline: `fleet-chat`

A Pydantic AI agent with a local model (qwen3:8b) talks to the same MCP server, so
nothing leaves the machine.

```bash
ollama create fleet-qwen3 -f Modelfile   # once: qwen3:8b with a 12k context (see Modelfile)
uv run fleet-chat                        # interactive; empty line or Ctrl-D quits
uv run fleet-chat "Which vehicle had the most recent GPS jump, and why?"
```

It prints every tool call the model makes and the context size it used.  Expect
30-90 s per question on an 8 GB laptop GPU.  `FLEET_CHAT_MODEL=llama3.1:8b` tries
another Ollama model with tool support; `FLEET_MCP_URL=http://127.0.0.1:8000/mcp`
connects to a running HTTP server instead of starting one over stdio.

### Transports: stdio (default) or HTTP

Same server, same tools; environment variables pick how clients reach it.

| Variable | Default | Meaning |
|---|---|---|
| `FLEET_TRANSPORT` | `stdio` | `stdio`: the client starts the server (Claude Code via `.mcp.json`). `http`: a long-running server at `http://<host>:<port>/mcp` (Streamable HTTP) |
| `FLEET_HOST` | `127.0.0.1` | Any other address (e.g. `0.0.0.0`) requires `FLEET_API_TOKEN` |
| `FLEET_API_TOKEN` | *(unset)* | HTTP: require `Authorization: Bearer <token>` (min. 32 chars); `fleet-chat` sends it too |
| `FLEET_PORT` | `8000` | HTTP port |
| `FLEET_DB` | `data/fleet.db` | Fleet database file |
| `FLEET_MODELS_DIR` | `data/models` | Where the reranker model is stored |
| `FLEET_KB_DB` | `data/kb.db` | Knowledge-base index file |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama: embeddings, and the `fleet-chat` model |
| `FLEET_CHAT_MODEL` | `fleet-qwen3` | `fleet-chat` only: Ollama model to chat with |
| `FLEET_MCP_URL` | *(unset = stdio)* | `fleet-chat` only: MCP server URL to connect to over HTTP |

```bash
FLEET_TRANSPORT=http uv run fleet-mcp
# or keep the settings in a .env file (gitignored); uv reads it, no extra package needed:
uv run --env-file .env fleet-mcp

# connect Claude Code to the running HTTP server:
claude mcp add --transport http fleet-http http://127.0.0.1:8000/mcp
# with a token:  ... --header "Authorization: Bearer $FLEET_API_TOKEN"
```

### Inspect it by hand

```bash
npx @modelcontextprotocol/inspector uv run fleet-mcp   # MCP Inspector in the browser (needs Node)
```

## Chat model comparison

`uv run fleet-agent-eval` runs the same agent as `fleet-chat` with several local
models (qwen3:8b, qwen3:8b with thinking off, qwen3:4b, llama3.2:3b, all with the
same 12k context) on 6 questions, and checks each answer:

- **tools**: the model called the tools the question needs;
- **facts**: the answer contains the expected facts, computed from `fleet.db` at
  eval time (e.g. the plates of the vehicles offline right now);
- **invented**: plates in the answer that appear in no tool result, i.e. made up.

A run passes with all three.  Missing model variants are created automatically
(`ollama pull` the base model first, e.g. `ollama pull qwen3:4b`).  Pass variant
names to run a subset: `uv run fleet-agent-eval qwen3:4b llama3.2:3b`.

## Deployment

The HTTP server has two unauthenticated endpoints for orchestrators: `/healthz`
(liveness: the process answers) and `/readyz` (readiness: both databases exist; 503
otherwise).  Everything else needs the bearer token once `FLEET_API_TOKEN` is set,
and it must be set for any non-localhost address.

**Docker Compose** (Ollama + fleet-mcp, like production):

```bash
echo "FLEET_API_TOKEN=$(openssl rand -hex 32)" > .env
docker compose up --build        # MCP at http://127.0.0.1:8000/mcp, ready after ~1 min
```

The container seeds fresh data and builds the index at start (pulling the embedding
model into Ollama the first time); the reranker model is baked into the image.

**Kubernetes (Helm)**: chart in `charts/fleet-mcp`, image published by CI to
`ghcr.io/partorg/mcp-gps-fleet`.

```bash
kubectl create namespace fleet-mcp
kubectl -n fleet-mcp create secret generic fleet-mcp-token --from-literal=token=$(openssl rand -hex 32)
helm install fleet charts/fleet-mcp -n fleet-mcp
kubectl -n fleet-mcp port-forward svc/fleet 8000:8000
```

An init container seeds the data and builds the index (retried by Kubernetes until
Ollama is up); the server runs non-root on a read-only root filesystem with
liveness/readiness probes and resource requests/limits measured under load
(~400 MiB fleet-mcp, ~450 MiB Ollama).  Deliberate limits: one replica (MCP sessions
live in process memory), one shared token (switch to the SDK's OAuth support for
per-user access), and `emptyDir` volumes (a new pod reseeds; Ollama re-pulls 274 MB).
To use an existing Ollama: `--set ollama.enabled=false --set ollama.url=http://...`.

**CI** (`.github/workflows/ci.yml`): ruff, pytest (RAG quality tests skip without
Ollama), `helm lint`, and the image build; pushes to `master` publish the image.

## Search quality

The knowledge-base search runs in three stages:

1. **Two retrievers**, 20 candidates each: vector search (meaning, via Ollama
   embeddings + sqlite-vec) and keyword search (SQLite FTS5, BM25).  Vectors handle
   paraphrases, keywords handle exact tokens like plates, incident numbers, "561/2006".
2. **Fusion** of the two rankings with Reciprocal Rank Fusion -> top 20.
3. **Reranking** of those 20 by a small cross-encoder (flashrank, MiniLM-L-12, CPU,
   no PyTorch), which reads the question and each chunk together.  Its 0..1 score
   also tells the LLM when nothing relevant was found (all scores near 0).

`uv run fleet-eval` scores every mode on the 22 questions in
`tests/eval_questions.json` (written before tuning, in user words) and prints the
rank of the right document per question.  Current results:

| mode | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| vector (v2) | 0.82 | 0.91 | 0.95 | 0.87 |
| keyword | 0.82 | 0.95 | 1.00 | 0.90 |
| hybrid | 0.82 | 1.00 | 1.00 | 0.91 |
| **rerank** (used by the tools) | **0.95** | 1.00 | 1.00 | **0.98** |

hit@k = share of questions with the right document in the top k; MRR = mean of
1/rank.  With 22 questions one question is ~4.5 points, so read these as a
direction.  Reranking costs ~250 ms per search (hybrid alone: ~30 ms), small next to
an LLM tool call.  `pytest` fails if the rerank mode drops below hit@1 0.9 / hit@3
0.95, or if any stage ranks worse than the one before it.

## Layout

| File | What it does |
|---|---|
| `src/fleet_mcp/schema.sql` | Tables, indexes, `last_position` view |
| `src/fleet_mcp/seed.py` | Synthetic data generator + anomaly injection |
| `src/fleet_mcp/db.py` | Read-only connection and every SQL query |
| `src/fleet_mcp/models.py` | Pydantic models = the tools' output schemas |
| `src/fleet_mcp/rag.py` | Chunking, Ollama embeddings, vector + FTS5 index, hybrid search, reranking |
| `src/fleet_mcp/rag_eval.py` | Retrieval eval (`fleet-eval`) |
| `src/fleet_mcp/agent_eval.py` | Chat model comparison (`fleet-agent-eval`) |
| `tests/eval_questions.json` | Eval questions with their expected documents |
| `src/fleet_mcp/server.py` | The MCP tools |
| `src/fleet_mcp/chat.py` | Offline Pydantic AI client (`fleet-chat`) |
| `Modelfile` | `fleet-qwen3`: qwen3:8b with a bigger context window |
| `Dockerfile`, `compose.yaml` | Server image; local stack with Ollama |
| `charts/fleet-mcp/` | Helm chart (fleet-mcp + Ollama) |
| `.github/workflows/ci.yml` | Lint, tests, Helm lint, image publish |
| `kb/*.md` | Guides and policies (one search chunk per `##` section) |
| `kb/incidents/*.md` | Past incident reports (one chunk per report) |

## Tools

| Tool | Answers |
|---|---|
| `get_vehicle_status` | Where is FM-0231, who drives it, how much fuel? |
| `list_vehicles` | Which vehicles are offline / in maintenance, and where are they? |
| `get_vehicle_history` | What did it do between two times? |
| `get_fleet_statistics` | How is the fleet doing overall? |
| `find_idle_vehicles` | Which vehicles have not moved for N hours? |
| `find_speed_violations` | Who drove faster than 130 km/h? |
| `find_anomalies` | Which alerts were raised (by vehicle / type)? |
| `search_fleet_knowledge` | What does this alert mean, what is the policy, how do I investigate? |
| `search_similar_incidents` | Have we seen this before, what was the cause and fix? |

## Roadmap

v1 database + tools → v1.5 stdio/HTTP transports → v2 RAG over `kb/` → v3 Pydantic AI offline client
(qwen3:8b) → v4 hybrid search + eval → v4.5 chat model eval → v5 flashrank reranking → v6 deployment: auth, Docker, Helm, CI (this).
