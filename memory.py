"""Bug-pattern memory: clusters real bugs caught across runs to answer
"which bug patterns does this model reliably miss" — the Memory component
from the original project charter, built to complete that promise.

Runs entirely locally via a small Hugging Face sentence-transformer model
(no OpenRouter calls, no per-use cost) — this feature accumulates for $0
regardless of API quota. Cluster labels are derived from the failing
test's own name rather than an LLM call, for the same reason.
"""

import json
import re
import struct
from datetime import datetime, timezone

import storage

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Cosine similarity above this is treated as "the same bug pattern" —
# a simple, tunable heuristic, not a rigorously validated threshold. Chosen
# because a bug's signature is short (a test name + one error line), so
# two genuinely different bugs rarely score much above ~0.5 in practice,
# leaving headroom before this threshold.
SIMILARITY_THRESHOLD = 0.75

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def embed(text: str) -> list[float]:
    model = _get_model()
    # normalize_embeddings=True makes cosine similarity equal a plain dot
    # product, which is all _cosine() below needs to compute.
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _bug_signature(test_code: str, error: str | None) -> str:
    # The test's own name usually names the kind of edge case being
    # exercised ("none_input", "unsorted_input") independent of which
    # spec's function is under test — that's the part worth clustering on,
    # more than the literal assertion values. The error's last line (the
    # exception type + message) adds the failure's actual shape without
    # the temp-dir path noise every other line of a traceback carries.
    name_match = re.match(r"def test_(\w+)", test_code.strip())
    name = name_match.group(1).replace("_", " ") if name_match else "unnamed test"

    last_error_line = ""
    if error:
        lines = [l.strip() for l in error.strip().splitlines() if l.strip()]
        if lines:
            last_error_line = lines[-1]

    return f"{name}: {last_error_line}".strip(": ")


def _humanize_test_name(test_code: str) -> str:
    match = re.match(r"def test_(\w+)", test_code.strip())
    if match:
        return match.group(1).replace("_", " ")
    return "unlabeled bug"


def record_bug(spec_id: int, test_code: str, error: str | None, db_path: str = storage.DB_PATH) -> int:
    """Matches a real bug against known patterns, or creates a new one.
    Returns the bug_patterns.id it was assigned to."""
    signature = _bug_signature(test_code, error)
    vector = embed(signature)

    conn = storage.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, centroid, frequency, example_spec_ids FROM bug_patterns")
        rows = cur.fetchall()

        best_match = None
        best_similarity = -1.0
        for pid, centroid_blob, frequency, example_spec_ids_json in rows:
            similarity = _cosine(vector, _unpack(centroid_blob))
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = (pid, _unpack(centroid_blob), frequency, example_spec_ids_json)

        if best_match is not None and best_similarity >= SIMILARITY_THRESHOLD:
            pid, centroid, frequency, example_spec_ids_json = best_match
            example_spec_ids = json.loads(example_spec_ids_json)
            if spec_id not in example_spec_ids:
                example_spec_ids.append(spec_id)

            new_frequency = frequency + 1
            # Running average: the centroid drifts toward the true center
            # of the cluster as more examples join it.
            new_centroid = [
                (c * frequency + v) / new_frequency for c, v in zip(centroid, vector)
            ]
            cur.execute(
                "UPDATE bug_patterns SET centroid = ?, frequency = ?, example_spec_ids = ? WHERE id = ?",
                (_pack(new_centroid), new_frequency, json.dumps(example_spec_ids), pid),
            )
            conn.commit()
            return pid

        description = _humanize_test_name(test_code)
        cur.execute(
            "INSERT INTO bug_patterns "
            "(description, representative_test, centroid, frequency, example_spec_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                description,
                test_code,
                _pack(vector),
                1,
                json.dumps([spec_id]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_patterns(db_path: str = storage.DB_PATH) -> list[dict]:
    conn = storage.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, description, frequency, example_spec_ids, representative_test "
            "FROM bug_patterns ORDER BY frequency DESC"
        )
        columns = [c[0] for c in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        for row in rows:
            row["example_spec_ids"] = json.loads(row["example_spec_ids"])
        return rows
    finally:
        conn.close()
