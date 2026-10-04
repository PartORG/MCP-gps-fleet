"""Pydantic models returned by the MCP tools.

Why models instead of plain dicts?  The MCP SDK reads a tool's return type
annotation and publishes it as the tool's *output schema*.  The client (Claude,
or the offline Pydantic AI agent in v3) therefore knows the exact shape and
meaning of every field before it calls the tool.  `Field(description=...)` text
ends up in that schema, so it is written for the LLM as much as for humans.

Timestamps are kept as the UTC strings stored in SQLite ('YYYY-MM-DD HH:MM:SS');
converting them to datetime and back would add code without adding information.
"""

from typing import Literal

from pydantic import BaseModel, Field

AlertType = Literal["gps_jump", "speeding", "fuel_drop", "long_idle"]
Status = Literal["active", "idle", "maintenance", "offline"]


class VehicleStatus(BaseModel):
    registration: str
    brand: str
    model: str
    year: int
    status: str = Field(description="active | idle | maintenance | offline")
    driver: str | None = Field(description="Assigned driver, null for pool vehicles")
    fuel_level: float = Field(description="Last known fuel level in percent")
    odometer_km: float
    last_seen: str | None = Field(description="UTC time of the last telemetry sample")
    lat: float | None
    lon: float | None
    nearest_city: str | None
    speed_kmh: float | None = Field(description="Speed in the last telemetry sample")


class TelemetryPoint(BaseModel):
    ts: str = Field(description="UTC time of the sample")
    lat: float
    lon: float
    nearest_city: str = Field(description="Closest known city, to make positions readable")
    speed_kmh: float
    heading: float = Field(description="Degrees, 0 = north")
    ignition: bool
    fuel_level: float = Field(description="Percent")


class SpeedViolation(TelemetryPoint):
    registration: str
    driver: str | None = Field(description="Driver of the trip during which it happened")


class IdleVehicle(BaseModel):
    registration: str
    status: str
    last_seen: str = Field(description="UTC time of the last telemetry sample")
    hours_since_last_seen: float
    nearest_city: str


class Alert(BaseModel):
    id: int
    registration: str
    ts: str = Field(description="UTC time of the anomalous telemetry sample")
    type: AlertType
    severity: str = Field(description="low | medium | high")
    details: str


class FleetStatistics(BaseModel):
    vehicles_by_status: dict[str, int]
    trips_last_24h: int
    km_driven_last_24h: float
    alerts_last_7d_by_type: dict[str, int]
    average_fuel_level: float


class KnowledgeChunk(BaseModel):
    source: str = Field(description="Markdown file in the knowledge base, e.g. 'incidents/incident_1842.md'")
    kind: Literal["guide", "incident"]
    title: str
    section: str = Field(description="Section heading inside the document ('' = whole document)")
    text: str
    score: float = Field(
        description="Reranker relevance 0..1: close to 1 = answers the query, close to 0 = not relevant "
        "(the search always returns results, so low scores mean the knowledge base has nothing on it)"
    )
