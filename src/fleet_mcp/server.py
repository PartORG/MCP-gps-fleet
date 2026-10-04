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

import os
import sys
from contextlib import closing
from datetime import datetime
from typing import Annotated

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from fleet_mcp import db, rag
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
        "Vehicles are identified by registration plates like 'FM-0231'. All times are UTC. "
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
Registration = Annotated[str, Field(description="Vehicle registration plate, e.g. 'FM-0231'")]
Limit = Annotated[int, Field(ge=1, le=500, description="Maximum number of rows to return")]
SinceHours = Annotated[int, Field(ge=1, le=24 * 30, description="Look back this many hours")]


@mcp.tool(annotations=READ_ONLY)
def get_vehicle_status(registration: Registration) -> VehicleStatus:
    """Current state of one vehicle: brand/model, status, driver, fuel level, odometer and
    its last known position (time, coordinates, nearest city, speed)."""
    with closing(db.connect()) as conn:
        status = db.vehicle_status(conn, registration)
    if status is None:
        raise ToolError(f"Unknown vehicle {registration!r}. Registrations look like 'FM-0231'.")
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
            raise ToolError(f"Unknown vehicle {registration!r}. Registrations look like 'FM-0231'.")
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
    """Telemetry samples above a speed threshold (fleet policy limit is 130 km/h), fastest
    first, with the vehicle and the driver of that trip."""
    with closing(db.connect()) as conn:
        return db.speed_violations(conn, min_speed_kmh, since_hours, limit)


@mcp.tool(annotations=READ_ONLY)
def find_anomalies(
    registration: Annotated[str | None, Field(description="Only this vehicle; omit for all")] = None,
    type: Annotated[AlertType | None, Field(description="Only this alert type; omit for all")] = None,
    since_hours: SinceHours = 24 * 7,
    limit: Limit = 50,
) -> list[Alert]:
    """Alerts raised by the telematics platform, newest first.  Types: gps_jump (position
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


# Hosts for which the MCP SDK automatically enables DNS-rebinding protection
# (it checks the Host/Origin headers, so a malicious web page in your browser
# cannot talk to this local server).
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def main() -> None:
    """Entry point of `fleet-mcp`.  The transport is picked by environment variables:

    FLEET_TRANSPORT  stdio (default) | http
    FLEET_HOST       127.0.0.1 (default), http only
    FLEET_PORT       8000 (default), http only

    Same tools either way; only the way messages travel changes.
    """
    # Every error below goes through sys.exit(msg), which prints to stderr:
    # with stdio, stdout belongs to the MCP protocol and must stay clean.
    if not db.DB_PATH.exists():
        sys.exit(f"Database not found at {db.DB_PATH}. Run `uv run fleet-seed` first.")

    transport = os.environ.get("FLEET_TRANSPORT", "stdio")
    if transport == "stdio":
        # The client (Claude Code, see .mcp.json) starts us and talks over stdin/stdout.
        mcp.run()
    elif transport == "http":
        # A long-running server at http://<host>:<port>/mcp that clients connect to.
        host = os.environ.get("FLEET_HOST", "127.0.0.1")
        if host not in LOCAL_HOSTS:
            # ponytail: localhost only; v6 adds auth, then non-local hosts become allowed.
            sys.exit(
                f"FLEET_HOST={host!r} would expose the server without authentication. Use one of {LOCAL_HOSTS}."
            )
        mcp.run("streamable-http", host=host, port=int(os.environ.get("FLEET_PORT", "8000")))
    else:
        sys.exit(f"FLEET_TRANSPORT must be 'stdio' or 'http', got {transport!r}.")
