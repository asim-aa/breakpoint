"""Tests for nodes/prover.py's code-extraction logic — pure parsing, no
LLM calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.prover import _extract_code


def test_extracts_code_from_python_fence():
    text = "```python\ndef f(x):\n    return x\n```"
    assert _extract_code(text) == "def f(x):\n    return x"


def test_extracts_code_from_bare_fence():
    text = "```\ndef f(x):\n    return x\n```"
    assert _extract_code(text) == "def f(x):\n    return x"


def test_extracts_code_with_leading_prose_no_fence():
    # A real failure mode: a model explains its reasoning, then writes code
    # with no markdown fence at all.
    text = (
        "Looking at this spec, I need to implement a merge function.\n\n"
        "def merge(a, b):\n    return a + b"
    )
    result = _extract_code(text)
    assert result.startswith("def merge(a, b):")
    assert "Looking at this spec" not in result


def test_extracts_code_starting_with_import():
    text = "Here's my solution:\n\nimport re\ndef f(x):\n    return re.sub('a', 'b', x)"
    result = _extract_code(text)
    assert result.startswith("import re")


def test_returns_text_unchanged_if_no_def_or_fence_found():
    # Degenerate case: nothing recognizable as code. Should not crash, just
    # pass the raw text through for the caller (compile() check) to reject.
    text = "I refuse to answer this request."
    assert _extract_code(text) == text


def test_extracts_code_from_javascript_fence():
    # A real bug this generalization fixes: the old regex only recognized
    # the literal tag "python" — any other fence tag (e.g. "javascript")
    # fell through to matching zero characters for the tag, then captured
    # the tag word ITSELF as part of the code.
    text = "```javascript\nfunction f(x) {\n  return x;\n}\n```"
    result = _extract_code(text, language="javascript")
    assert result == "function f(x) {\n  return x;\n}"
    assert "javascript" not in result


def test_extracts_code_with_leading_prose_no_fence_javascript():
    text = (
        "Here's my implementation:\n\n"
        "function merge(a, b) {\n  return a.concat(b);\n}"
    )
    result = _extract_code(text, language="javascript")
    assert result.startswith("function merge(a, b)")
    assert "Here's my implementation" not in result
