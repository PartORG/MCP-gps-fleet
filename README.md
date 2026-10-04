# Fleet MCP

An MCP server that lets an LLM (Claude, or later a local Ollama model) query a
synthetic fleet-management database: 50 vehicles driving between German cities,
with injected anomalies (GPS jumps, speeding, fuel drops, long idling).

```text
Claude ──MCP (stdio)──> server.py (tools) ──> db.py (read-only SQL) ──> data/fleet.db
```

## Quick start

```bash
uv sync
uv run fleet-seed        # (re)creates data/fleet.db, ~10k telemetry rows ending "now"
uv run pytest            # seed + queries + tools checks
```

The data is relative to the time of seeding ("last 24 hours"), so re-run
`fleet-seed` when it gets old.

### Use it from Claude Code

`.mcp.json` in this folder registers the server; start `claude` here and approve
the `fleet` server.  Then ask e.g. *"Which vehicles had GPS jumps today, and
what did FM-0977 do around that time?"*

### Transports: stdio (default) or HTTP

Same server, same tools; environment variables pick how clients reach it.

| Variable | Default | Meaning |
|---|---|---|
| `FLEET_TRANSPORT` | `stdio` | `stdio`: the client starts the server (Claude Code via `.mcp.json`). `http`: a long-running server at `http://<host>:<port>/mcp` (Streamable HTTP) |
| `FLEET_HOST` | `127.0.0.1` | Only `127.0.0.1`, `localhost` or `::1` until auth exists (v6) |
| `FLEET_PORT` | `8000` | HTTP port |
| `FLEET_DB` | `data/fleet.db` | Database file |

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
| `src/fleet_mcp/server.py` | The MCP tools |

## Tools

| Tool | Answers |
|---|---|
| `get_vehicle_status` | Where is FM-0231, who drives it, how much fuel? |
| `get_vehicle_history` | What did it do between two times? |
| `get_fleet_statistics` | How is the fleet doing overall? |
| `find_idle_vehicles` | Which vehicles have not moved for N hours? |
| `find_speed_violations` | Who drove faster than 130 km/h? |
| `find_anomalies` | Which alerts were raised (by vehicle / type)? |

## Roadmap

v1 database + tools → v1.5 stdio/HTTP transports (this) → v2 RAG over `kb/` → v3 Pydantic AI offline client
(qwen3:8b) → v4 hybrid search + eval → v5 flashrank reranking → v6 deployment (auth, Docker, health probes).
