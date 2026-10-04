"""RAG: the fleet knowledge base (kb/*.md) turned into a searchable vector index.

Run:  uv run fleet-ingest      (re-run whenever a file in kb/ changes)

The two halves
--------------
INGEST (offline, once)                       SEARCH (per tool call)
  kb/**/*.md                                   question from the LLM
    └─ split_markdown(): one chunk per           └─ embed() with the "search_query: " prefix
       "## section" (guides) or per file            └─ sqlite-vec KNN: the k nearest chunk
       (incident reports)                              vectors by cosine distance
    └─ embed() each chunk via Ollama                └─ join back to the chunk text
       (nomic-embed-text, 768 numbers)                 └─ list[KnowledgeChunk] to the LLM
    └─ store text in `chunks`,
       vectors in `chunk_vectors` (data/kb.db)

Why a separate data/kb.db and not fleet.db?  `fleet-seed` deletes and rebuilds
fleet.db whenever the synthetic data gets old; the knowledge base only changes
when the markdown changes, and re-embedding it should not depend on re-seeding.

Note: the LLM never sees vectors.  It sends text and gets text back; embeddings
are only how we find the relevant text.
"""

import os
import re
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
"""


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
    conn.close()
    return len(chunks)


def search(conn, query: str, kind: str | None, limit: int) -> list[KnowledgeChunk]:
    """The `limit` chunks closest in meaning to `query`, best first; `kind` filters guide/incident."""
    [vector] = embed([query], query=True)
    # KNN syntax of sqlite-vec: `embedding MATCH <vector> AND k = <n>` returns the
    # n nearest rows with a `distance` column.  It runs in the CTE on its own, then
    # we join the text.  The optional filter is one of two fixed strings, never user input.
    kind_filter = "AND kind = :kind" if kind else ""
    rows = conn.execute(
        f"""
        WITH nearest AS (
            SELECT rowid, distance FROM chunk_vectors
            WHERE embedding MATCH :vector AND k = :k {kind_filter}
        )
        SELECT c.source, c.kind, c.title, c.section, c.text, nearest.distance
        FROM nearest JOIN chunks c ON c.id = nearest.rowid
        ORDER BY nearest.distance
        """,
        {"vector": sqlite_vec.serialize_float32(vector), "k": limit, "kind": kind},
    ).fetchall()
    # cosine distance = 1 - cosine similarity; similarity reads more naturally as a score
    return [
        KnowledgeChunk(**{k: r[k] for k in Chunk._fields}, score=round(1 - r["distance"], 3)) for r in rows
    ]


def main() -> None:
    n = ingest(KB_DIR, KB_PATH)
    print(f"Indexed {n} chunks from {KB_DIR} into {KB_PATH} ({EMBED_MODEL})")
