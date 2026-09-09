"""Tests for nodes/prover.py's prove() retry-on-invalid-syntax loop.
nodes.prover.complete is monkeypatched (no LLM call, no network) but the
syntax checks themselves are real: compile() for Python, and `node
--check` for JavaScript — this is exactly the check that would have
caught a real failure observed live (a reasoning model leaking pure
prose into the code field, with zero actual JavaScript in it)."""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import nodes.prover as prover
from nodes.prover import prove

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _env(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")


def test_returns_immediately_on_valid_python(monkeypatch):
    _env(monkeypatch)
    calls = []

    def fake_complete(**kwargs):
        calls.append(1)
        return "```python\ndef f(x):\n    return x\n```"

    monkeypatch.setattr(prover, "complete", fake_complete)
    code = prove(spec={"function_name": "f"})
    assert code == "def f(x):\n    return x"
    assert len(calls) == 1


def test_retries_on_invalid_python_syntax(monkeypatch):
    _env(monkeypatch)
    responses = iter(
        [
            "```python\ndef f(x:\n    return x\n```",  # unclosed paren — invalid syntax
            "```python\ndef f(x):\n    return x\n```",
        ]
    )
    monkeypatch.setattr(prover, "complete", lambda **kwargs: next(responses))
    code = prove(spec={"function_name": "f"}, retries=2)
    assert code == "def f(x):\n    return x"


def test_returns_last_attempt_after_exhausting_retries_on_python(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(
        prover, "complete", lambda **kwargs: "```python\ndef f(x:\n    return x\n```"
    )
    code = prove(spec={"function_name": "f"}, retries=1)
    assert "def f(x:" in code  # invalid syntax returned as-is, not raised


@requires_node
def test_returns_immediately_on_valid_javascript(monkeypatch):
    _env(monkeypatch)
    calls = []

    def fake_complete(**kwargs):
        calls.append(1)
        return "```javascript\nfunction f(x) {\n  return x;\n}\n```"

    monkeypatch.setattr(prover, "complete", fake_complete)
    code = prove(spec={"function_name": "f"}, language="javascript")
    assert code == "function f(x) {\n  return x;\n}"
    assert len(calls) == 1


@requires_node
def test_retries_on_reasoning_prose_leaked_instead_of_javascript_code(monkeypatch):
    # The exact real failure observed live: no fence, no recognizable
    # code start — just prose reasoning about how to fix the previous
    # failure, with zero actual JavaScript.
    _env(monkeypatch)
    responses = iter(
        [
            "We need to reconsider the whitespace handling here. Let's think "
            "step by step about what the test expects versus what we return...",
            "```javascript\nfunction f(x) {\n  return x;\n}\n```",
        ]
    )
    monkeypatch.setattr(prover, "complete", lambda **kwargs: next(responses))
    code = prove(spec={"function_name": "f"}, retries=2, language="javascript")
    assert code == "function f(x) {\n  return x;\n}"


@requires_node
def test_returns_last_attempt_after_exhausting_retries_on_javascript(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(
        prover, "complete", lambda **kwargs: "This is just an explanation, no code at all."
    )
    code = prove(spec={"function_name": "f"}, retries=1, language="javascript")
    assert "explanation" in code  # invalid/non-code returned as-is, not raised
