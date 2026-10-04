"""The Fleet MCP server: exposes the fleet database to an LLM as MCP tools.

Run:  uv run fleet-mcp                         stdio: an MCP client starts it (default)
      FLEET_TRANSPORT=http uv run fleet-mcp    HTTP: http://127.0.0.1:8000/mcp
      (or put the variables in a .env file:  uv run --env-file .env fleet-mcp)
See main() at the bottom for all settings.

How a tool call flows
---------------------
    Claude ──(JSON-RPC over stdio or HTTP)──> MCPServer ──> tool function below
                                                             ├─ Pydantic validates the arguments
                                                             │  (types + Field constraints)
                                                             ├─ db.<query>() on a read-only connection
                                                             └─ returns a Pydantic model
    Claude <── JSON result + output schema ─────────────────┘

Everything an LLM needs to pick the right tool comes from this file:
  * the function name            -> tool name
  * the docstring                -> tool description (write it for the model!)
  * the type hints + Field(...)  -> input JSON schema
  * the return annotation        -> output JSON schema (see models.py)

Error handling: `ToolError` is for failures we expect (unknown vehicle).  The
model sees our message and can correct itself.  Any other exception is treated
as a crash: the model only sees "Error executing tool ..." and the server logs
the traceback.
"""

import hmac
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from typing import Annotated

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from fleet_mcp import db, rag, telemetry
from fleet_mcp.models import (
    Alert,
    AlertType,
    FleetStatistics,
    IdleVehicle,
    KnowledgeChunk,
    SpeedViolation,
    Status,
    TelemetryPoint,
    VehicleStatus,
)

mcp = MCPServer(
    "fleet",
    instructions=(
        "Tools over a fleet-management database of ~50 vehicles driving between German cities. "
        "Vehicles are identified by registration plates: 'FM-' followed by four digits. All times are UTC. "
        "Typical flow: find_anomalies / find_speed_violations / find_idle_vehicles to discover "
        "something, or list_vehicles to see vehicles by status; then get_vehicle_status and "
        "get_vehicle_history to inspect one vehicle. "
        "The database holds facts; to explain them, use search_similar_incidents (how past cases "
        "like this were resolved) and search_fleet_knowledge (policies, troubleshooting guides). "
        "Base explanations on both: what the data shows and what the knowledge base says."
    ),
)

# Tells clients these tools only read data, so they can be auto-approved safely.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# Reusable parameter types.  Field constraints are enforced by Pydantic before
# our code runs, so a model asking for limit=10_000 gets a validation error.
# The format is described instead of shown: with an example plate in the description, a
# small model (llama3.2:3b in fleet-agent-eval) copied that non-existent plate into an answer.
Registration = Annotated[str, Field(description="Vehicle registration plate: 'FM-' followed by four digits")]
Limit = Annotated[int, Field(ge=1, le=500, description="Maximum number of rows to return")]
SinceHours = Annotated[int, Field(ge=1, le=24 * 30, description="Look back this many hours")]


@mcp.tool(annotations=READ_ONLY)
def get_vehicle_status(registration: Registration) -> VehicleStatus:
    """Current state of one vehicle: brand/model, status, driver, fuel level, odometer and
    its last known position (time, coordinates, nearest city, speed)."""
    with closing(db.connect()) as conn:
        status = db.vehicle_status(conn, registration)
    if status is None:
        raise ToolError(
            f"Unknown vehicle {registration!r}. Registrations are 'FM-' followed by four digits; list_vehicles shows all."
        )
    return status


@mcp.tool(annotations=READ_ONLY)
def list_vehicles(
    status: Annotated[
        Status | None, Field(description="Only vehicles with this status; omit for all")
    ] = None,
) -> list[VehicleStatus]:
    """List vehicles with the same details as get_vehicle_status (driver, fuel, last position
    and nearest city), optionally only those with one status.  Use it for questions like
    "which vehicles are offline / in maintenance, and where are they?"."""
    with closing(db.connect()) as conn:
        return db.vehicles(conn, status=status)


@mcp.tool(annotations=READ_ONLY)
def get_vehicle_history(
    registration: Registration,
    since: Annotated[
        datetime | None, Field(description="Start of the window (ISO 8601, UTC if no offset)")
    ] = None,
    until: Annotated[
        datetime | None, Field(description="End of the window (ISO 8601, UTC if no offset)")
    ] = None,
    limit: Limit = 100,
) -> list[TelemetryPoint]:
    """Telemetry samples (time, position, nearest city, speed, heading, ignition, fuel) of one
    vehicle, oldest first.  Without since/until it returns the most recent samples.  Use it to
    see what a vehicle actually did around an alert, e.g. position jumps or fuel changes."""
    with closing(db.connect()) as conn:
        if db.vehicle_status(conn, registration) is None:
            raise ToolError(
                f"Unknown vehicle {registration!r}. Registrations are 'FM-' followed by four digits; list_vehicles shows all."
            )
        return db.vehicle_history(conn, registration, since, until, limit)


@mcp.tool(annotations=READ_ONLY)
def get_fleet_statistics() -> FleetStatistics:
    """Fleet overview: vehicles per status, trips and kilometres in the last 24 hours,
    alerts per type in the last 7 days, average fuel level."""
    with closing(db.connect()) as conn:
        return db.fleet_statistics(conn)


@mcp.tool(annotations=READ_ONLY)
def find_idle_vehicles(
    hours: Annotated[int, Field(ge=1, le=24 * 30, description="Minimum hours without telemetry")] = 4,
) -> list[IdleVehicle]:
    """Vehicles that have not sent telemetry (i.e. not been driven) for at least `hours` hours,
    longest-idle first, with where they are parked and their recorded status."""
    with closing(db.connect()) as conn:
        return db.idle_vehicles(conn, hours)


@mcp.tool(annotations=READ_ONLY)
def find_speed_violations(
    min_speed_kmh: Annotated[float, Field(gt=0, description="Report samples faster than this")] = 130,
    since_hours: SinceHours = 24 * 7,
    limit: Limit = 50,
) -> list[SpeedViolation]:
    """Telemetry samples above a speed threshold (fleet policy limit is 130 km/h), sorted
    FASTEST first, with the vehicle and the driver of that trip.  Use this for "who drove
    fastest / highest speed" questions (find_anomalies is sorted by time, not speed)."""
    with closing(db.connect()) as conn:
        return db.speed_violations(conn, min_speed_kmh, since_hours, limit)


@mcp.tool(annotations=READ_ONLY)
def find_anomalies(
    registration: Annotated[str | None, Field(description="Only this vehicle; omit for all")] = None,
    type: Annotated[AlertType | None, Field(description="Only this alert type; omit for all")] = None,
    since_hours: SinceHours = 24 * 7,
    limit: Limit = 50,
) -> list[Alert]:
    """Alerts raised by the telematics platform, sorted NEWEST first (not by speed or
    severity; for the highest speeds use find_speed_violations).  Types: gps_jump (position
    jumped and came back), speeding, fuel_drop (unexplained fuel loss), long_idle (engine
    running while standing).  Follow up with get_vehicle_history around the alert time."""
    with closing(db.connect()) as conn:
        return db.alerts(conn, registration, type, since_hours, limit)


# --- RAG tools: unstructured knowledge (kb/*.md) ------------------------------------
# The tools above answer "what happened" from the database; these answer "what does it
# mean / what do we do about it" from the knowledge base (see rag.py).

SearchText = Annotated[str, Field(min_length=3, max_length=500)]
SearchLimit = Annotated[int, Field(ge=1, le=20, description="Number of results")]


def _search(query: str, kind: str | None, limit: int) -> list[KnowledgeChunk]:
    """Shared body of the two search tools: turns setup problems into messages the model can relay."""
    if not rag.KB_PATH.exists():
        raise ToolError("The knowledge base is not built yet. Run `uv run fleet-ingest`.")
    try:
        with closing(rag.connect()) as conn:
            return rag.search(conn, query, kind, limit)
    except httpx.HTTPError as e:  # Ollama down, model not pulled, timeout...
        raise ToolError(
            f"The embedding service (Ollama at {rag.OLLAMA_URL}, model {rag.EMBED_MODEL}) failed: {e}. "
            "Is `ollama serve` running?"
        ) from e
    except sqlite3.OperationalError as e:  # e.g. "no such table: chunks_fts" in a pre-v4 index
        raise ToolError(f"The knowledge base index is outdated ({e}). Run `uv run fleet-ingest`.") from e


@mcp.tool(annotations=READ_ONLY)
def search_fleet_knowledge(
    query: Annotated[SearchText, Field(description="A question or topic in natural language")],
    limit: SearchLimit = 5,
) -> list[KnowledgeChunk]:
    """Search the fleet's guides and policies: alert type definitions, vehicle statuses, GPS
    troubleshooting and accuracy, fuel anomaly signatures, driver coaching, safety policy (speed
    limits, driving hours), maintenance.  Use it to explain what data means and what to do next."""
    return _search(query, "guide", limit)


@mcp.tool(annotations=READ_ONLY)
def search_similar_incidents(
    description: Annotated[
        SearchText, Field(description="What happened, e.g. 'position jumped 15 km and returned 30 s later'")
    ],
    limit: SearchLimit = 5,
) -> list[KnowledgeChunk]:
    """Find past incident reports similar to a described situation.  Each report gives the vehicle,
    date, what was observed, the investigation, the root cause and the resolution, so it shows how
    a similar case was explained and handled before.  Similar symptoms can have different root
    causes: compare the details with the current data."""
    return _search(description, "incident", limit)


# --- HTTP deployment: health checks and authentication (v6) -----------------------------


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> PlainTextResponse:
    """Liveness: the process is up and answering HTTP.  Kubernetes restarts the pod if not.

    Deliberately checks nothing else: a missing database is not fixed by a restart.
    """
    return PlainTextResponse("ok")


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(request: Request) -> JSONResponse:
    """Readiness: the data the tools need is in place.  Kubernetes only sends traffic if so.

    The knowledge base needs Ollama at query time too, but an Ollama outage only breaks the
    two search tools (with a clear ToolError), so it doesn't make the whole server unready.
    """
    checks = {"fleet_db": db.DB_PATH.exists(), "kb_db": rag.KB_PATH.exists()}
    if checks["fleet_db"]:
        try:
            with closing(db.connect()) as conn:
                conn.execute("SELECT 1 FROM vehicles LIMIT 1")
        except sqlite3.Error:
            checks["fleet_db"] = False
    return JSONResponse(checks, status_code=200 if all(checks.values()) else 503)


# Paths reachable without a token: Kubernetes probes don't carry credentials.
PUBLIC_PATHS = {"/healthz", "/readyz"}


def require_token(app, token: str):
    """Wrap an ASGI app so every request except PUBLIC_PATHS needs `Authorization: Bearer <token>`.

    ASGI is the interface between the web server (uvicorn) and the app: every request
    arrives as a call app(scope, receive, send).  Wrapping it lets us reject a request
    before the MCP app ever sees it.  Non-HTTP events (the "lifespan" startup/shutdown
    messages) pass through untouched.

    ponytail: one shared static token (fine for one team / one client); for per-user
    access switch to the SDK's OAuth support (MCPServer(auth=..., token_verifier=...)).
    """
    expected = f"Bearer {token}".encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and scope["path"] not in PUBLIC_PATHS:
            sent = dict(scope["headers"]).get(b"authorization", b"")
            # compare_digest takes the same time whether the first or the last byte differs,
            # so the token can't be guessed byte by byte from response times.
            if not hmac.compare_digest(sent, expected):
                response = PlainTextResponse(
                    "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


# Hosts for which the MCP SDK automatically enables DNS-rebinding protection
# (it checks the Host/Origin headers, so a malicious web page in your browser
# cannot talk to this local server).
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def main() -> None:
    """Entry point of `fleet-mcp`.  The transport is picked by environment variables:

    FLEET_TRANSPORT  stdio (default) | http
    FLEET_HOST       127.0.0.1 (default), http only
    FLEET_PORT       8000 (default), http only
    FLEET_API_TOKEN  http only: require `Authorization: Bearer <token>`.  Mandatory
                     when FLEET_HOST is not a localhost address (e.g. 0.0.0.0 in a container).

    Same tools either way; only the way messages travel changes.
    """
    telemetry.setup("fleet-mcp")  # only if OTEL_EXPORTER_OTLP_ENDPOINT is set
    # Every error below goes through sys.exit(msg), which prints to stderr:
    # with stdio, stdout belongs to the MCP protocol and must stay clean.
    if not db.DB_PATH.exists():
        sys.exit(f"Database not found at {db.DB_PATH}. Run `uv run fleet-seed` first.")

    transport = os.environ.get("FLEET_TRANSPORT", "stdio")
    if transport == "stdio":
        # The client (Claude Code, see .mcp.json) starts us and talks over stdin/stdout.
        mcp.run()
    elif transport == "http":
        serve_http()
    else:
        sys.exit(f"FLEET_TRANSPORT must be 'stdio' or 'http', got {transport!r}.")


def serve_http() -> None:
    """A long-running server at http://<host>:<port>/mcp, plus /healthz and /readyz."""
    host = os.environ.get("FLEET_HOST", "127.0.0.1")
    port = os.environ.get("FLEET_PORT", "8000")
    token = os.environ.get("FLEET_API_TOKEN")
    if not port.isdigit():
        sys.exit(f"FLEET_PORT must be a number, got {port!r}.")
    if host not in LOCAL_HOSTS and not token:
        sys.exit(f"FLEET_HOST={host!r} would expose the server without authentication. Set FLEET_API_TOKEN.")
    if token is not None and len(token) < 32:
        sys.exit("FLEET_API_TOKEN is too short (min. 32 characters). Generate one: openssl rand -hex 32")

    # Instead of mcp.run("streamable-http"), build the app ourselves so we can wrap it.
    # `host` is passed so the SDK still enables DNS-rebinding protection for localhost.
    # (For 0.0.0.0 it doesn't; the token covers that: a browser page can't attach it.)
    app = mcp.streamable_http_app(host=host)
    if token:
        app = require_token(app, token)
    uvicorn.run(app, host=host, port=int(port), log_level="info")
