"""Tests for dashboard.py using Flask's test client against a real,
disposable temp DB — no mocking of storage/memory, since these are pure
reads over data already proven correct in test_storage.py/test_memory.py.
No network, no LLM calls (a real embedding is used for the one test that
records a bug via memory.record_bug, same as test_memory_integration.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import storage
from dashboard import create_app


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_breakpoint.db")


@pytest.fixture
def client(db_path):
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    return app.test_client()


def _history(round_passed_seq, error="boom"):
    history = []
    for i, passed in enumerate(round_passed_seq, start=1):
        history.append(
            {
                "round": i,
                "code": f"def f(): return {i}",
                "round_passed": passed,
                "results": [
                    {
                        "test_code": "def test_a():\n    assert False",
                        "passed": passed,
                        "valid": True,
                        "error": None if passed else error,
                    }
                ],
            }
        )
    return history


def test_index_shows_empty_state_with_no_runs(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"No runs recorded yet" in response.data


def test_index_lists_a_recorded_run(client, db_path):
    storage.save_run("merge intervals", {}, _history([True]), {}, db_path=db_path)

    response = client.get("/")
    assert response.status_code == 200
    assert b"merge intervals" in response.data
    assert b"round_passed" in response.data


def test_run_detail_shows_full_trace(client, db_path):
    spec_id = storage.save_run(
        "merge intervals", {"function_name": "f"}, _history([False, True]), {}, db_path=db_path
    )

    response = client.get(f"/runs/{spec_id}")
    assert response.status_code == 200
    assert b"merge intervals" in response.data
    assert b"Round 1" in response.data
    assert b"Round 2" in response.data
    assert b"def f(): return 1" in response.data
    assert b"boom" in response.data  # the error from round 1's failure


def test_run_detail_404s_for_unknown_id(client):
    response = client.get("/runs/9999")
    assert response.status_code == 404


def test_patterns_page_shows_empty_state(client):
    response = client.get("/patterns")
    assert response.status_code == 200
    assert b"No bug patterns recorded yet" in response.data


def test_patterns_page_shows_a_real_recorded_pattern(client, db_path):
    import cli

    spec_id = storage.save_run(
        "merge intervals", {}, _history([False], error="TypeError: boom"), {}, db_path=db_path
    )
    cli._record_bugs_for_memory(
        spec_id,
        _history([False], error="TypeError: boom"),
        db_path=db_path,
    )

    response = client.get("/patterns")
    assert response.status_code == 200
    assert b"merge intervals" in response.data


def test_leaderboard_page_shows_empty_state(client):
    response = client.get("/leaderboard")
    assert response.status_code == 200
    assert b"No runs recorded yet" in response.data


def test_leaderboard_page_shows_model_pair(client, db_path, monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-x")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-y")
    storage.save_run("req", {}, _history([True]), {}, db_path=db_path)

    response = client.get("/leaderboard")
    assert response.status_code == 200
    assert b"prover-x" in response.data
    assert b"skeptic-y" in response.data
