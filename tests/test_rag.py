"""Checks for the RAG layer: chunking, retrieval quality and the two search tools.

Everything except the chunking and "not built" tests needs a running Ollama with
nomic-embed-text; without it those tests are SKIPPED (shown as 's'), not failed.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fleet_mcp import rag
from fleet_mcp.server import mcp


def ollama_running() -> bool:
    try:
        return httpx.get(rag.OLLAMA_URL, timeout=1).is_success
    except httpx.HTTPError:
        return False


requires_ollama = pytest.mark.skipif(not ollama_running(), reason="Ollama is not running")


def call(tool, **args):
    return asyncio.run(mcp.call_tool(tool, args))


def test_split_markdown():
    guide = "# Fuel Guide\nIntro text.\n\n## Theft\nSudden drop.\n\n## Leak\nSlow loss.\n"
    chunks = rag.split_markdown("fuel.md", guide)
    assert [(c.kind, c.section) for c in chunks] == [("guide", ""), ("guide", "Theft"), ("guide", "Leak")]
    assert chunks[2].text.startswith("# Fuel Guide\n## Leak")  # title kept for context

    incident = "# Incident #1: x\n\n## Observed\na\n\n## Resolution\nb\n"
    [chunk] = rag.split_markdown("incidents/incident_1.md", incident)  # one chunk per report
    assert chunk.kind == "incident" and "## Resolution" in chunk.text


def test_search_without_index_is_a_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "KB_PATH", tmp_path / "missing.db")
    with pytest.raises(ToolError, match="fleet-ingest"):
        call("search_fleet_knowledge", query="speed limit")


# --- with real embeddings -------------------------------------------------------------


@pytest.fixture(scope="session")
def kb_path(tmp_path_factory):
    """The real kb/ folder, embedded once per test session (~5 s)."""
    if not ollama_running():
        pytest.skip("Ollama is not running")
    path = tmp_path_factory.mktemp("kb") / "kb.db"
    rag.ingest(rag.KB_DIR, path)
    return path


@pytest.fixture
def kb(kb_path, monkeypatch):
    monkeypatch.setattr(rag, "KB_PATH", kb_path)  # the tools use rag.KB_PATH


# A tiny golden set: (question, file that must be among the top 3).
# v4 turns this into a measured eval (recall@k) to compare vector vs. hybrid search.
GOLDEN = [
    (
        "search_similar_incidents",
        "fuel level fell overnight at a truck stop, ignition off",
        "incident_1433.md",
    ),
    (
        "search_similar_incidents",
        "fuel level dropped and an hour later was back without refuelling",
        "incident_1519.md",
    ),
    (
        "search_similar_incidents",
        "position froze while the truck kept driving, always the same driver",
        "incident_1650.md",
    ),
    pytest.param(
        "search_similar_incidents",
        "many vehicles all over Germany had GPS jumps at the same time",
        "incident_1977.md",
        # Known miss of vector-only search: ranks 5th, all GPS incidents score within 0.05,
        # because the embedding captures "GPS jump" but not "many vehicles at once".
        # strict=True: when v4/v5 fix it, this XPASS fails the run -> remove the marker.
        marks=pytest.mark.xfail(strict=True, reason="vector-only search ranks it 5th; target for v4/v5"),
    ),
    (
        "search_similar_incidents",
        "engine running for an hour at deliveries to keep the cargo cold",
        "incident_1600.md",
    ),
    ("search_fleet_knowledge", "what is the motorway speed limit for trucks", "fleet_safety.md"),
    ("search_fleet_knowledge", "how can I tell fuel theft from a leak", "fuel_anomalies.md"),
    ("search_fleet_knowledge", "what does the maintenance status mean", "vehicle_statuses.md"),
    (
        "search_fleet_knowledge",
        "how long must a truck driver rest after 4.5 hours of driving",
        "driver_behavior.md",
    ),
]


@requires_ollama
@pytest.mark.parametrize(("tool", "question", "expected"), GOLDEN)
def test_retrieval_finds_expected_document(kb, tool, question, expected):
    arg = "description" if tool == "search_similar_incidents" else "query"
    results = call(tool, **{arg: question, "limit": 3}).structured_content["result"]
    assert expected in [Path(r["source"]).name for r in results]


@requires_ollama
def test_tools_only_return_their_kind(kb):
    incidents = call("search_similar_incidents", description="GPS jump", limit=10).structured_content[
        "result"
    ]
    guides = call("search_fleet_knowledge", query="GPS jump", limit=10).structured_content["result"]
    assert {r["kind"] for r in incidents} == {"incident"}
    assert {r["kind"] for r in guides} == {"guide"}


@requires_ollama
def test_ollama_down_is_a_tool_error(kb, monkeypatch):
    monkeypatch.setattr(rag, "OLLAMA_URL", "http://127.0.0.1:9")  # nothing listens on port 9
    with pytest.raises(ToolError, match="ollama serve"):
        call("search_fleet_knowledge", query="speed limit")
