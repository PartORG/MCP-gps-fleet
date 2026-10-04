"""Checks for tracing: off by default, and nested spans for the search stages.

Spans go to an in-memory exporter and Ollama is replaced by fake embeddings, so this
needs neither a trace collector nor Ollama.  The real end-to-end trace (chat process ->
MCP server process -> Ollama) is checked by hand with Jaeger, see telemetry.py.
"""

from contextlib import closing

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fleet_mcp import rag, telemetry

# The global tracer provider can be set only once per process, so once for this module.
spans = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(spans))  # simple = export immediately
trace.set_tracer_provider(_provider)


def test_setup_is_off_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.setup("test") is False


def test_search_stages_are_nested_spans(tmp_path, monkeypatch):
    # Fake embeddings: same vector for everything, enough for the index and the KNN query.
    monkeypatch.setattr(rag, "ensure_embed_model", lambda: None)
    monkeypatch.setattr(rag, "embed", lambda texts, query: [[1.0] + [0.0] * (rag.EMBED_DIM - 1)] * len(texts))
    path = tmp_path / "kb.db"
    rag.ingest(rag.KB_DIR, path)
    spans.clear()

    with closing(rag.connect(path)) as conn:
        rag.search(conn, "secret question about FM-0550", "incident", 3, mode="hybrid")

    by_name = {s.name: s for s in spans.get_finished_spans()}
    search = by_name["rag.search"]
    assert dict(search.attributes) == {"rag.mode": "hybrid", "rag.kind": "incident", "rag.limit": 3}
    for stage in ("rag.vector", "rag.keyword"):
        assert by_name[stage].parent.span_id == search.context.span_id  # nested under rag.search
    # The query text must not end up in any span (traces often go to shared backends).
    assert not any("secret" in str(v) for s in spans.get_finished_spans() for v in s.attributes.values())
