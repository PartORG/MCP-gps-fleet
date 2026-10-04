"""Agent eval: which local chat model answers fleet questions best through our MCP server?

Run:  uv run fleet-agent-eval                      every variant, every question once
      uv run fleet-agent-eval qwen3:4b llama3.2:3b only these variants
      uv run fleet-agent-eval --runs 3              each question 3 times (local models
                                                    vary from run to run; ~1 h for all)

rag_eval.py measures retrieval alone; this measures the whole loop a user sees:
the model must pick the right tools, in the right order, with real arguments, and
then answer with the facts from the tool results without inventing any.

Every variant runs the same agent as `fleet-chat` (chat.build_agent) on the same
questions.  Per run we check:

  tools      the model called the tools the question needs.  Each requirement is a
             group: calling any one tool of the group counts (several tools can be valid).
  facts      the answer contains the expected facts.  They are computed from fleet.db
             when the eval runs (e.g. the plates of the offline vehicles right now), so
             the eval stays correct after every re-seed.
  invented   plates (FM-xxxx) in the answer that appear in no tool result and not in the
             question, i.e. made up.  A precise hallucination check for this domain.
  retries    tool calls the server rejected (bad arguments, unknown plate...) that the
             model then had to correct; not a failure in itself, but costs time.

A run PASSES with all tools, all facts and nothing invented.  Local models are not
deterministic: use --runs 3 (or more) before drawing conclusions from small differences.
"""

import argparse
import asyncio
import re
import sys
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from statistics import median

import httpx
from pydantic_ai.messages import RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import UsageLimits

from fleet_mcp import chat, db
from fleet_mcp.rag import OLLAMA_URL

# Same context window for every model (see Modelfile): with Ollama's 4096 default the
# small models would lose because their prompt gets truncated, not because they're worse.
CONTEXT = 12288

# label -> (Ollama model to chat with, base model it is created from, extra instructions).
# Models that don't exist yet are created from their base with CONTEXT tokens of context;
# that only adds a small config entry in Ollama, the weights are shared with the base.
VARIANTS = {
    "qwen3:8b": ("fleet-qwen3", "qwen3:8b", ""),
    # Qwen3's documented soft switch: skip the long "thinking" phase before answering.
    "qwen3:8b no-think": ("fleet-qwen3", "qwen3:8b", "\n/no_think"),
    "qwen3:4b": ("fleet-qwen3-4b", "qwen3:4b", ""),
    "llama3.2:3b": ("fleet-llama3.2-3b", "llama3.2:3b", ""),
}


@dataclass
class Case:
    question: str
    tools: list[set[str]]  # each set: at least one of these tools must be called
    facts: Callable  # (sqlite3.Connection) -> list of strings the answer must contain


def _plates(conn, sql: str) -> list[str]:
    return [r[0] for r in conn.execute(sql)]


def _fastest(conn) -> list[str]:
    """Plate and driver of the fastest telemetry sample of the last 7 days."""
    top = db.speed_violations(conn, min_speed_kmh=0, since_hours=24 * 7, limit=1)[0]
    return [top.registration, top.driver] if top.driver else [top.registration]


def _where_is(conn, registration: str) -> list[str]:
    """Nearest city of the vehicle's last position, and its driver (pool vehicles have none)."""
    status = db.vehicle_status(conn, registration)
    return [status.nearest_city, status.driver] if status.driver else [status.nearest_city]


def _driver_and_status(conn, registration: str) -> list[str]:
    status = db.vehicle_status(conn, registration)
    return [status.driver, status.status]


CASES = [
    Case(
        "Which vehicles are offline right now?",
        [{"list_vehicles"}],
        lambda c: _plates(c, "SELECT registration FROM vehicles WHERE status = 'offline'"),
    ),
    Case(
        "Which vehicles are in maintenance, and what does that status mean for route planning?",
        [{"list_vehicles"}, {"search_fleet_knowledge"}],
        lambda c: _plates(c, "SELECT registration FROM vehicles WHERE status = 'maintenance'"),
    ),
    Case(
        "Which vehicle had the most recent GPS jump, and based on past incidents, what could have caused it?",
        [{"find_anomalies"}, {"search_similar_incidents", "search_fleet_knowledge"}],
        lambda c: _plates(
            c, "SELECT registration FROM alerts WHERE type = 'gps_jump' ORDER BY ts DESC LIMIT 1"
        ),
    ),
    Case(
        "Which vehicle was driven fastest in the last 7 days, and who was driving?",
        [{"find_speed_violations"}],
        lambda c: _fastest(c),
    ),
    Case(
        "Where is FM-0977 right now, and who is its driver?",
        [{"get_vehicle_status", "list_vehicles"}],
        lambda c: _where_is(c, "FM-0977"),
    ),
    Case(
        "What speed limit applies to our trucks on German motorways?",
        [{"search_fleet_knowledge"}],
        lambda c: ["80"],
    ),
    # Facts below are chosen to stay stable during a long eval run: nothing that depends on
    # a sliding time window like "trips in the last 24 hours".
    Case(
        "Which vehicles are idle right now?",
        [{"list_vehicles"}],
        lambda c: _plates(c, "SELECT registration FROM vehicles WHERE status = 'idle'"),
    ),
    Case(
        "Who is the assigned driver of FM-0012, and what is the vehicle's status?",
        [{"get_vehicle_status", "list_vehicles"}],
        lambda c: _driver_and_status(c, "FM-0012"),
    ),
    Case(
        "Have we had GPS problems with FM-0550 before? What was the cause?",
        [{"search_similar_incidents", "search_fleet_knowledge"}],
        lambda c: ["antenna"],  # incident #1208: loose, corroded antenna connector
    ),
    Case(
        "Which vehicles had the three most recent fuel drop alerts?",
        [{"find_anomalies"}],
        lambda c: _plates(
            c, "SELECT registration FROM alerts WHERE type = 'fuel_drop' ORDER BY ts DESC LIMIT 3"
        ),
    ),
]


def normalize(text: str) -> str:
    """Models like typographic dashes (FM‑0977 with U+2011); compare on plain ASCII ones."""
    return re.sub(r"[‐-―−]", "-", text)


PLATE = re.compile(r"FM-\d{4}")


def score(case_question: str, answer: str, tools_called: set[str], tool_text: str, required, facts):
    """The checks for one run, as a dict (pure function, unit-tested in tests/test_agent_eval.py)."""
    answer = normalize(answer)
    missing = [f for f in facts if f.lower() not in answer.lower()]
    known = set(PLATE.findall(tool_text)) | set(PLATE.findall(case_question))
    invented = sorted(set(PLATE.findall(answer)) - known)
    tools_ok = all(group & tools_called for group in required)
    return {
        "tools_ok": tools_ok,
        "missing": missing,
        "invented": invented,
        "passed": tools_ok and not missing and not invented,
    }


def ensure_model(name: str, base: str) -> None:
    """Create `name` from `base` with CONTEXT tokens of context, unless it already exists."""
    if httpx.post(f"{OLLAMA_URL}/api/show", json={"model": name}, timeout=10).status_code != 404:
        return
    response = httpx.post(
        f"{OLLAMA_URL}/api/create",
        json={"model": name, "from": base, "parameters": {"num_ctx": CONTEXT}, "stream": False},
        timeout=120,
    )
    if response.status_code != 200:
        sys.exit(f"Could not create {name} from {base}: {response.text}. Try `ollama pull {base}`.")


async def run_case(agent, case: Case, facts: list[str]) -> dict:
    start = time.perf_counter()
    try:
        # A cap on model requests, so a model stuck in a tool-calling loop can't run forever.
        result = await agent.run(case.question, usage_limits=UsageLimits(request_limit=12))
    except Exception as e:  # noqa: BLE001 - any crash is a failed run, recorded, not fatal
        return {
            "passed": False,
            "error": f"{type(e).__name__}: {e}"[:120],
            "seconds": time.perf_counter() - start,
        }
    parts = [p for m in result.all_messages() for p in m.parts]
    run = score(
        case.question,
        result.output,
        {p.tool_name for p in parts if isinstance(p, ToolCallPart)},
        " ".join(str(p.content) for p in parts if isinstance(p, ToolReturnPart)),
        case.tools,
        facts,
    )
    run["retries"] = sum(isinstance(p, RetryPromptPart) for p in parts)
    run["answer"] = " ".join(result.output.split())  # one line, for the progress output
    run["seconds"] = time.perf_counter() - start
    run["context"] = result.response.usage.input_tokens
    return run


async def evaluate(label: str, facts: list[list[str]], repeats: int) -> list[dict]:
    """All questions, `repeats` times, for one variant.  Each run dict gets its question index."""
    model, base, extra = VARIANTS[label]
    ensure_model(model, base)
    agent = chat.build_agent(model, extra)
    runs = []
    async with agent:  # one MCP server process for all questions of this variant
        for _ in range(repeats):
            for i, (case, case_facts) in enumerate(zip(CASES, facts, strict=True), start=1):
                run = await run_case(agent, case, case_facts)
                run["question"] = i
                mark = "PASS" if run["passed"] else "fail"
                detail = run.get("error") or ", ".join(
                    f"{k}={run[k]}"
                    for k in ("tools_ok", "missing", "invented")
                    if run[k] not in (True, [], ())
                )
                print(f"  [{label}] Q{i} {mark} {run['seconds']:5.0f}s  {detail}", flush=True)
                if not run["passed"] and "answer" in run:  # show what the model said instead
                    print(f"      answer: {run['answer'][:150]}", flush=True)
                runs.append(run)
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare local chat models end to end.")
    parser.add_argument(
        "variants", nargs="*", default=list(VARIANTS), help=f"default: all of {list(VARIANTS)}"
    )
    parser.add_argument("--runs", type=int, default=1, help="repeat every question N times (models vary)")
    args = parser.parse_args()
    if unknown := set(args.variants) - set(VARIANTS):
        sys.exit(f"Unknown variant(s) {sorted(unknown)}; choose from {list(VARIANTS)}")
    # The expected facts are computed once, from the same fleet.db the MCP server reads.
    with closing(db.connect()) as conn:
        facts = [case.facts(conn) for case in CASES]

    results = {}
    for label in args.variants:
        print(f"{label}:", flush=True)
        results[label] = asyncio.run(evaluate(label, facts, args.runs))

    print(f"\n{len(CASES)} questions x {args.runs} run(s) per variant\n")
    print(
        f"{'variant':<20}{'passed':>9}{'tools':>8}{'facts':>8}{'invent':>8}{'retry':>7}{'median s':>10}{'max ctx':>9}"
    )
    for label, runs in results.items():
        ok = [r for r in runs if "error" not in r]
        n = len(runs)
        print(
            f"{label:<20}"
            f"{sum(r['passed'] for r in runs):>6}/{n}"
            f"{sum(r['tools_ok'] for r in ok):>5}/{n}"
            f"{sum(not r['missing'] for r in ok):>5}/{n}"
            f"{sum(len(r['invented']) for r in ok):>8}"
            f"{sum(r['retries'] for r in ok):>7}"
            f"{median(r['seconds'] for r in runs):>10.0f}"
            f"{max((r['context'] for r in ok), default=0):>9}"
        )
    print("\npassed = right tools + all facts + nothing invented; invent = made-up plates (total)")

    # Which questions are hard for which model: passes out of runs, per question.
    print("\npasses per question:")
    print(f"{'':<4}" + "".join(f"{label[:18]:>20}" for label in results))
    for i, case in enumerate(CASES, start=1):
        cells = "".join(
            f"{sum(r['passed'] for r in runs if r['question'] == i):>18}/{args.runs}"
            for runs in results.values()
        )
        print(f"Q{i:<3}{cells}   {case.question[:60]}")
