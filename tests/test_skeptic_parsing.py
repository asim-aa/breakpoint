"""Tests for nodes/skeptic.py's test-extraction logic — pure parsing, no
LLM calls. Several cases here reproduce real malformed Skeptic output
encountered during development (see README.md's "Real bugs found and
fixed" section)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from nodes.skeptic import _extract_json_array, _find_balanced_array, _split_multi_def_tests


def test_extracts_clean_array():
    text = json.dumps(["def test_a():\n    assert True", "def test_b():\n    assert True"])
    result = _extract_json_array(text)
    assert len(result) == 2


def test_extracts_array_from_markdown_fence():
    inner = json.dumps(["def test_a():\n    assert True"])
    text = f"```json\n{inner}\n```"
    result = _extract_json_array(text)
    assert len(result) == 1


def test_unescapes_over_escaped_newlines():
    # Real bug found live: a model emitted a literal two-character "\n"
    # sequence (over-escaped) inside a JSON string instead of a real
    # newline, producing invalid Python that failed identically on every
    # test. json.dumps of a string containing a literal backslash-n
    # reproduces exactly that malformed shape.
    raw_test_source = "def test_empty():\\n    assert f([]) == []"
    text = json.dumps([raw_test_source])
    result = _extract_json_array(text)
    assert "\n" in result[0]
    assert "\\n" not in result[0]
    # The unescaped result must be valid, compilable Python.
    compile(result[0], "<test>", "exec")


def test_does_not_mangle_correctly_escaped_newlines():
    # A normal, correctly-escaped multi-line test should pass through with
    # real newlines intact, not be double-processed.
    tests = ["def test_a():\n    assert True\n    assert 1 == 1"]
    text = json.dumps(tests)
    result = _extract_json_array(text)
    assert result[0] == tests[0]


def test_splits_multiple_defs_bundled_in_one_entry():
    # Real bug found live: the Skeptic sometimes bundles several
    # `def test_...` functions into a single JSON array entry despite
    # instructions to write one per entry. This contaminates pass/fail
    # attribution since the sandbox auto-discovers every test_* function.
    bundled = (
        "def test_empty_list():\n"
        "    assert f([]) is None\n\n"
        "def test_negative_numbers():\n"
        "    assert f([-3, 4, 7, -1]) == [0, 3]\n"
    )
    result = _split_multi_def_tests([bundled])
    assert len(result) == 2
    assert result[0].startswith("def test_empty_list")
    assert result[1].startswith("def test_negative_numbers")
    assert "test_negative_numbers" not in result[0]


def test_leaves_single_def_entries_unsplit():
    single = "def test_single():\n    assert True"
    result = _split_multi_def_tests([single])
    assert result == [single]


def test_find_balanced_array_ignores_brackets_inside_strings():
    text = '["def test_a():\\n    assert x[0] == 1"]'
    result = _find_balanced_array(text)
    assert result == text


def test_skips_non_string_entries_in_the_array():
    # Real failure observed live: despite the "list of strings"
    # instruction, a model emitted a stray non-string element (a bare
    # int) alongside otherwise-valid test strings. One malformed entry
    # shouldn't crash extraction or lose the real tests around it.
    text = json.dumps(["def test_a():\n    assert True", 42, "def test_b():\n    assert True"])
    result = _extract_json_array(text)
    assert result == ["def test_a():\n    assert True", "def test_b():\n    assert True"]


def test_raises_value_error_when_no_array_present():
    with pytest.raises(ValueError):
        _extract_json_array("I cannot generate tests for this.")


def test_recovers_plain_javascript_source_when_json_array_instruction_ignored():
    # Real failure captured live: the Skeptic model ignored the "respond
    # with a JSON array of strings" instruction entirely and just wrote a
    # plain JS test file with a `//` comment before each function — no
    # brackets, no quotes, nothing json.loads or the balanced-array finder
    # can work with. Previously this raised and crashed the whole run;
    # now it's recovered via the same multi-def splitter used for a
    # bundled JSON entry.
    raw = (
        "// Test 1: empty string returns empty string\n"
        "function test_emptyStringReturnsEmpty() {\n"
        "  assert.strictEqual(f(''), '');\n"
        "}\n\n"
        "// Test 2: single word with no spaces\n"
        "function test_singleWordNoSpaces() {\n"
        "  assert.strictEqual(f('hello'), 'olleh');\n"
        "}\n"
    )
    result = _extract_json_array(raw, language="javascript")
    assert len(result) == 2
    assert result[0].startswith("function test_emptyStringReturnsEmpty")
    assert result[1].startswith("function test_singleWordNoSpaces")
    assert "test_singleWordNoSpaces" not in result[0]


def test_splits_multiple_javascript_defs_bundled_in_one_entry():
    bundled = (
        "function test_empty_list() {\n"
        "  assert.strictEqual(f([]), null);\n"
        "}\n\n"
        "function test_negative_numbers() {\n"
        "  assert.deepStrictEqual(f([-3, 4, 7, -1]), [0, 3]);\n"
        "}\n"
    )
    result = _split_multi_def_tests([bundled], language="javascript")
    assert len(result) == 2
    assert result[0].startswith("function test_empty_list")
    assert result[1].startswith("function test_negative_numbers")
    assert "test_negative_numbers" not in result[0]


def test_leaves_single_javascript_def_entry_unsplit():
    single = "function test_single() {\n  assert.ok(true);\n}\n"
    result = _split_multi_def_tests([single], language="javascript")
    assert result == [single]
