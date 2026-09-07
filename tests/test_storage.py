"""Tests for storage.py's SQLite persistence — pure local I/O, no LLM
calls. Uses a temp file per test so tests never share or corrupt state."""

import os
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import storage


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_breakpoint.db")


def _history(round_passed_seq):
    """Build a fake history: one round per bool in round_passed_seq, with
    one test that fails on rounds where round_passed is False and passes
    once it's True."""
    history = []
    for i, passed in enumerate(round_passed_seq, start=1):
        history.append(
            {
                "round": i,
                "code": f"def f(): return {i}",
                "results": [
                    {"test_code": "def test_a(): pass", "passed": passed, "valid": True}
                ],
                "round_passed": passed,
            }
        )
    return history


def test_save_run_returns_a_spec_id(db_path):
    spec_id = storage.save_run("some request", {"function_name": "f"}, _history([True]), {}, db_path=db_path)
    assert isinstance(spec_id, int)


def test_save_run_persists_correct_row_counts(db_path):
    history = _history([False, True])
    storage.save_run("some request", {"function_name": "f"}, history, {}, db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM specs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM tests").fetchone()[0] == 2
    finally:
        conn.close()


def test_is_bug_true_only_for_failed_valid_tests(db_path):
    history = [
        {
            "round": 1,
            "code": "def f(): pass",
            "round_passed": False,
            "results": [
                {"test_code": "real_bug", "passed": False, "valid": True},
                {"test_code": "invalid_test", "passed": False, "valid": False},
                {"test_code": "passing_test", "passed": True, "valid": True},
            ],
        }
    ]
    storage.save_run("req", {}, history, {}, db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT test_code, is_bug, is_valid FROM tests").fetchall()
    finally:
        conn.close()

    by_code = {code: (is_bug, is_valid) for code, is_bug, is_valid in rows}
    assert by_code["real_bug"] == (1, 1)
    assert by_code["invalid_test"] == (0, 0)  # failed but invalid -> not a bug
    assert by_code["passing_test"] == (0, 1)


def test_list_history_bugs_caught_matches_raw_query(db_path):
    history = _history([False, False, True])
    storage.save_run("merge intervals", {}, history, {}, db_path=db_path)

    rows = storage.list_history(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["request"] == "merge intervals"
    assert rows[0]["rounds"] == 3
    # The same test_code failed in rounds 1 and 2 — deduped to 1 distinct bug.
    assert rows[0]["bugs_caught"] == 1
    assert rows[0]["last_verdict"] == "round_passed"


def test_list_history_orders_newest_first(db_path):
    storage.save_run("first request", {}, _history([True]), {}, db_path=db_path)
    storage.save_run("second request", {}, _history([True]), {}, db_path=db_path)

    rows = storage.list_history(db_path=db_path)
    assert rows[0]["request"] == "second request"
    assert rows[1]["request"] == "first request"


def test_list_history_respects_limit(db_path):
    for i in range(5):
        storage.save_run(f"request {i}", {}, _history([True]), {}, db_path=db_path)

    rows = storage.list_history(limit=2, db_path=db_path)
    assert len(rows) == 2


def test_get_connection_is_idempotent_and_migration_safe(db_path):
    # Calling get_connection twice (as save_run + list_history each do)
    # must not fail on "column already exists" from the is_valid or error
    # migrations.
    conn1 = storage.get_connection(db_path)
    conn1.close()
    conn2 = storage.get_connection(db_path)
    conn2.close()


def test_save_run_persists_error_message(db_path):
    history = [
        {
            "round": 1,
            "code": "def f(): pass",
            "round_passed": False,
            "results": [
                {"test_code": "def test_a(): pass", "passed": False, "valid": True, "error": "AssertionError: boom"}
            ],
        }
    ]
    storage.save_run("req", {}, history, {}, db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        error = conn.execute("SELECT error FROM tests").fetchone()[0]
    finally:
        conn.close()
    assert error == "AssertionError: boom"


def test_get_all_bugs_excludes_passing_and_invalid_tests(db_path):
    history = [
        {
            "round": 1,
            "code": "def f(): pass",
            "round_passed": False,
            "results": [
                {"test_code": "real_bug", "passed": False, "valid": True, "error": "e1"},
                {"test_code": "invalid_test", "passed": False, "valid": False, "error": "e2"},
                {"test_code": "passing_test", "passed": True, "valid": True, "error": None},
            ],
        }
    ]
    storage.save_run("req", {}, history, {}, db_path=db_path)

    bugs = storage.get_all_bugs(db_path=db_path)
    assert len(bugs) == 1
    assert bugs[0]["test_code"] == "real_bug"
    assert bugs[0]["error"] == "e1"
    assert bugs[0]["request"] == "req"


def test_get_all_bugs_dedupes_within_a_spec_but_not_across_specs(db_path):
    # Same test_code failing in rounds 1 and 2 of ONE run -> counted once.
    repeated_within_run = [
        {"round": 1, "code": "def f(): pass", "round_passed": False,
         "results": [{"test_code": "same_bug", "passed": False, "valid": True, "error": "e"}]},
        {"round": 2, "code": "def f(): pass", "round_passed": False,
         "results": [{"test_code": "same_bug", "passed": False, "valid": True, "error": "e (different tmp path)"}]},
    ]
    storage.save_run("spec A", {}, repeated_within_run, {}, db_path=db_path)

    # An identical-looking bug on a SEPARATE spec -> counted again, since
    # that's a genuine recurrence for the clustering system to notice.
    storage.save_run(
        "spec B", {},
        [{"round": 1, "code": "def g(): pass", "round_passed": False,
          "results": [{"test_code": "same_bug", "passed": False, "valid": True, "error": "e"}]}],
        {}, db_path=db_path,
    )

    bugs = storage.get_all_bugs(db_path=db_path)
    assert len(bugs) == 2
    assert {b["request"] for b in bugs} == {"spec A", "spec B"}


def test_get_all_bugs_empty_when_no_bugs_exist(db_path):
    storage.save_run("req", {}, _history([True]), {}, db_path=db_path)
    assert storage.get_all_bugs(db_path=db_path) == []


def test_get_requests_by_ids_returns_matching_map(db_path):
    id1 = storage.save_run("merge intervals", {}, _history([True]), {}, db_path=db_path)
    id2 = storage.save_run("two sum indices", {}, _history([True]), {}, db_path=db_path)

    result = storage.get_requests_by_ids([id1, id2], db_path=db_path)
    assert result == {id1: "merge intervals", id2: "two sum indices"}


def test_get_requests_by_ids_ignores_unknown_ids(db_path):
    id1 = storage.save_run("merge intervals", {}, _history([True]), {}, db_path=db_path)
    result = storage.get_requests_by_ids([id1, 9999], db_path=db_path)
    assert result == {id1: "merge intervals"}


def test_get_requests_by_ids_empty_list_returns_empty_dict(db_path):
    assert storage.get_requests_by_ids([], db_path=db_path) == {}
