"""RAG: the fleet knowledge base (kb/*.md) turned into a searchable index (vectors + keywords).

Run:  uv run fleet-ingest      (re-run whenever a file in kb/ changes)

The two halves
--------------
INGEST (offline, once)                       SEARCH (per tool call, "hybrid")
  kb/**/*.md                                   question from the LLM
    └─ split_markdown(): one chunk per           ├─ vector:  embed() the question, sqlite-vec
       "## section" (guides) or per file         │           KNN -> 20 chunks closest in MEANING
       (incident reports)                        ├─ keyword: FTS5 full-text search, BM25 ranking
    └─ embed() each chunk via Ollama             │           -> 20 chunks sharing the most WORDS
       (nomic-embed-text, 768 numbers)           └─ fuse both rankings (RRF), keep the top `limit`
    └─ store text in `chunks`,                      └─ list[KnowledgeChunk] to the LLM
       vectors in `chunk_vectors`,
       words in `chunks_fts` (data/kb.db)

Why both?  They fail differently.  Vectors understand paraphrases ("fuel paid with the
company card never showed up in the tank") but blur exact tokens: plates, incident
numbers, "561/2006".  Keywords nail exact tokens but miss paraphrases.  Measure the
difference with `uv run fleet-eval` (rag_eval.py).

Why a separate data/kb.db and not fleet.db?  `fleet-seed` deletes and rebuilds
fleet.db whenever the synthetic data gets old; the knowledge base only changes
when the markdown changes, and re-embedding it should not depend on re-seeding.

Note: the LLM never sees vectors.  It sends text and gets text back; embeddings
are only how we find the relevant text.
"""

import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Literal, NamedTuple

import httpx
import sqlite_vec

from fleet_mcp import db
from fleet_mcp.models import KnowledgeChunk

ROOT = Path(__file__).resolve().parents[2]  # rag.py -> fleet_mcp/ -> src/ -> project root
KB_DIR = ROOT / "kb"
KB_PATH = Path(os.environ.get("FLEET_KB_DB", ROOT / "data" / "kb.db"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# The embedding model is baked into the index: vectors from different models are
# not comparable, and the table below is created for exactly 768 numbers.
# Changing the model = change both constants + re-run fleet-ingest.
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768

SCHEMA = f"""
CREATE TABLE chunks (
    id      INTEGER PRIMARY KEY,
    source  TEXT NOT NULL,   -- path relative to kb/, e.g. 'incidents/incident_1842.md'
    kind    TEXT NOT NULL CHECK (kind IN ('guide', 'incident')),
    title   TEXT NOT NULL,   -- the document's '# ' heading
    section TEXT NOT NULL,   -- the '## ' heading of this chunk, '' for a whole document
    text    TEXT NOT NULL    -- what is embedded and what the LLM gets back
);
-- sqlite-vec virtual table: one row per chunk, rowid = chunks.id.
--   distance_metric=cosine : compare directions of vectors (standard for text embeddings)
--   kind text              : a "metadata column", lets the KNN search filter by kind
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
    embedding float[{EMBED_DIM}] distance_metric=cosine,
    kind text
);
-- FTS5 full-text index over chunks.text (built into SQLite, no extension needed).
--   content='chunks'  : "external content" table, the text is not stored twice;
--                       FTS5 reads it from `chunks` and only keeps its word index
--   porter unicode61  : lowercase, split on non-alphanumerics, and reduce English words
--                       to their stem, so "jumps" / "jumped" / "jump" all match
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
"""

# Each retriever returns this many candidates before fusion; more than `limit`, so a
# chunk ranked 8th by one retriever and 2nd by the other can still win.
CANDIDATES = 20
# Reciprocal Rank Fusion constant (Cormack et al., 2009; 60 is the usual value).
# A chunk's fused score is the sum over retrievers of 1 / (RRF_K + its rank there).
# The larger the constant, the less a single #1 rank dominates over agreement.
RRF_K = 60
Mode = Literal["vector", "keyword", "hybrid"]


class Chunk(NamedTuple):
    source: str
    kind: Literal["guide", "incident"]
    title: str
    section: str
    text: str


def split_markdown(source: str, markdown: str) -> list[Chunk]:
    """Cut one markdown file into chunks.

    * Incident reports (kb/incidents/) are short and only make sense as a whole
      (observed -> investigation -> root cause -> resolution): one chunk per file.
    * Guides cover several topics: one chunk per '## ' section, so a search for
      "truck speed limit" returns that section and not a whole policy document.
      The document title is repeated in every chunk's text, so a section like
      "Signature: leak" still says it is about fuel when embedded on its own.
    """
    first_line, _, body = markdown.strip().partition("\n")
    title = first_line.removeprefix("# ").strip()

    if source.startswith("incidents/"):
        return [Chunk(source, "incident", title, "", markdown.strip())]

    intro, *sections = re.split(r"^## ", body, flags=re.MULTILINE)
    chunks = [Chunk(source, "guide", title, "", f"# {title}\n{intro.strip()}")] if intro.strip() else []
    for section in sections:
        heading, _, text = section.partition("\n")
        chunks.append(
            Chunk(source, "guide", title, heading.strip(), f"# {title}\n## {heading}\n{text.strip()}")
        )
    return chunks


def embed(texts: list[str], *, query: bool) -> list[list[float]]:
    """Turn texts into vectors with the local Ollama embedding model.

    nomic-embed-text was trained with task prefixes: questions get
    "search_query: ", the stored texts get "search_document: ".  Leaving them
    out noticeably hurts retrieval quality.
    """
    prefix = "search_query: " if query else "search_document: "
    response = httpx.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": [prefix + t for t in texts]},
        timeout=120,  # the first call loads the model into memory, which takes a few seconds
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def connect(path: Path | None = None, *, readonly: bool = True):
    """Open the kb database with the sqlite-vec extension loaded."""
    conn = db.connect(path or KB_PATH, readonly=readonly)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    # Switch loading off again so SQL can't call load_extension() on this connection.
    conn.enable_load_extension(False)
    return conn


def ingest(kb_dir: Path, path: Path) -> int:
    """Rebuild the index at `path` from every markdown file under `kb_dir`.  Returns #chunks."""
    chunks = [
        chunk
        for file in sorted(kb_dir.rglob("*.md"))
        for chunk in split_markdown(file.relative_to(kb_dir).as_posix(), file.read_text())
    ]
    # Embed BEFORE touching the old index: if Ollama is down, the old index survives.
    # ponytail: one request for all chunks; batch it if the kb grows to thousands of chunks.
    vectors = embed([c.text for c in chunks], query=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = connect(path, readonly=False)
    with conn:  # one transaction
        conn.executescript(SCHEMA)
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True), start=1):
            conn.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)", (i, *chunk))
            conn.execute(
                "INSERT INTO chunk_vectors (rowid, embedding, kind) VALUES (?, ?, ?)",
                # sqlite-vec stores vectors as packed 32-bit floats
                (i, sqlite_vec.serialize_float32(vector), chunk.kind),
            )
        # External-content FTS5 tables are filled by this special command: (re)index
        # every row of `chunks` in one go.
        conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('rebuild')")
    conn.close()
    return len(chunks)


def search(conn, query: str, kind: str | None, limit: int, mode: Mode = "hybrid") -> list[KnowledgeChunk]:
    """The `limit` most relevant chunks for `query`, best first; `kind` filters guide/incident.

    `mode` exists for the eval (comparing retrievers); the MCP tools always use "hybrid".
    """
    rankings = []  # one list of chunk ids per retriever, best first
    if mode in ("vector", "hybrid"):
        rankings.append(_vector_ranking(conn, query, kind))
    if mode in ("keyword", "hybrid"):
        rankings.append(_keyword_ranking(conn, query, kind))

    # Reciprocal Rank Fusion: only the RANK in each list counts, not the raw scores.
    # That is why it can combine cosine distances and BM25 scores, which are on
    # completely different scales.  With a single retriever it keeps that order.
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1 / (RRF_K + rank)
    best = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]

    rows = conn.execute(
        f"SELECT id, source, kind, title, section, text FROM chunks WHERE id IN ({','.join('?' * len(best))})",
        best,  # the placeholders are generated, the values are bound: no user text in the SQL
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [
        KnowledgeChunk(**{k: by_id[i][k] for k in Chunk._fields}, score=round(scores[i], 4)) for i in best
    ]


def _vector_ranking(conn, query: str, kind: str | None) -> list[int]:
    """Chunk ids closest in meaning to `query` (cosine distance of embeddings)."""
    [vector] = embed([query], query=True)
    # KNN syntax of sqlite-vec: `embedding MATCH <vector> AND k = <n>` returns the n
    # nearest rows, nearest first.  The optional filter is a fixed string, never user input.
    kind_filter = "AND kind = :kind" if kind else ""
    rows = conn.execute(
        f"SELECT rowid FROM chunk_vectors WHERE embedding MATCH :vector AND k = :k {kind_filter} ORDER BY distance",
        {"vector": sqlite_vec.serialize_float32(vector), "k": CANDIDATES, "kind": kind},
    ).fetchall()
    return [r[0] for r in rows]


def _keyword_ranking(conn, query: str, kind: str | None) -> list[int]:
    """Chunk ids sharing the most (rare) words with `query`, by FTS5's BM25 ranking."""
    # FTS5 has its own query language (AND, NOT, quotes, *, ...), so passing the raw
    # question could be a syntax error ("24 %") or change the meaning.  Instead: keep
    # only the words, quote each one, and OR them, i.e. "match any of these words".
    # BM25 then ranks chunks higher for rarer words, so "the" barely counts and
    # "0550" counts a lot.  Words are [a-zA-Z0-9_] runs, so they cannot contain quotes.
    words = re.findall(r"\w+", query)
    if not words:
        return []
    kind_filter = "AND c.kind = :kind" if kind else ""
    rows = conn.execute(
        f"""
        SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH :match {kind_filter}
        ORDER BY chunks_fts.rank   -- rank = BM25; lower is better
        LIMIT :n
        """,
        {"match": " OR ".join(f'"{w}"' for w in words), "kind": kind, "n": CANDIDATES},
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    n = ingest(KB_DIR, KB_PATH)
    print(f"Indexed {n} chunks from {KB_DIR} into {KB_PATH} ({EMBED_MODEL})")
