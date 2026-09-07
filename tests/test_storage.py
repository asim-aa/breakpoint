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


def _history_with_bug(rounds_and_pass):
    """Like _history() but each round's single test has a real bug (fails)
    until the round marked True, matching what a genuine multi-round run
    looks like for leaderboard aggregation."""
    history = []
    for i, passed in enumerate(rounds_and_pass, start=1):
        history.append(
            {
                "round": i,
                "code": f"def f(): return {i}",
                "results": [{"test_code": "def test_a(): pass", "passed": passed, "valid": True}],
                "round_passed": passed,
            }
        )
    return history


def test_save_run_records_model_pair_from_environment(db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-x")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-y")
    storage.save_run("req", {}, _history([True]), {}, db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT prover_model, skeptic_model FROM specs").fetchone()
    finally:
        conn.close()
    assert row == ("prover-x", "skeptic-y")


def test_save_run_records_none_when_env_vars_unset(db_path, monkeypatch):
    monkeypatch.delenv("PROVER_MODEL", raising=False)
    monkeypatch.delenv("SKEPTIC_MODEL", raising=False)
    storage.save_run("req", {}, _history([True]), {}, db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT prover_model, skeptic_model FROM specs").fetchone()
    finally:
        conn.close()
    assert row == (None, None)


def test_get_leaderboard_groups_by_model_pair(db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-a")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-a")
    storage.save_run("req1", {}, _history_with_bug([False, True]), {}, db_path=db_path)  # 2 rounds, converged
    storage.save_run("req2", {}, _history_with_bug([True]), {}, db_path=db_path)  # 1 round, converged

    monkeypatch.setenv("PROVER_MODEL", "prover-b")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-b")
    storage.save_run("req3", {}, _history_with_bug([False, False]), {}, db_path=db_path)  # unresolved

    board = storage.get_leaderboard(db_path=db_path)
    assert len(board) == 2

    by_pair = {(row["prover_model"], row["skeptic_model"]): row for row in board}
    a = by_pair[("prover-a", "skeptic-a")]
    assert a["total_runs"] == 2
    assert a["converged"] == 2
    assert a["avg_rounds"] == 1.5

    b = by_pair[("prover-b", "skeptic-b")]
    assert b["total_runs"] == 1
    assert b["converged"] == 0


def test_get_leaderboard_groups_legacy_null_rows_as_unknown(db_path, monkeypatch):
    monkeypatch.delenv("PROVER_MODEL", raising=False)
    monkeypatch.delenv("SKEPTIC_MODEL", raising=False)
    storage.save_run("legacy req", {}, _history([True]), {}, db_path=db_path)

    board = storage.get_leaderboard(db_path=db_path)
    assert len(board) == 1
    assert board[0]["prover_model"] == "unknown"
    assert board[0]["skeptic_model"] == "unknown"


def test_get_leaderboard_counts_distinct_bugs_per_pair(db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-a")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-a")
    history = [
        {
            "round": 1, "code": "def f(): pass", "round_passed": False,
            "results": [
                {"test_code": "bug1", "passed": False, "valid": True},
                {"test_code": "bug2", "passed": False, "valid": True},
            ],
        }
    ]
    storage.save_run("req", {}, history, {}, db_path=db_path)

    board = storage.get_leaderboard(db_path=db_path)
    assert board[0]["total_bugs_caught"] == 2


def test_get_leaderboard_orders_by_total_runs_descending(db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-rare")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-rare")
    storage.save_run("req1", {}, _history([True]), {}, db_path=db_path)

    monkeypatch.setenv("PROVER_MODEL", "prover-common")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-common")
    storage.save_run("req2", {}, _history([True]), {}, db_path=db_path)
    storage.save_run("req3", {}, _history([True]), {}, db_path=db_path)

    board = storage.get_leaderboard(db_path=db_path)
    assert board[0]["prover_model"] == "prover-common"
    assert board[0]["total_runs"] == 2


def test_get_run_detail_returns_none_for_unknown_spec(db_path):
    assert storage.get_run_detail(9999, db_path=db_path) is None


def test_get_run_detail_returns_full_trace(db_path):
    history = [
        {
            "round": 1, "code": "def f(): return None", "round_passed": False,
            "results": [{"test_code": "def test_a(): pass", "passed": False, "valid": True, "error": "boom"}],
        },
        {
            "round": 2, "code": "def f(): return []", "round_passed": True,
            "results": [{"test_code": "def test_a(): pass", "passed": True, "valid": True, "error": None}],
        },
    ]
    spec_id = storage.save_run("merge intervals", {"function_name": "f"}, history, {}, db_path=db_path)

    detail = storage.get_run_detail(spec_id, db_path=db_path)
    assert detail["request"] == "merge intervals"
    assert detail["id"] == spec_id
    assert len(detail["attempts"]) == 2
    assert detail["attempts"][0]["round"] == 1
    assert detail["attempts"][0]["code"] == "def f(): return None"
    assert detail["attempts"][0]["tests"][0]["passed"] is False
    assert detail["attempts"][0]["tests"][0]["error"] == "boom"
    assert detail["attempts"][1]["verdict"] == "round_passed"


def test_get_run_detail_includes_model_pair(db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-x")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-y")
    spec_id = storage.save_run("req", {}, _history([True]), {}, db_path=db_path)

    detail = storage.get_run_detail(spec_id, db_path=db_path)
    assert detail["prover_model"] == "prover-x"
    assert detail["skeptic_model"] == "skeptic-y"


def test_get_prover_models_by_ids_returns_matching_map(db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-a")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-a")
    id1 = storage.save_run("req1", {}, _history([True]), {}, db_path=db_path)

    monkeypatch.setenv("PROVER_MODEL", "prover-b")
    id2 = storage.save_run("req2", {}, _history([True]), {}, db_path=db_path)

    result = storage.get_prover_models_by_ids([id1, id2], db_path=db_path)
    assert result == {id1: "prover-a", id2: "prover-b"}


def test_get_prover_models_by_ids_maps_legacy_null_to_unknown(db_path, monkeypatch):
    monkeypatch.delenv("PROVER_MODEL", raising=False)
    monkeypatch.delenv("SKEPTIC_MODEL", raising=False)
    spec_id = storage.save_run("req", {}, _history([True]), {}, db_path=db_path)

    result = storage.get_prover_models_by_ids([spec_id], db_path=db_path)
    assert result[spec_id] == "unknown"


def test_get_prover_models_by_ids_empty_list_returns_empty_dict(db_path):
    assert storage.get_prover_models_by_ids([], db_path=db_path) == {}
