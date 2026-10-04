"""Generate a synthetic fleet: drivers, vehicles, trips, telemetry and alerts.

Run:  uv run fleet-seed          (writes data/fleet.db, or $FLEET_DB)

How the fake world works
------------------------
* 50 vehicles drive between 15 German cities during the last 7 days, ending "now".
  Each trip is a straight line between two cities, sampled 12 times
  (~10k telemetry rows in total).  A vehicle starts its next trip where the
  previous one ended, so the history of every vehicle is continuous.
* Vehicle status drives *when* it stopped driving:
    active      -> drives until now (the last trip may still be in progress)
    idle        -> parked for the last 5-24 hours
    maintenance / offline -> parked for the last 2-4 days
  That is what makes `find_idle_vehicles` return something meaningful.
* Anomalies are INJECTED on purpose and an alert is written for each one, so
  every alert points at real, inspectable telemetry:
    gps_jump  -> an extra sample 30 s later, 10-30 km off the route, then back
    speeding  -> one sample at 135-175 km/h
    fuel_drop -> 15-30 % of fuel disappears between two samples (theft/leak)
    long_idle -> engine running at 0 km/h for 45-120 min after a trip
* Everything comes from one seeded `random.Random`, so the same data (relative
  to "now") is produced on every run.  Re-run the seed when the data gets old:
  the tools query relative to the current time ("last 24 hours").
"""

import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fleet_mcp.db import CITIES, DB_PATH, connect, haversine_km, to_sql_ts

N_DRIVERS = 40
N_VEHICLES = 50
DAYS = 7
SAMPLES_PER_TRIP = 12
ROAD_FACTOR = 1.25  # roads are ~25 % longer than the straight line
FUEL_PCT_PER_KM = 0.08  # full tank lasts ~1250 km
SPEED_LIMIT = 130  # fleet policy limit, km/h

FIRST_NAMES = [
    "Anna",
    "Ben",
    "Clara",
    "David",
    "Elena",
    "Felix",
    "Greta",
    "Hannes",
    "Ida",
    "Jonas",
    "Katrin",
    "Lukas",
    "Mia",
    "Niklas",
    "Olga",
    "Paul",
    "Rosa",
    "Stefan",
    "Tina",
    "Yusuf",
]
LAST_NAMES = [
    "Müller",
    "Schmidt",
    "Schneider",
    "Fischer",
    "Weber",
    "Meyer",
    "Wagner",
    "Becker",
    "Hoffmann",
    "Schulz",
    "Koch",
    "Richter",
    "Klein",
    "Wolf",
    "Neumann",
    "Kaya",
]
MODELS = {
    "Mercedes-Benz": ["Sprinter", "Vito", "Actros"],
    "Volkswagen": ["Crafter", "Transporter"],
    "MAN": ["TGE", "TGX"],
    "Ford": ["Transit"],
    "Iveco": ["Daily"],
}


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate compass heading from point 1 to point 2 (fine for short distances)."""
    return math.degrees(math.atan2((lon2 - lon1) * math.cos(math.radians(lat1)), lat2 - lat1)) % 360


def offset(lat: float, lon: float, km: float, rng: random.Random) -> tuple[float, float]:
    """A point `km` away from (lat, lon) in a random direction (1° lat ≈ 111 km)."""
    angle = rng.uniform(0, 2 * math.pi)
    return (
        lat + km * math.cos(angle) / 111,
        lon + km * math.sin(angle) / (111 * math.cos(math.radians(lat))),
    )


def generate(rng: random.Random, now: datetime) -> dict[str, list[tuple]]:
    """Build every table's rows in memory.  Returns {table name: list of row tuples}."""
    drivers = [
        (i, f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}", f"DE-{70000 + i}")
        for i in range(1, N_DRIVERS + 1)
    ]
    # The first 40 vehicles each get their own driver; the remaining 10 are pool vehicles.
    driver_ids: list[int | None] = [d[0] for d in drivers]
    rng.shuffle(driver_ids)
    driver_ids += [None] * (N_VEHICLES - N_DRIVERS)

    vehicles, trips, telemetry, alerts = [], [], [], []
    plates = sorted(rng.sample(range(1, 1000), N_VEHICLES))

    for plate, driver_id in zip(plates, driver_ids):
        reg = f"FM-{plate:04d}"
        brand = rng.choice(list(MODELS))
        status = rng.choices(["active", "idle", "maintenance", "offline"], weights=[34, 8, 5, 3])[0]
        stop_at = {
            "active": now,
            "idle": now - timedelta(hours=rng.uniform(5, 24)),
        }.get(status, now - timedelta(days=rng.uniform(2, 4)))

        city = rng.choice(list(CITIES))
        t = now - timedelta(days=DAYS) + timedelta(hours=rng.uniform(0, 6))
        fuel = rng.uniform(50, 100)
        odometer = rng.uniform(20_000, 300_000)
        last = None  # last telemetry row we kept, for the vehicle's final fuel level

        while t < stop_at:
            dest = rng.choice([c for c in CITIES if c != city])
            (lat1, lon1), (lat2, lon2) = CITIES[city], CITIES[dest]
            dist = haversine_km(lat1, lon1, lat2, lon2) * ROAD_FACTOR
            duration = timedelta(hours=dist / rng.uniform(65, 95))
            heading = bearing(lat1, lon1, lat2, lon2)
            # Pool vehicles get a random driver per trip.
            trip_driver = driver_id or rng.choice(drivers)[0]
            # Refuel before the trip if the tank would get too low (leave room for a fuel_drop).
            if fuel - dist * FUEL_PCT_PER_KM < 35:
                fuel = 100.0

            # Regular samples along the straight line between the two cities.
            points = []
            for i in range(SAMPLES_PER_TRIP):
                f = i / (SAMPLES_PER_TRIP - 1)  # 0.0 at start .. 1.0 at destination
                moving = 0 < i < SAMPLES_PER_TRIP - 1
                points.append(
                    [
                        t + duration * f,
                        lat1 + (lat2 - lat1) * f + rng.uniform(-0.01, 0.01),
                        lon1 + (lon2 - lon1) * f + rng.uniform(-0.01, 0.01),
                        rng.uniform(70, 110) if moving else 0.0,
                        heading,
                        1,
                        fuel - dist * f * FUEL_PCT_PER_KM,
                    ]
                )
            # point = [ts, lat, lon, speed, heading, ignition, fuel] (mutable list on purpose)

            trip_alerts = []  # (ts, type, severity, details)
            mid = rng.randrange(2, SAMPLES_PER_TRIP - 2)  # a sample in the middle of the trip

            if rng.random() < 0.10:  # --- speeding
                speed = rng.uniform(135, 175)
                points[mid][3] = speed
                trip_alerts.append(
                    (
                        points[mid][0],
                        "speeding",
                        "high" if speed > 160 else "medium",
                        f"Speed {speed:.0f} km/h exceeds the fleet limit of {SPEED_LIMIT} km/h.",
                    )
                )

            if rng.random() < 0.04:  # --- fuel_drop: every later sample loses `drop` %
                drop = rng.uniform(15, 30)
                for p in points[mid:]:
                    p[6] -= drop
                trip_alerts.append(
                    (
                        points[mid][0],
                        "fuel_drop",
                        "high",
                        f"Fuel level fell {drop:.0f} % more than expected between two samples.",
                    )
                )

            if rng.random() < 0.08:  # --- gps_jump: insert a bogus fix and a return fix
                base = points[mid]
                km = rng.uniform(10, 30)
                jlat, jlon = offset(base[1], base[2], km, rng)
                jump = [base[0] + timedelta(seconds=30), jlat, jlon, base[3], base[4], 1, base[6]]
                back = [base[0] + timedelta(seconds=60), base[1] + 0.003, base[2] + 0.003, *base[3:]]
                points[mid + 1 : mid + 1] = [jump, back]
                trip_alerts.append(
                    (
                        jump[0],
                        "gps_jump",
                        "medium" if km > 20 else "low",
                        f"Position jumped {km:.1f} km within 30 s and returned 30 s later.",
                    )
                )

            end = t + duration
            next_start = end + timedelta(hours=rng.uniform(1, 8))
            if rng.random() < 0.05:  # --- long_idle: engine on, standing still after arrival
                minutes = rng.randrange(45, 121, 15)
                arrival = points[-1]
                for m in range(15, minutes + 1, 15):
                    points.append(
                        [end + timedelta(minutes=m), arrival[1], arrival[2], 0.0, heading, 1, arrival[6]]
                    )
                trip_alerts.append(
                    (
                        end + timedelta(minutes=minutes),
                        "long_idle",
                        "low",
                        f"Engine idling for {minutes} min at {dest} without moving.",
                    )
                )
                next_start = max(next_start, end + timedelta(minutes=minutes + 15))

            # Keep only what already "happened": samples/alerts after stop_at are cut.
            # A trip still running at stop_at keeps its first samples but gets no trip row.
            for p in points:
                if p[0] <= stop_at:
                    last = (
                        reg,
                        to_sql_ts(p[0]),
                        round(p[1], 5),
                        round(p[2], 5),
                        round(p[3], 1),
                        round(p[4], 1),
                        p[5],
                        round(max(p[6], 0), 1),
                    )
                    telemetry.append(last)
            alerts += [(reg, to_sql_ts(ts), *rest) for ts, *rest in trip_alerts if ts <= stop_at]
            if end <= stop_at:
                trips.append((reg, trip_driver, to_sql_ts(t), to_sql_ts(end), city, dest, round(dist, 1)))
                odometer += dist

            fuel = points[-1][6]
            city, t = dest, next_start

        vehicles.append(
            (
                reg,
                brand,
                rng.choice(MODELS[brand]),
                rng.randint(2015, 2026),
                driver_id,
                status,
                last[-1] if last else round(fuel, 1),
                round(odometer, 1),
            )
        )

    return {
        "drivers": drivers,
        "vehicles": vehicles,
        "trips": trips,
        "telemetry": telemetry,
        "alerts": alerts,
    }


INSERTS = {
    "drivers": "INSERT INTO drivers (id, name, license_no) VALUES (?, ?, ?)",
    "vehicles": "INSERT INTO vehicles (registration, brand, model, year, driver_id, status, fuel_level, "
    "odometer_km) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    "trips": "INSERT INTO trips (registration, driver_id, start_time, end_time, start_city, end_city, "
    "distance_km) VALUES (?, ?, ?, ?, ?, ?, ?)",
    "telemetry": "INSERT INTO telemetry (registration, ts, lat, lon, speed_kmh, heading, ignition, "
    "fuel_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    "alerts": "INSERT INTO alerts (registration, ts, type, severity, details) VALUES (?, ?, ?, ?, ?)",
}


def seed(path: Path, rng_seed: int = 42) -> dict[str, int]:
    """(Re)create the database at `path`.  Returns row counts per table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # always start from an empty file
    # "Now" rounded down to the minute so the data does not depend on seconds.
    now = datetime.now(UTC).replace(second=0, microsecond=0, tzinfo=None)
    tables = generate(random.Random(rng_seed), now)

    conn = connect(path, readonly=False)
    with conn:  # one transaction: commit on success, roll back on error
        conn.executescript((Path(__file__).parent / "schema.sql").read_text())
        for table, sql in INSERTS.items():  # dict order = FK-safe insert order
            conn.executemany(sql, tables[table])
    conn.close()
    return {table: len(rows) for table, rows in tables.items()}


def main() -> None:
    counts = seed(DB_PATH)
    print(f"Seeded {DB_PATH}")
    for table, n in counts.items():
        print(f"  {table:<10} {n:>6}")
