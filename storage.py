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
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def save_run(request: str, spec: dict, history: list, report: dict, db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO specs (request, spec_json, created_at) VALUES (?, ?, ?)",
            (request, json.dumps(spec), datetime.now(timezone.utc).isoformat()),
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
                cur.execute(
                    "INSERT INTO tests (attempt_id, test_code, passed, is_bug) VALUES (?, ?, ?, ?)",
                    (
                        attempt_id,
                        result["test_code"],
                        int(result["passed"]),
                        int(not result["passed"]),
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
