"""Tests for nodes/framer.py's JSON-extraction logic — pure parsing, no
LLM calls. Cases are grounded in real malformed outputs seen in practice
(prose wrapped around JSON, fenced blocks, unfenced text)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import nodes.framer as framer
from nodes.framer import _extract_json, _find_balanced_object, infer_spec_from_code


def test_extracts_clean_json():
    text = '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'
    result = _extract_json(text)
    assert result["function_name"] == "f"


def test_extracts_json_from_markdown_fence():
    text = '```json\n{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}\n```'
    result = _extract_json(text)
    assert result["function_name"] == "f"


def test_extracts_json_with_leading_prose():
    # A real failure mode: models sometimes preface JSON with an explanation
    # despite instructions not to.
    text = (
        "Sure, here is the spec you asked for:\n\n"
        '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'
    )
    result = _extract_json(text)
    assert result["function_name"] == "f"


def test_extracts_json_with_trailing_prose():
    text = (
        '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'
        "\n\nLet me know if you need anything else!"
    )
    result = _extract_json(text)
    assert result["function_name"] == "f"


def test_ignores_braces_inside_string_values():
    # A description field containing literal { } characters shouldn't
    # confuse the balanced-brace scanner into stopping early.
    text = '{"function_name": "f", "inputs": [], "output": {}, "constraints": ["uses {curly} braces"], "examples": []}'
    result = _extract_json(text)
    assert result["constraints"] == ["uses {curly} braces"]


def test_raises_value_error_when_no_json_present():
    with pytest.raises(ValueError):
        _extract_json("I cannot produce a spec for this request.")


def test_find_balanced_object_returns_none_with_no_brace():
    assert _find_balanced_object("no braces here") is None


def test_find_balanced_object_handles_escaped_quotes():
    text = '{"a": "value with \\" an escaped quote and a } brace"}'
    result = _find_balanced_object(text)
    assert result == text


def test_infer_spec_from_code_includes_code_in_prompt(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    captured = {}

    def fake_complete(prompt, model, system=None):
        captured["prompt"] = prompt
        captured["system"] = system
        return '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'

    monkeypatch.setattr(framer, "complete", fake_complete)

    infer_spec_from_code("def f(x):\n    return x")
    assert "def f(x):" in captured["prompt"]
    assert "EXISTING Python function" in captured["system"]


def test_infer_spec_from_code_includes_context_when_given(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    captured = {}

    def fake_complete(prompt, model, system=None):
        captured["prompt"] = prompt
        return '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'

    monkeypatch.setattr(framer, "complete", fake_complete)

    infer_spec_from_code("def f(x):\n    return x", context="Fixes a bug in list handling")
    assert "Fixes a bug in list handling" in captured["prompt"]


def test_infer_spec_from_code_omits_context_section_when_not_given(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    captured = {}

    def fake_complete(prompt, model, system=None):
        captured["prompt"] = prompt
        return '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'

    monkeypatch.setattr(framer, "complete", fake_complete)

    infer_spec_from_code("def f(x):\n    return x")
    assert "Additional context" not in captured["prompt"]


def test_infer_spec_from_code_retries_on_malformed_json(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    responses = iter(["not json", '{"function_name": "f", "inputs": [], "output": {}, "constraints": [], "examples": []}'])
    monkeypatch.setattr(framer, "complete", lambda prompt, model, system=None: next(responses))

    result = infer_spec_from_code("def f(x):\n    return x", retries=2)
    assert result["function_name"] == "f"


def test_infer_spec_from_code_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    monkeypatch.setattr(framer, "complete", lambda prompt, model, system=None: "still not json")

    with pytest.raises(ValueError):
        infer_spec_from_code("def f(x):\n    return x", retries=1)
