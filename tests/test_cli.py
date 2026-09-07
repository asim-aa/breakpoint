"""Tests for cli.py's pure logic — the bug-extraction step that feeds
memory.record_bug, mocked so no OpenRouter or model calls happen. The
actual `breakpoint run`/`breakpoint history` commands need a real graph
invocation and aren't covered here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli


def test_record_bugs_for_memory_dedupes_within_a_run(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.memory, "record_bug", lambda **kwargs: calls.append(kwargs))

    history = [
        {"round": 1, "results": [{"test_code": "same_bug", "passed": False, "valid": True, "error": "e1"}]},
        {"round": 2, "results": [{"test_code": "same_bug", "passed": False, "valid": True, "error": "e2 (diff tmp path)"}]},
    ]
    cli._record_bugs_for_memory(spec_id=1, history=history)

    assert len(calls) == 1
    assert calls[0]["test_code"] == "same_bug"
    assert calls[0]["error"] == "e1"  # first occurrence's error is kept


def test_record_bugs_for_memory_skips_invalid_and_passing_tests(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.memory, "record_bug", lambda **kwargs: calls.append(kwargs))

    history = [
        {
            "round": 1,
            "results": [
                {"test_code": "real_bug", "passed": False, "valid": True, "error": "e"},
                {"test_code": "invalid_test", "passed": False, "valid": False, "error": "e"},
                {"test_code": "passing_test", "passed": True, "valid": True, "error": None},
            ],
        }
    ]
    cli._record_bugs_for_memory(spec_id=1, history=history)

    assert len(calls) == 1
    assert calls[0]["test_code"] == "real_bug"


def test_record_bugs_for_memory_does_nothing_when_no_bugs(monkeypatch):
    load_attempted = []
    monkeypatch.setattr(cli.memory, "record_bug", lambda **kwargs: load_attempted.append(True))

    history = [{"round": 1, "results": [{"test_code": "t", "passed": True, "valid": True, "error": None}]}]
    cli._record_bugs_for_memory(spec_id=1, history=history)

    assert load_attempted == []  # never even called, so no model load triggered


def test_record_bugs_for_memory_survives_a_memory_failure(monkeypatch, capsys):
    def raise_error(**kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(cli.memory, "record_bug", raise_error)

    history = [{"round": 1, "results": [{"test_code": "t", "passed": False, "valid": True, "error": "e"}]}]
    cli._record_bugs_for_memory(spec_id=1, history=history)  # must not raise

    captured = capsys.readouterr()
    assert "skipped" in captured.out
