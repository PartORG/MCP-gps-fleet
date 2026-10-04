# Fleet MCP

An MCP server that lets an LLM (Claude, or later a local Ollama model) query a
synthetic fleet-management database: 50 vehicles driving between German cities,
with injected anomalies (GPS jumps, speeding, fuel drops, long idling), plus a
knowledge base of guides and past incident reports searched with RAG.

```text
                                        ┌─> db.py  (read-only SQL)          ──> data/fleet.db   facts
Claude ──MCP──> server.py (8 tools) ────┤
                                        └─> rag.py (Ollama embeddings + KNN) ─> data/kb.db      knowledge
```

Structured facts ("where is FM-0977, how fast was it?") come from SQL;
unstructured know-how ("what causes GPS jumps, have we seen this before?")
comes from the knowledge base in `kb/`.

## Quick start

```bash
uv sync
ollama pull nomic-embed-text   # local embedding model (once); Ollama must be running
uv run fleet-seed              # (re)creates data/fleet.db, ~10k telemetry rows ending "now"
uv run fleet-ingest            # embeds kb/*.md into data/kb.db (re-run after editing kb/)
uv run pytest                  # RAG tests are skipped if Ollama is not running
```

The data is relative to the time of seeding ("last 24 hours"), so re-run
`fleet-seed` when it gets old.

### Use it from Claude Code

`.mcp.json` in this folder registers the server; start `claude` here and approve
the `fleet` server.  Then ask e.g. *"Which vehicles had fuel drops today, what
does the data show, and what could have caused it?"*

### Transports: stdio (default) or HTTP

Same server, same tools; environment variables pick how clients reach it.

| Variable | Default | Meaning |
|---|---|---|
| `FLEET_TRANSPORT` | `stdio` | `stdio`: the client starts the server (Claude Code via `.mcp.json`). `http`: a long-running server at `http://<host>:<port>/mcp` (Streamable HTTP) |
| `FLEET_HOST` | `127.0.0.1` | Only `127.0.0.1`, `localhost` or `::1` until auth exists (v6) |
| `FLEET_PORT` | `8000` | HTTP port |
| `FLEET_DB` | `data/fleet.db` | Fleet database file |
| `FLEET_KB_DB` | `data/kb.db` | Knowledge-base index file |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama, used to embed search queries |

```bash
FLEET_TRANSPORT=http uv run fleet-mcp
# or keep the settings in a .env file (gitignored); uv reads it, no extra package needed:
uv run --env-file .env fleet-mcp

# connect Claude Code to the running HTTP server:
claude mcp add --transport http fleet-http http://127.0.0.1:8000/mcp
```

### Inspect it by hand

```bash
npx @modelcontextprotocol/inspector uv run fleet-mcp   # MCP Inspector in the browser (needs Node)
```

## Layout

| File | What it does |
|---|---|
| `src/fleet_mcp/schema.sql` | Tables, indexes, `last_position` view |
| `src/fleet_mcp/seed.py` | Synthetic data generator + anomaly injection |
| `src/fleet_mcp/db.py` | Read-only connection and every SQL query |
| `src/fleet_mcp/models.py` | Pydantic models = the tools' output schemas |
| `src/fleet_mcp/rag.py` | Chunking, Ollama embeddings, sqlite-vec index and search |
| `src/fleet_mcp/server.py` | The MCP tools |
| `kb/*.md` | Guides and policies (one search chunk per `##` section) |
| `kb/incidents/*.md` | Past incident reports (one chunk per report) |

## Tools

| Tool | Answers |
|---|---|
| `get_vehicle_status` | Where is FM-0231, who drives it, how much fuel? |
| `get_vehicle_history` | What did it do between two times? |
| `get_fleet_statistics` | How is the fleet doing overall? |
| `find_idle_vehicles` | Which vehicles have not moved for N hours? |
| `find_speed_violations` | Who drove faster than 130 km/h? |
| `find_anomalies` | Which alerts were raised (by vehicle / type)? |
| `search_fleet_knowledge` | What does this alert mean, what is the policy, how do I investigate? |
| `search_similar_incidents` | Have we seen this before, what was the cause and fix? |

## Roadmap

v1 database + tools → v1.5 stdio/HTTP transports → v2 RAG over `kb/` (this) → v3 Pydantic AI offline client
(qwen3:8b) → v4 hybrid search + eval → v5 flashrank reranking → v6 deployment (auth, Docker, health probes).
