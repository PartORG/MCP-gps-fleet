"""Checks for the seed, the queries and the MCP tools.

One temporary database is seeded per test session (takes well under a second)
and every test reads from it.  Run:  uv run pytest
"""

import asyncio
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from fleet_mcp import db, seed
from fleet_mcp.server import mcp


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "fleet.db"
    seed.seed(path)
    return path


@pytest.fixture
def conn(db_path):
    with closing(db.connect(db_path)) as c:
        yield c


def test_seed_sizes(conn):
    count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert count("vehicles") == 50
    assert 8_000 < count("telemetry") < 13_000
    types = {r[0] for r in conn.execute("SELECT DISTINCT type FROM alerts")}
    assert types == {"gps_jump", "speeding", "fuel_drop", "long_idle"}


def test_gps_jump_alerts_point_at_real_jumps(conn):
    """Every gps_jump alert must match a sample that is far from the sample 30 s before it."""
    jumps = conn.execute("SELECT registration, ts FROM alerts WHERE type = 'gps_jump'").fetchall()
    assert jumps
    for reg, ts in jumps:
        before, jump = conn.execute(
            "SELECT lat, lon FROM telemetry WHERE registration = ? AND ts <= ? ORDER BY ts DESC LIMIT 2",
            (reg, ts),
        ).fetchall()[::-1]
        assert db.haversine_km(*before, *jump) >= 9.5  # injected 10-30 km (+ GPS jitter)


def test_speeding_alerts_match_telemetry(conn):
    rows = conn.execute(
        "SELECT t.speed_kmh FROM alerts a JOIN telemetry t ON t.registration = a.registration AND t.ts = a.ts "
        "WHERE a.type = 'speeding'"
    ).fetchall()
    assert rows and all(r[0] > 130 for r in rows)


def test_server_connection_is_read_only(conn):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM alerts")


def test_offline_vehicles_are_idle(conn):
    """offline/maintenance vehicles stopped driving 2-4 days ago, so they must show up as idle."""
    idle = {v.registration for v in db.idle_vehicles(conn, hours=24)}
    parked = {
        r[0]
        for r in conn.execute("SELECT registration FROM vehicles WHERE status IN ('offline', 'maintenance')")
    }
    assert parked <= idle


# --- through the MCP server, the way a client calls it ----------------------------


@pytest.fixture
def server(db_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", db_path)  # tools call db.connect() without a path
    return mcp


def call(server, tool, **args):
    return asyncio.run(server.call_tool(tool, args))


def test_tools_are_registered(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {
        "get_vehicle_status",
        "list_vehicles",
        "get_vehicle_history",
        "get_fleet_statistics",
        "find_idle_vehicles",
        "find_speed_violations",
        "find_anomalies",
        "search_fleet_knowledge",
        "search_similar_incidents",
    }


def test_anomaly_then_history_flow(server):
    """The demo flow: find a GPS jump, then inspect the vehicle's history around it."""
    alert = call(server, "find_anomalies", type="gps_jump", limit=1).structured_content["result"][0]
    history = call(
        server, "get_vehicle_history", registration=alert["registration"].lower(), until=alert["ts"], limit=3
    ).structured_content["result"]
    assert history[-1]["ts"] == alert["ts"]


def test_list_vehicles_by_status(server, conn):
    """The question the v3 chat could not answer: which vehicles are offline, and where?"""
    offline = call(server, "list_vehicles", status="offline").structured_content["result"]
    expected = {r[0] for r in conn.execute("SELECT registration FROM vehicles WHERE status = 'offline'")}
    assert {v["registration"] for v in offline} == expected
    assert all(v["status"] == "offline" and v["nearest_city"] for v in offline)
    assert len(call(server, "list_vehicles").structured_content["result"]) == 50  # no filter = all


def test_unknown_vehicle_is_a_tool_error(server):
    with pytest.raises(ToolError, match="Unknown vehicle"):
        call(server, "get_vehicle_status", registration="XX-0000")


def test_limit_is_validated(server):
    with pytest.raises(ToolError):
        call(server, "find_anomalies", limit=10_000)


# --- transports: the real `fleet-mcp` entry point, started as a separate process ---

# The script uv generated from [project.scripts], next to the venv's python.
FLEET_MCP = str(Path(sys.executable).parent / "fleet-mcp")


def run_server(db_path, **env):
    """Start `fleet-mcp` with extra env vars, e.g. FLEET_TRANSPORT="http"."""
    env = {**os.environ, "FLEET_DB": str(db_path), **env}
    return subprocess.Popen([FLEET_MCP], env=env, stderr=subprocess.PIPE, text=True)


def port_open(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def http_server(db_path):
    """Run `fleet-mcp` over HTTP on a free port for the duration of a `with`; yields its URL."""
    with socket.socket() as s:  # ask the OS for a free port
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # `with Popen` closes the stderr pipe and waits for the process on exit.
    with run_server(db_path, FLEET_TRANSPORT="http", FLEET_PORT=str(port)) as proc:
        try:
            # Wait until the server accepts connections (uvicorn needs a moment to start).
            deadline = time.monotonic() + 10
            while not port_open(port):
                assert proc.poll() is None, proc.stderr.read()  # died instead of starting
                assert time.monotonic() < deadline, "HTTP server did not start"
                time.sleep(0.1)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:  # stop the server even if the test failed
            proc.terminate()


def test_stdio_transport(db_path):
    """Default transport: the client starts the server, exactly like Claude Code via .mcp.json."""
    params = StdioServerParameters(command=FLEET_MCP, env={**os.environ, "FLEET_DB": str(db_path)})

    async def talk():
        async with Client(params) as client:
            return len((await client.list_tools()).tools)

    assert asyncio.run(talk()) == 9


def test_http_transport(db_path):
    async def talk(url):
        async with Client(url) as client:
            tools = await client.list_tools()
            stats = await client.call_tool("get_fleet_statistics", {})
            return len(tools.tools), stats.structured_content

    with http_server(db_path) as url:
        n_tools, stats = asyncio.run(talk(url))
    assert n_tools == 9
    assert sum(stats["vehicles_by_status"].values()) == 50


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"FLEET_TRANSPORT": "http", "FLEET_HOST": "0.0.0.0"}, "without authentication"),
        ({"FLEET_TRANSPORT": "sse"}, "must be 'stdio' or 'http'"),
    ],
)
def test_bad_transport_settings_are_refused(db_path, env, message):
    proc = run_server(db_path, **env)
    _, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 1
    assert message in stderr
