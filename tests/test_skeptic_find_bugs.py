"""Tests for nodes/skeptic.py's find_bugs — the LLM-calling half, with
nodes.skeptic.complete monkeypatched so this runs for $0 with no network
access. Covers a live failure: a reasoning Skeptic model can burn its
whole token budget and make complete() raise RuntimeError instead of
returning text — this used to crash find_bugs immediately instead of
retrying, which is exactly what took down a live GitHub Action run."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import nodes.skeptic as skeptic
from nodes.skeptic import find_bugs


def _env(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "prover-model")
    monkeypatch.setenv("SKEPTIC_MODEL", "skeptic-model")


def test_find_bugs_passes_a_wide_max_tokens_budget(monkeypatch):
    # The live bug: the default max_tokens (4000) wasn't enough for a
    # reasoning model verifying a real (non-toy) file.
    _env(monkeypatch)
    captured = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps(["def test_a():\n    assert True"])

    monkeypatch.setattr(skeptic, "complete", fake_complete)
    find_bugs(spec={"function_name": "f"}, code="def f(x): return x")
    assert captured["max_tokens"] >= 8000


def test_find_bugs_retries_after_empty_content_runtime_error(monkeypatch):
    calls = []

    def flaky_complete(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(
                "Model skeptic-model returned empty content (finish_reason='length')."
            )
        return json.dumps(["def test_a():\n    assert True"])

    _env(monkeypatch)
    monkeypatch.setattr(skeptic, "complete", flaky_complete)

    result = find_bugs(spec={"function_name": "f"}, code="def f(x): return x")
    assert len(calls) == 2  # first call failed, retry succeeded
    assert result == ["def test_a():\n    assert True"]


def test_find_bugs_raises_after_exhausting_retries_on_persistent_provider_error(monkeypatch):
    _env(monkeypatch)

    def always_fails(**kwargs):
        raise RuntimeError("Model skeptic-model returned empty content (finish_reason='length').")

    monkeypatch.setattr(skeptic, "complete", always_fails)

    with pytest.raises(RuntimeError, match="finish_reason"):
        find_bugs(spec={"function_name": "f"}, code="def f(x): return x", retries=1)
