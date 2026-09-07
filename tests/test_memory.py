"""Tests for memory.py's clustering logic — memory.embed is monkeypatched
with a deterministic fake so these run in milliseconds with no model load
and no network access. The real embedding model is exercised separately
in test_memory_integration.py."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import memory
import storage


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_breakpoint.db")


@pytest.fixture
def fake_embed(monkeypatch):
    # A tiny deterministic "embedding" scheme for testing cluster logic in
    # isolation: same text -> same vector; texts sharing a keyword score
    # high similarity; unrelated texts score low. Real semantic behavior
    # is verified against the actual model in test_memory_integration.py.
    vectors = {
        "none input: typeerror": [1.0, 0.0, 0.0],
        "empty list: typeerror": [0.95, 0.05, 0.0],  # similar to the above
        "reversed words: assertionerror": [0.0, 0.0, 1.0],  # unrelated
    }

    def fake(text):
        return vectors.get(text, [0.0, 1.0, 0.0])

    monkeypatch.setattr(memory, "embed", fake)
    return vectors


def test_bug_signature_combines_test_name_and_last_error_line():
    test_code = "def test_none_input():\n    assert f(None) == []\n"
    error = 'Traceback (most recent call last):\n  File "/tmp/x/run.py", line 3\nTypeError: boom'
    sig = memory._bug_signature(test_code, error)
    assert sig == "none input: TypeError: boom"


def test_bug_signature_handles_missing_error():
    sig = memory._bug_signature("def test_empty_list():\n    pass", None)
    assert sig == "empty list"


def test_humanize_test_name():
    assert memory._humanize_test_name("def test_none_input():\n    pass") == "none input"
    assert memory._humanize_test_name("not a test function") == "unlabeled bug"


def test_record_bug_creates_new_pattern_when_none_exist(db_path, fake_embed, monkeypatch):
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "none input: typeerror")
    pattern_id = memory.record_bug(spec_id=1, test_code="def test_none_input():\n    pass", error="e", db_path=db_path)

    patterns = memory.list_patterns(db_path=db_path)
    assert len(patterns) == 1
    assert patterns[0]["id"] == pattern_id
    assert patterns[0]["frequency"] == 1
    assert patterns[0]["example_spec_ids"] == [1]


def test_record_bug_matches_similar_signature_to_existing_pattern(db_path, fake_embed, monkeypatch):
    sigs = iter(["none input: typeerror", "empty list: typeerror"])
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: next(sigs))

    memory.record_bug(spec_id=1, test_code="def test_none_input():\n    pass", error="e", db_path=db_path)
    memory.record_bug(spec_id=2, test_code="def test_empty_list():\n    pass", error="e", db_path=db_path)

    patterns = memory.list_patterns(db_path=db_path)
    assert len(patterns) == 1  # matched, not a new cluster
    assert patterns[0]["frequency"] == 2
    assert set(patterns[0]["example_spec_ids"]) == {1, 2}


def test_record_bug_creates_separate_pattern_for_dissimilar_signature(db_path, fake_embed, monkeypatch):
    sigs = iter(["none input: typeerror", "reversed words: assertionerror"])
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: next(sigs))

    memory.record_bug(spec_id=1, test_code="def test_none_input():\n    pass", error="e", db_path=db_path)
    memory.record_bug(spec_id=2, test_code="def test_reversed_words():\n    pass", error="e", db_path=db_path)

    patterns = memory.list_patterns(db_path=db_path)
    assert len(patterns) == 2


def test_record_bug_does_not_duplicate_spec_id_in_example_list(db_path, fake_embed, monkeypatch):
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "none input: typeerror")

    memory.record_bug(spec_id=1, test_code="def test_a():\n    pass", error="e", db_path=db_path)
    memory.record_bug(spec_id=1, test_code="def test_b():\n    pass", error="e", db_path=db_path)

    patterns = memory.list_patterns(db_path=db_path)
    assert patterns[0]["example_spec_ids"] == [1]
    assert patterns[0]["frequency"] == 2  # frequency still counts each occurrence


def test_list_patterns_orders_by_frequency_descending(db_path, fake_embed, monkeypatch):
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "reversed words: assertionerror")
    memory.record_bug(spec_id=1, test_code="def test_a():\n    pass", error="e", db_path=db_path)

    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "none input: typeerror")
    memory.record_bug(spec_id=2, test_code="def test_b():\n    pass", error="e", db_path=db_path)
    memory.record_bug(spec_id=3, test_code="def test_c():\n    pass", error="e", db_path=db_path)

    patterns = memory.list_patterns(db_path=db_path)
    assert patterns[0]["frequency"] == 2
    assert patterns[1]["frequency"] == 1


def test_pack_unpack_roundtrip():
    vector = [0.1, -0.2, 0.3, 0.0]
    assert memory._unpack(memory._pack(vector)) == pytest.approx(vector)


def test_cosine_of_identical_vectors_is_one():
    assert memory._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero():
    assert memory._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_patterns_by_prover_model_attributes_bugs_to_the_model_that_wrote_them(
    db_path, fake_embed, monkeypatch
):
    # Two specs from prover-a hit the "none input" bug; one spec from
    # prover-b hits a genuinely different bug.
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "none input: typeerror")
    memory.record_bug(spec_id=1, test_code="def test_none_input():\n    pass", error="e", db_path=db_path)
    memory.record_bug(spec_id=2, test_code="def test_none_input():\n    pass", error="e", db_path=db_path)

    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "reversed words: assertionerror")
    memory.record_bug(spec_id=3, test_code="def test_reversed_words():\n    pass", error="e", db_path=db_path)

    # record_bug() only needs a spec_id, not a real spec row, so create
    # minimal ones directly (save_run() normally does this as part of a
    # real run) to attach a prover_model for get_prover_models_by_ids to find.
    conn = storage.get_connection(db_path)
    for sid in (1, 2, 3):
        conn.execute(
            "INSERT OR IGNORE INTO specs (id, request, spec_json, created_at) VALUES (?, 'r', '{}', 'now')",
            (sid,),
        )
    conn.execute("UPDATE specs SET prover_model = 'prover-a' WHERE id IN (1, 2)")
    conn.execute("UPDATE specs SET prover_model = 'prover-b' WHERE id = 3")
    conn.commit()
    conn.close()

    breakdown = memory.patterns_by_prover_model(db_path=db_path)

    assert breakdown["prover-a"][0]["description"] == "none input"
    assert breakdown["prover-a"][0]["count"] == 2
    assert breakdown["prover-b"][0]["description"] == "reversed words"
    assert breakdown["prover-b"][0]["count"] == 1


def test_patterns_by_prover_model_uses_unknown_for_legacy_specs(db_path, fake_embed, monkeypatch):
    monkeypatch.setattr(memory, "_bug_signature", lambda t, e: "none input: typeerror")
    memory.record_bug(spec_id=1, test_code="def test_none_input():\n    pass", error="e", db_path=db_path)
    # No spec row at all for id=1 -> get_prover_models_by_ids has nothing
    # to find, defaulting to "unknown" rather than crashing.

    breakdown = memory.patterns_by_prover_model(db_path=db_path)
    assert "unknown" in breakdown
    assert breakdown["unknown"][0]["description"] == "none input"


def test_patterns_by_prover_model_empty_when_no_bugs(db_path, fake_embed):
    assert memory.patterns_by_prover_model(db_path=db_path) == {}
