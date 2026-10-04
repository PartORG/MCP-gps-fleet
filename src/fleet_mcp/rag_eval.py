"""Retrieval eval: how often does search put the right document near the top?

Run:  uv run fleet-eval        (needs Ollama and a built index: uv run fleet-ingest)

The golden set (tests/eval_questions.json) pairs questions with the kb file(s) that
answer them.  Every question is searched in each mode and we record the RANK of the
first chunk from an expected file (1 = top result).  From those ranks:

  hit@k  share of questions whose expected document is in the top k.
         hit@3 matters most: the tools return a handful of chunks, and the LLM
         reads all of them, so "in the top 3" is "the LLM sees it".
  MRR    mean reciprocal rank: average of 1/rank (0 if not found in the top 10).
         1.0 = always first; it rewards ranking the answer higher, not just finding it.

Rules for a meaningful eval: write the questions BEFORE looking at search results,
in the words a user would use (not copied from the documents), and never tune the
search to a single question; a change must improve the numbers overall.
"""

import json
from contextlib import closing
from pathlib import Path

from fleet_mcp import rag

QUESTIONS = rag.ROOT / "tests" / "eval_questions.json"
MODES: tuple[rag.Mode, ...] = ("vector", "keyword", "hybrid")
DEPTH = 10  # look this deep for the rank; deeper misses count as "not found"


def ranks(conn, mode: rag.Mode, questions: list[dict]) -> list[int | None]:
    """Rank of the first expected document for each question (None = not in the top DEPTH)."""
    result = []
    for q in questions:
        found = rag.search(conn, q["question"], q["kind"], DEPTH, mode)
        files = [Path(chunk.source).name for chunk in found]
        result.append(next((i for i, f in enumerate(files, start=1) if f in q["expected"]), None))
    return result


def metrics(ranks: list[int | None]) -> dict[str, float]:
    n = len(ranks)
    hit = lambda k: sum(r is not None and r <= k for r in ranks) / n
    return {"hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "MRR": sum(1 / r for r in ranks if r) / n}


def main() -> None:
    questions = json.loads(QUESTIONS.read_text())
    with closing(rag.connect()) as conn:
        results = {mode: ranks(conn, mode, questions) for mode in MODES}

    print(f"{len(questions)} questions from {QUESTIONS.relative_to(rag.ROOT)}\n")
    print(f"{'mode':<9}" + "".join(f"{m:>8}" for m in metrics(results["hybrid"])))
    for mode, r in results.items():
        print(f"{mode:<9}" + "".join(f"{v:>8.2f}" for v in metrics(r).values()))

    # Per-question ranks show WHERE the modes differ, e.g. identifiers vs paraphrases.
    print(f"\nrank of the expected document per question (- = not in top {DEPTH}):")
    print(f"{'vec':>4}{'key':>4}{'hyb':>4}  question")
    for i, q in enumerate(questions):
        cells = "".join(f"{results[m][i] or '-':>4}" for m in MODES)
        print(f"{cells}  {q['question']}")
