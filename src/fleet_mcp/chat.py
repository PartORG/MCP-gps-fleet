"""Offline chat client: a local Ollama model answers fleet questions through our MCP server.

Run:  uv run fleet-chat                          interactive conversation
      uv run fleet-chat "which vehicles are idle?"   one question, then exit

Nothing leaves the machine: the LLM (qwen3:8b), the embeddings and the data are all local.
It shows the point of MCP: the same server works unchanged with Claude and with a local model.

How it fits together
--------------------
    you ──> Pydantic AI Agent ──(OpenAI-compatible API)──> Ollama (qwen3:8b)
                 │      ^                                        │
                 │      └──────── "call tool X with args Y" ─────┘
                 └──MCP──> fleet-mcp (server.py) ──> fleet.db / kb.db
    The agent loops: ask the model -> run the tool calls it requests over MCP
    -> send the results back -> ... until the model answers in plain text.

Transport (same switch idea as the server):
    default               stdio: the agent starts `fleet-mcp` itself, using the command in .mcp.json
    FLEET_MCP_URL=<url>   HTTP: connect to a server already running with FLEET_TRANSPORT=http,
                          e.g. FLEET_MCP_URL=http://127.0.0.1:8000/mcp
    FLEET_API_TOKEN=<t>   sent as `Authorization: Bearer <t>` to a token-protected HTTP server
"""

import asyncio
import json
import os
import sys

# Pydantic AI prints an advert for its observability service unless this is set.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from fleet_mcp import telemetry
from fleet_mcp.rag import OLLAMA_URL, ROOT

# fleet-qwen3 = qwen3:8b with a 12k context window, created from ./Modelfile (see the comments there).
# Any Ollama model with tool support works, e.g. FLEET_CHAT_MODEL=llama3.1:8b.
CHAT_MODEL = os.environ.get("FLEET_CHAT_MODEL", "fleet-qwen3")

# Client-side instructions.  The domain knowledge (what the tools are, typical flow)
# comes from the server's own `instructions`, see include_instructions below.
INSTRUCTIONS = (
    "You are a fleet operations assistant. Answer only from tool results; never invent "
    "vehicles, numbers or incidents. When a question is about why something happened, look at "
    "the data first, then search the knowledge base, and say which source each statement comes "
    "from. If the tools return nothing relevant, say so. Answer concisely in plain text.\n"
    # Small local models like to request every tool at once, filling in values they don't
    # know yet (seen: registration="<registration>"), then answer as if the call succeeded.
    "Work step by step: when a tool needs a value from another tool's result (like a "
    "registration or a time), call the first tool, read its result, and only then call the "
    "next one. Never use placeholders or guessed values as arguments. If a tool returns an "
    "error, fix the arguments and call it again instead of answering without that data."
)


def build_agent(model_name: str = CHAT_MODEL, extra_instructions: str = "") -> Agent:
    """The fleet agent: a local Ollama model + our MCP server as its toolset.

    The parameters exist for the model comparison in agent_eval.py; fleet-chat uses the defaults.
    """
    headers = None
    if url := os.environ.get("FLEET_MCP_URL"):
        transport = url  # MCPToolset builds a Streamable HTTP client from a URL
        if token := os.environ.get("FLEET_API_TOKEN"):
            headers = {"Authorization": f"Bearer {token}"}
    else:
        # Reuse Claude Code's config, so there is one place that says how to start the server.
        server = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["fleet"]
        # The MCP SDK gives a stdio server only a whitelist of env vars (PATH, HOME, ...), so a
        # secret in your shell doesn't leak into every child process.  Our own settings must be
        # forwarded explicitly, or e.g. OLLAMA_URL / FLEET_DB would be silently ignored.
        # FLEET_TRANSPORT is pinned: the server we start here must speak stdio, even if your
        # shell has FLEET_TRANSPORT=http set for running a separate HTTP server.
        # OTEL_*: the server exports its spans to the same place (see telemetry.py).
        ours = {k: v for k, v in os.environ.items() if k.startswith(("FLEET_", "OTEL_")) or k == "OLLAMA_URL"}
        env = server.get("env", {}) | ours | {"FLEET_TRANSPORT": "stdio"}
        # cwd=ROOT: `uv run` must start inside the project, wherever fleet-chat was launched from.
        transport = StdioTransport(server["command"], server["args"], env=env, cwd=str(ROOT))

    # include_instructions=True: pass the server's `instructions` (tool overview and flow) to
    # the model. Off by default in Pydantic AI; Claude Code always reads them.
    fleet = MCPToolset(transport, include_instructions=True, headers=headers)
    # Ollama speaks the OpenAI chat API under /v1, which is what OllamaModel talks to.
    model = OllamaModel(model_name, provider=OllamaProvider(base_url=f"{OLLAMA_URL}/v1"))
    return Agent(model, toolsets=[fleet], instructions=INSTRUCTIONS + extra_instructions)


async def ask(agent: Agent, question: str, history: list) -> list:
    """Run one question; print the tool calls and the answer.  Returns the updated history."""
    result = await agent.run(question, message_history=history)
    # Show which tools the model used: the best way to see (and debug) what a small model does.
    for message in result.new_messages():
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                print(f"  [tool] {part.tool_name}({part.args_as_json_str()})")
    # Every model request re-sends the whole conversation (instructions, tool schemas, all tool
    # results so far), so the LAST request is the biggest.  That is the number that must stay
    # below the context window (12288 for fleet-qwen3) or Ollama silently cuts the beginning.
    context = result.response.usage.input_tokens
    print(f"  [usage] {result.usage.requests} model requests, last one used {context} context tokens")
    print(f"\n{result.output}\n")
    # The full history goes into the next run, so follow-ups like "and the day before?" work.
    return result.all_messages()


async def chat(question: str | None) -> None:
    agent = build_agent()
    # `async with agent` starts the MCP server once and keeps it for the whole conversation
    # (without it, every run would start and stop a new server process).
    async with agent:
        if question:
            await ask(agent, question, [])
            return
        print(f"Fleet chat ({CHAT_MODEL}, offline). Empty line or Ctrl-D to quit.")
        history: list = []
        while True:
            try:
                # input() blocks; run it in a thread so the MCP connection stays serviced meanwhile.
                question = (await asyncio.to_thread(input, "you> ")).strip()
            except EOFError:  # Ctrl-D
                break
            if not question:
                break
            history = await ask(agent, question, history)


def main() -> None:
    if telemetry.setup("fleet-chat"):
        Agent.instrument_all()  # Pydantic AI spans: agent run, model requests, tool calls
    try:
        asyncio.run(chat(" ".join(sys.argv[1:]) or None))
    except KeyboardInterrupt:  # Ctrl-C: asyncio.run re-raises it here, after cleaning up
        pass
    except ModelAPIError as e:  # Ollama not running (connection error) or model missing (404)
        sys.exit(
            f"Chat model request failed: {e}\n"
            f"Is Ollama running at {OLLAMA_URL}, and did you run `ollama create fleet-qwen3 -f Modelfile`?"
        )
