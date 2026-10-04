"""Checks for the offline chat client's wiring: agent -> MCP (stdio and HTTP) -> fleet-mcp.

The real LLM is replaced by Pydantic AI's TestModel, which deterministically calls the
tools we name.  So these tests need no Ollama and run in seconds; they check everything
around the model (config, transports, env forwarding).  How well qwen3 answers is
checked by hand with `uv run fleet-chat` (and measured properly by the v4 eval).
"""

import asyncio
from contextlib import closing

import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.test import TestModel
from test_db import http_server

from fleet_mcp import chat, db, seed


@pytest.fixture(scope="module")
def other_db(tmp_path_factory):
    """A fleet seeded with a different random seed than data/fleet.db, so its statistics
    differ: if the tool returns these numbers, FLEET_DB really reached the server."""
    path = tmp_path_factory.mktemp("chat") / "fleet.db"
    seed.seed(path, rng_seed=7)
    with closing(db.connect(path)) as conn:
        return path, db.fleet_statistics(conn).vehicles_by_status


def fleet_statistics_via_agent() -> dict:
    """Run the real agent with a fake model that just calls get_fleet_statistics."""
    agent = chat.build_agent()

    async def run():
        with agent.override(model=TestModel(call_tools=["get_fleet_statistics"])):
            return await agent.run("fleet status?")

    result = asyncio.run(run())
    [returned] = [p for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
    return returned.content["vehicles_by_status"]


def test_stdio_agent_forwards_settings_to_server(other_db, monkeypatch):
    path, expected = other_db
    monkeypatch.delenv("FLEET_MCP_URL", raising=False)
    monkeypatch.setenv("FLEET_DB", str(path))
    monkeypatch.setenv("FLEET_TRANSPORT", "http")  # must NOT leak into the stdio child
    assert fleet_statistics_via_agent() == expected


def test_http_agent(other_db, monkeypatch):
    path, expected = other_db
    with http_server(path) as url:
        monkeypatch.setenv("FLEET_MCP_URL", url)
        assert fleet_statistics_via_agent() == expected
