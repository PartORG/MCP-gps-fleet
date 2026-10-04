"""Database access: the connection and every SQL query the MCP tools use.

Design notes
------------
* Plain stdlib `sqlite3`, no ORM: the server only runs a handful of read
  queries, and the SQL is easier to read than ORM calls would be.
* The MCP server opens the database READ-ONLY (`mode=ro`).  Even if a bug or a
  prompt-injected tool call tried to write, SQLite itself would refuse.
* One short-lived connection per tool call.  The MCP SDK runs sync tools in
  worker threads and a sqlite3 connection must stay in the thread that made it;
  opening a local SQLite file takes microseconds, so pooling would buy nothing.
* Each query function takes the connection as its first argument, so tests can
  point it at a temporary database.
* All user-facing values (registration, limits, dates) are passed as SQL
  parameters (`?` / `:name`), never formatted into the SQL string.
"""

import math
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fleet_mcp.models import (
    Alert,
    FleetStatistics,
    IdleVehicle,
    SpeedViolation,
    TelemetryPoint,
    VehicleStatus,
)

# <project root>/data/fleet.db unless overridden with the FLEET_DB env var.
# parents[2]: db.py -> fleet_mcp/ -> src/ -> project root.
DB_PATH = Path(os.environ.get("FLEET_DB", Path(__file__).resolve().parents[2] / "data" / "fleet.db"))

# German cities used both by the seed (as trip endpoints) and by the tools
# (to turn raw coordinates into a readable "near Essen").
CITIES: dict[str, tuple[float, float]] = {
    "Berlin": (52.520, 13.405),
    "Hamburg": (53.551, 9.994),
    "Munich": (48.137, 11.576),
    "Cologne": (50.938, 6.960),
    "Frankfurt": (50.110, 8.682),
    "Stuttgart": (48.776, 9.183),
    "Düsseldorf": (51.227, 6.773),
    "Essen": (51.456, 7.012),
    "Dortmund": (51.514, 7.468),
    "Leipzig": (51.340, 12.375),
    "Dresden": (51.050, 13.738),
    "Hanover": (52.376, 9.732),
    "Nuremberg": (49.452, 11.077),
    "Bremen": (53.079, 8.802),
    "Kassel": (51.312, 9.480),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two GPS points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def nearest_city(lat: float, lon: float) -> str:
    # ponytail: linear scan over 15 cities; use a real reverse geocoder if positions must be precise.
    return min(CITIES, key=lambda c: haversine_km(lat, lon, *CITIES[c]))


def to_sql_ts(dt: datetime) -> str:
    """datetime -> the UTC text format stored in the DB.  Naive datetimes are taken as UTC."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def connect(path: Path | None = None, *, readonly: bool = True) -> sqlite3.Connection:
    """Open the fleet DB.  Read-only by default; only the seed script writes.

    `path` defaults to DB_PATH, looked up at call time so tests can patch it.
    """
    path = path or DB_PATH
    if readonly:
        # URI form is the only way to ask sqlite3 for read-only mode.
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["speed_kmh"]
    return conn


# --- queries ---------------------------------------------------------------------


def vehicle_status(conn: sqlite3.Connection, registration: str) -> VehicleStatus | None:
    """One vehicle joined with its driver and last position; None if unknown."""
    row = conn.execute(
        """
        SELECT v.registration, v.brand, v.model, v.year, v.status, v.fuel_level, v.odometer_km,
               d.name AS driver, p.ts AS last_seen, p.lat, p.lon, p.speed_kmh
        FROM vehicles v
        LEFT JOIN drivers d       ON d.id = v.driver_id
        LEFT JOIN last_position p ON p.registration = v.registration
        WHERE v.registration = ?
        """,
        (registration.strip().upper(),),
    ).fetchone()
    if row is None:
        return None
    city = nearest_city(row["lat"], row["lon"]) if row["lat"] is not None else None
    return VehicleStatus(**row, nearest_city=city)


def vehicle_history(
    conn: sqlite3.Connection,
    registration: str,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[TelemetryPoint]:
    """Telemetry of one vehicle in [since, until], oldest first.

    If the window holds more than `limit` samples, the most recent ones are kept
    (that is what "what happened lately?" questions care about).
    """
    rows = conn.execute(
        """
        SELECT ts, lat, lon, speed_kmh, heading, ignition, fuel_level
        FROM telemetry
        WHERE registration = ? AND ts >= ? AND ts <= ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (
            registration.strip().upper(),
            # Missing bounds become strings that sort before/after any timestamp.
            to_sql_ts(since) if since else "0000",
            to_sql_ts(until) if until else "9999",
            limit,
        ),
    ).fetchall()
    return [_point(r) for r in reversed(rows)]


def fleet_statistics(conn: sqlite3.Connection) -> FleetStatistics:
    """Fleet-wide numbers: vehicles per status, last-24h activity, recent alerts."""
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM vehicles GROUP BY status").fetchall())
    trips, km = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(distance_km), 0) FROM trips WHERE end_time >= datetime('now', '-1 day')"
    ).fetchone()
    alerts = dict(
        conn.execute(
            "SELECT type, COUNT(*) FROM alerts WHERE ts >= datetime('now', '-7 days') GROUP BY type"
        ).fetchall()
    )
    avg_fuel = conn.execute("SELECT AVG(fuel_level) FROM vehicles").fetchone()[0] or 0.0
    return FleetStatistics(
        vehicles_by_status=by_status,
        trips_last_24h=trips,
        km_driven_last_24h=round(km, 1),
        alerts_last_7d_by_type=alerts,
        average_fuel_level=round(avg_fuel, 1),
    )


def idle_vehicles(conn: sqlite3.Connection, hours: int) -> list[IdleVehicle]:
    """Vehicles that sent no telemetry for at least `hours` hours, longest-idle first.

    The trackers only report while the ignition is on, so "no telemetry"
    means "the vehicle has not been used".
    """
    rows = conn.execute(
        """
        SELECT v.registration, v.status, p.ts AS last_seen, p.lat, p.lon,
               (julianday('now') - julianday(p.ts)) * 24 AS hours_since_last_seen
        FROM vehicles v
        JOIN last_position p ON p.registration = v.registration
        WHERE p.ts < datetime('now', ?)
        ORDER BY p.ts
        """,
        (f"-{int(hours)} hours",),  # SQLite date modifier, e.g. '-4 hours'
    ).fetchall()
    return [
        IdleVehicle(
            registration=r["registration"],
            status=r["status"],
            last_seen=r["last_seen"],
            hours_since_last_seen=round(r["hours_since_last_seen"], 1),
            nearest_city=nearest_city(r["lat"], r["lon"]),
        )
        for r in rows
    ]


def speed_violations(
    conn: sqlite3.Connection, min_speed_kmh: float, since_hours: int, limit: int
) -> list[SpeedViolation]:
    """Telemetry samples faster than `min_speed_kmh`, fastest first.

    The driver comes from the trip that was running at that moment (a pool
    vehicle can be driven by different people), not from the vehicle record.
    """
    rows = conn.execute(
        """
        SELECT t.registration, t.ts, t.lat, t.lon, t.speed_kmh, t.heading, t.ignition,
               t.fuel_level, d.name AS driver
        FROM telemetry t
        LEFT JOIN trips tr ON tr.registration = t.registration
                          AND t.ts BETWEEN tr.start_time AND tr.end_time
        LEFT JOIN drivers d ON d.id = tr.driver_id
        WHERE t.speed_kmh > ? AND t.ts >= datetime('now', ?)
        ORDER BY t.speed_kmh DESC
        LIMIT ?
        """,
        (min_speed_kmh, f"-{int(since_hours)} hours", limit),
    ).fetchall()
    return [
        SpeedViolation(**_point(r).model_dump(), registration=r["registration"], driver=r["driver"])
        for r in rows
    ]


def alerts(
    conn: sqlite3.Connection,
    registration: str | None,
    alert_type: str | None,
    since_hours: int,
    limit: int,
) -> list[Alert]:
    """Alerts raised by the telematics platform, newest first, optionally filtered."""
    rows = conn.execute(
        """
        SELECT id, registration, ts, type, severity, details
        FROM alerts
        WHERE ts >= datetime('now', :since)
          AND (:reg  IS NULL OR registration = :reg)   -- NULL filter = "any"
          AND (:type IS NULL OR type = :type)
        ORDER BY ts DESC
        LIMIT :limit
        """,
        {
            "since": f"-{int(since_hours)} hours",
            "reg": registration.strip().upper() if registration else None,
            "type": alert_type,
            "limit": limit,
        },
    ).fetchall()
    return [Alert(**r) for r in rows]


def _point(r: sqlite3.Row) -> TelemetryPoint:
    """Row from the telemetry table -> TelemetryPoint (adds the readable city)."""
    return TelemetryPoint(
        ts=r["ts"],
        lat=r["lat"],
        lon=r["lon"],
        nearest_city=nearest_city(r["lat"], r["lon"]),
        speed_kmh=r["speed_kmh"],
        heading=r["heading"],
        ignition=bool(r["ignition"]),
        fuel_level=r["fuel_level"],
    )
