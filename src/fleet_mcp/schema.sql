-- Fleet database schema (SQLite).
--
-- Conventions:
--   * A vehicle is identified by its registration plate, e.g. 'FM-0231'.
--     Using the plate as the primary key means every table speaks the same
--     language as the user ("what happened to FM-0231?") with no id lookups.
--   * All timestamps are UTC text in SQLite's own format 'YYYY-MM-DD HH:MM:SS'.
--     That format sorts correctly as a string, so `ts >= datetime('now', '-1 day')`
--     works directly in SQL without any date parsing.
--   * CHECK constraints keep the seed honest: the DB rejects an unknown status
--     or alert type instead of letting it leak into tool output.

CREATE TABLE drivers (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    license_no TEXT NOT NULL UNIQUE
);

CREATE TABLE vehicles (
    registration TEXT PRIMARY KEY,
    brand        TEXT NOT NULL,
    model        TEXT NOT NULL,
    year         INTEGER NOT NULL,
    driver_id    INTEGER REFERENCES drivers(id),   -- NULL = no assigned driver
    status       TEXT NOT NULL CHECK (status IN ('active', 'idle', 'maintenance', 'offline')),
    fuel_level   REAL NOT NULL,                    -- percent, 0..100 (last known)
    odometer_km  REAL NOT NULL
);

-- One row per journey between two cities.
CREATE TABLE trips (
    id           INTEGER PRIMARY KEY,
    registration TEXT NOT NULL REFERENCES vehicles(registration),
    driver_id    INTEGER REFERENCES drivers(id),
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    start_city   TEXT NOT NULL,
    end_city     TEXT NOT NULL,
    distance_km  REAL NOT NULL
);

-- GPS/CAN samples sent by the (imaginary) tracker in each vehicle.
CREATE TABLE telemetry (
    id           INTEGER PRIMARY KEY,
    registration TEXT NOT NULL REFERENCES vehicles(registration),
    ts           TEXT NOT NULL,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    speed_kmh    REAL NOT NULL,
    heading      REAL NOT NULL,                    -- degrees, 0 = north
    ignition     INTEGER NOT NULL CHECK (ignition IN (0, 1)),
    fuel_level   REAL NOT NULL
);
-- Almost every query is "this vehicle, in this time range" -> one composite index.
CREATE INDEX telemetry_vehicle_ts ON telemetry (registration, ts);
CREATE INDEX telemetry_speed ON telemetry (speed_kmh);

-- The latest telemetry sample of every vehicle ("where is it now?").
-- SQLite-specific trick: with a single MAX() aggregate, the other "bare" columns
-- are taken from the row that holds the maximum, so this is one indexed pass
-- instead of a correlated subquery.
CREATE VIEW last_position AS
SELECT registration, MAX(ts) AS ts, lat, lon, speed_kmh, ignition, fuel_level
FROM telemetry
GROUP BY registration;

-- Anomalies flagged by the (imaginary) telematics platform.
-- In this demo the seed script injects the anomaly into telemetry AND writes
-- the matching alert, so every alert points at real, inspectable data.
CREATE TABLE alerts (
    id           INTEGER PRIMARY KEY,
    registration TEXT NOT NULL REFERENCES vehicles(registration),
    ts           TEXT NOT NULL,
    type         TEXT NOT NULL CHECK (type IN ('gps_jump', 'speeding', 'fuel_drop', 'long_idle')),
    severity     TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    details      TEXT NOT NULL
);
CREATE INDEX alerts_ts ON alerts (ts);
