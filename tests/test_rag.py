"""Checks for the RAG layer: chunking, retrieval quality and the two search tools.

Everything except the chunking and "not built" tests needs a running Ollama with
nomic-embed-text; without it those tests are SKIPPED (shown as 's'), not failed.
"""

import asyncio
import json
import shutil
from contextlib import closing

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fleet_mcp import rag, rag_eval
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


@requires_ollama
def test_retrieval_quality_gate(kb_path):
    """Regression gate on the eval set (tests/eval_questions.json, see rag_eval.py).

    Thresholds sit just below the measured v4 numbers (hybrid hit@3 = 1.00, MRR 0.91;
    vector MRR 0.87), so a change that makes retrieval worse fails here.  Run
    `uv run fleet-eval` for the full per-question table.
    """
    questions = json.loads(rag_eval.QUESTIONS.read_text())
    with closing(rag.connect(kb_path)) as conn:
        hybrid = rag_eval.metrics(rag_eval.ranks(conn, "hybrid", questions))
        vector = rag_eval.metrics(rag_eval.ranks(conn, "vector", questions))
    assert hybrid["hit@3"] >= 0.95  # at most one question may fall out of the top 3
    assert hybrid["MRR"] >= vector["MRR"]  # the reason hybrid exists


@requires_ollama
def test_keyword_search_survives_fts_syntax(kb_path):
    """FTS5 has its own query language; user text with quotes/operators must not break it."""
    with closing(rag.connect(kb_path)) as conn:
        for query in ['fuel fell 24 % ("theft")', "NOT * OR AND", "speed-limit: 130?", "!!!"]:
            rag.search(conn, query, None, 3, "keyword")  # must not raise


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


@requires_ollama
def test_outdated_index_is_a_tool_error(kb_path, tmp_path, monkeypatch):
    """An index built before v4 has no keyword table: tell the user to re-ingest."""
    old = tmp_path / "kb.db"
    shutil.copy(kb_path, old)
    with closing(rag.connect(old, readonly=False)) as conn:
        conn.execute("DROP TABLE chunks_fts")
    monkeypatch.setattr(rag, "KB_PATH", old)
    with pytest.raises(ToolError, match="outdated.*fleet-ingest"):
        call("search_fleet_knowledge", query="speed limit")
