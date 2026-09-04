"""Generates adversarial tests aimed at breaking the Prover's implementation."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

SYSTEM_PROMPT = """You are the Skeptic in an adversarial code-generation system.
You are given a spec and an implementation. Your job is to try to BREAK the
implementation, not to confirm it works. Write pytest-style test functions
targeting: empty/null input, boundary values, unsorted or malformed input,
type edge cases, and anything the spec's constraints imply but the
implementation might not actually handle.

Each test must be a self-contained Python function named test_<something>
that calls the function under test directly (it is already defined in the
same file — do not import it) and uses a bare `assert`.

Respond with STRICT JSON: a list of strings, where each string is one
complete test function's source code, using REAL newline characters inside
each string (a single backslash followed by n, standard JSON escaping) —
not a literal backslash-backslash-n. Output ONLY the JSON array, no
markdown fences, no commentary."""


def _find_balanced_array(text: str) -> str | None:
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json_array(text: str) -> list:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    try:
        tests = json.loads(text)
    except json.JSONDecodeError:
        balanced = _find_balanced_array(text)
        if balanced is None:
            raise ValueError(f"Could not find a JSON array in Skeptic output: {text!r}")
        tests = json.loads(balanced)

    # Models sometimes over-escape newlines inside the JSON string values
    # (emitting a literal "\n" two-character sequence rather than a real
    # line break), which produces syntactically invalid Python. Detect that
    # case per-string and unescape it.
    fixed = []
    for t in tests:
        if "\n" not in t and "\\n" in t:
            t = t.encode().decode("unicode_escape")
        fixed.append(t)
    return _split_multi_def_tests(fixed)


def _split_multi_def_tests(tests: list[str]) -> list[str]:
    # Despite instructions to write one self-contained function per array
    # entry, models sometimes bundle several `def test_...` functions into
    # a single string. The sandbox harness auto-discovers every test_*
    # function in a run, so an unsplit blob makes one failing sub-test mask
    # or misattribute the result of the others sharing that entry. Split any
    # entry with more than one top-level test def into separate entries.
    split = []
    for t in tests:
        starts = [m.start() for m in re.finditer(r"(?m)^def test_\w+\s*\(", t)]
        if len(starts) <= 1:
            split.append(t)
            continue
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(t)
            split.append(t[start:end].rstrip())
    return split


def find_bugs(spec: dict, code: str, retries: int = 2) -> list[str]:
    prover_model = os.environ["PROVER_MODEL"]
    skeptic_model = os.environ["SKEPTIC_MODEL"]
    assert prover_model != skeptic_model, (
        "PROVER_MODEL and SKEPTIC_MODEL must differ — the adversarial "
        "pressure depends on genuinely different blind spots."
    )

    prompt = f"Spec:\n{json.dumps(spec, indent=2)}\n\nImplementation:\n{code}"
    last_error = None
    for _ in range(retries + 1):
        raw = complete(prompt=prompt, model=skeptic_model, system=SYSTEM_PROMPT)
        try:
            return _extract_json_array(raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
    raise last_error
