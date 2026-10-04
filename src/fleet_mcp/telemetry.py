"""OpenTelemetry tracing: off unless OTEL_EXPORTER_OTLP_ENDPOINT is set.

See every step of a question as one timed trace, e.g. with Jaeger:

    docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/jaeger:2.21.0
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run fleet-chat "why did FM-0977 jump?"
    open http://localhost:16686  (service "fleet-chat")

Where the spans come from (most of it is not our code):
    fleet-chat  Pydantic AI (instrument_all): the agent run, each model request, each tool call
    fleet-mcp   MCP SDK (built in, on by default): one span per request, e.g. "tools/call find_anomalies"
                rag.py (ours): rag.search with its stages rag.vector / rag.keyword / rag.rerank
    both        httpx instrumentation: every HTTP call, i.e. every Ollama request

The MCP client puts the trace context (W3C `traceparent`) into each request's `_meta`
field, and the MCP SDK on the server continues that trace.  So even over stdio, the
server's spans appear inside the client's trace: one trace per question, two processes.

Without the env var nothing is set up and the OpenTelemetry API calls are no-ops.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup(service_name: str) -> bool:
    """Export traces via OTLP/HTTP if OTEL_EXPORTER_OTLP_ENDPOINT is set.  Returns whether it did."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    # service.name tells the processes apart in the trace viewer (set in code, so the client
    # and the server it starts keep different names even though they share the OTEL_* env).
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    # Batch: spans are sent in the background every few seconds, not one HTTP call per span.
    # The exporter reads the endpoint (+ /v1/traces) and any headers from the OTEL_* env vars.
    # The provider flushes the remaining spans when the process exits.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()  # spans for all httpx calls (Ollama)
    return True
