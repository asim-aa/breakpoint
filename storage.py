"""SQLite persistence for Breakpoint runs — specs, per-round attempts, and
per-test results."""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "breakpoint.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL REFERENCES specs(id),
    round INTEGER NOT NULL,
    code TEXT NOT NULL,
    verdict TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id),
    test_code TEXT NOT NULL,
    passed INTEGER NOT NULL,
    is_bug INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bug_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    representative_test TEXT NOT NULL,
    centroid BLOB NOT NULL,
    frequency INTEGER NOT NULL DEFAULT 1,
    example_spec_ids TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    # Migration for DBs created before the Validator existed: add is_valid,
    # defaulting existing rows to 1 (treat pre-validator failures as valid,
    # since that was the only behavior available at the time).
    try:
        conn.execute("ALTER TABLE tests ADD COLUMN is_valid INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migration for DBs created before bug-pattern memory existed: add
    # error, needed to build a bug's signature text for embedding.
    try:
        conn.execute("ALTER TABLE tests ADD COLUMN error TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migration for DBs created before the leaderboard existed: record
    # which model pair produced each run. NULL on old rows rather than
    # guessed — get_leaderboard() groups those under an "unknown" pair
    # instead of fabricating history.
    try:
        conn.execute("ALTER TABLE specs ADD COLUMN prover_model TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE specs ADD COLUMN skeptic_model TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    return conn


def save_run(request: str, spec: dict, history: list, report: dict, db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO specs (request, spec_json, created_at, prover_model, skeptic_model) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                request,
                json.dumps(spec),
                datetime.now(timezone.utc).isoformat(),
                os.environ.get("PROVER_MODEL"),
                os.environ.get("SKEPTIC_MODEL"),
            ),
        )
        spec_id = cur.lastrowid

        for record in history:
            attempt_verdict = "round_passed" if record["round_passed"] else "bugs_found"
            cur.execute(
                "INSERT INTO attempts (spec_id, round, code, verdict) VALUES (?, ?, ?, ?)",
                (spec_id, record["round"], record["code"], attempt_verdict),
            )
            attempt_id = cur.lastrowid

            for result in record["results"]:
                valid = result.get("valid", True)
                cur.execute(
                    "INSERT INTO tests (attempt_id, test_code, passed, is_bug, is_valid, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id,
                        result["test_code"],
                        int(result["passed"]),
                        # is_bug means "real bug": failed AND judged a
                        # legitimate test, not just any raw failure.
                        int((not result["passed"]) and valid),
                        int(valid),
                        result.get("error"),
                    ),
                )

        conn.commit()
        return spec_id
    finally:
        conn.close()


def list_history(limit: int = 20, db_path: str = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                s.id,
                s.request,
                s.created_at,
                (SELECT a.verdict FROM attempts a
                 WHERE a.spec_id = s.id ORDER BY a.round DESC LIMIT 1) AS last_verdict,
                (SELECT COUNT(*) FROM attempts a WHERE a.spec_id = s.id) AS rounds,
                (SELECT COUNT(DISTINCT t.test_code)
                 FROM tests t JOIN attempts a ON t.attempt_id = a.id
                 WHERE a.spec_id = s.id AND t.is_bug = 1) AS bugs_caught
            FROM specs s
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_all_bugs(db_path: str = DB_PATH) -> list[dict]:
    """Every real bug ever caught, one row per distinct (spec, test) pair.

    Deliberately deduped within a spec (a bug that failed identically
    across several retry rounds is the SAME bug being caught once, not
    several) but NOT deduped across specs — the same kind of bug
    resurfacing on a different spec is exactly the recurrence
    nodes/memory.py's clustering is meant to surface.

    `error` isn't part of the grouping key: the sandbox embeds a random
    temp-dir path in every traceback, so the same test's error text can
    differ slightly across rounds even though it's the same bug. GROUP BY
    on (spec_id, test_code) with a bare `error` column is valid SQLite —
    it resolves to one representative row's value from the group, which
    is all a bug signature needs.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT a.spec_id AS spec_id, s.request AS request,
                   t.test_code AS test_code, t.error AS error
            FROM tests t
            JOIN attempts a ON t.attempt_id = a.id
            JOIN specs s ON a.spec_id = s.id
            WHERE t.is_bug = 1
            GROUP BY a.spec_id, t.test_code
            """
        )
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_requests_by_ids(spec_ids: list[int], db_path: str = DB_PATH) -> dict[int, str]:
    """Maps spec_id -> request text, for display purposes (e.g. showing
    which specs a bug pattern's examples actually came from)."""
    if not spec_ids:
        return {}
    conn = get_connection(db_path)
    try:
        placeholders = ",".join("?" for _ in spec_ids)
        cur = conn.execute(
            f"SELECT id, request FROM specs WHERE id IN ({placeholders})", spec_ids
        )
        return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def get_leaderboard(db_path: str = DB_PATH) -> list[dict]:
    """Aggregates every run by (prover_model, skeptic_model) — accumulates
    naturally as `breakpoint run` gets used with different .env settings
    across sessions, rather than needing a dedicated multi-pair runner
    script that would immediately hit OpenRouter's daily quota on its own.

    Runs from before this feature existed have NULL for both models —
    grouped under their own ("unknown", "unknown") row rather than
    guessed, since fabricating which models produced old data would be
    worse than admitting it isn't known.
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            WITH per_spec AS (
                SELECT
                    s.id AS spec_id,
                    COALESCE(s.prover_model, 'unknown') AS prover_model,
                    COALESCE(s.skeptic_model, 'unknown') AS skeptic_model,
                    (SELECT a.verdict FROM attempts a
                     WHERE a.spec_id = s.id ORDER BY a.round DESC LIMIT 1) AS last_verdict,
                    (SELECT COUNT(*) FROM attempts a WHERE a.spec_id = s.id) AS rounds,
                    (SELECT COUNT(DISTINCT t.test_code)
                     FROM tests t JOIN attempts a ON t.attempt_id = a.id
                     WHERE a.spec_id = s.id AND t.is_bug = 1) AS bugs_caught
                FROM specs s
            )
            SELECT
                prover_model,
                skeptic_model,
                COUNT(*) AS total_runs,
                SUM(CASE WHEN last_verdict = 'round_passed' THEN 1 ELSE 0 END) AS converged,
                SUM(bugs_caught) AS total_bugs_caught,
                ROUND(AVG(rounds), 2) AS avg_rounds
            FROM per_spec
            GROUP BY prover_model, skeptic_model
            ORDER BY total_runs DESC
            """
        )
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()
