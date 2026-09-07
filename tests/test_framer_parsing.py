"""Tests for nodes/framer.py's JSON-extraction logic — pure parsing, no
LLM calls. Cases are grounded in real malformed outputs seen in practice
(prose wrapped around JSON, fenced blocks, unfenced text)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from nodes.framer import _extract_json, _find_balanced_object


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
