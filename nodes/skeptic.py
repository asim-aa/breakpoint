"""Generates adversarial tests aimed at breaking the Prover's implementation."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

SYSTEM_PROMPTS = {
    "python": """You are the Skeptic in an adversarial code-generation system.
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
markdown fences, no commentary.""",
    "javascript": """You are the Skeptic in an adversarial code-generation system.
You are given a spec and an implementation. Your job is to try to BREAK the
implementation, not to confirm it works. Write test functions targeting:
empty/null input, boundary values, unsorted or malformed input, type edge
cases, and anything the spec's constraints imply but the implementation
might not actually handle.

Each test must be a self-contained, plain top-level JavaScript function
declaration (`function test_<something>() { ... }` — not an arrow
function, not async) that calls the function under test directly (it is
already defined in the same file — do not require/import it) and asserts
using Node's built-in `assert` module (`assert.strictEqual`,
`assert.deepStrictEqual`, `assert.ok`, etc.) — `assert` is already required
at the top of the file, do not require it yourself.

Respond with STRICT JSON: a list of strings, where each string is one
complete test function's source code, using REAL newline characters inside
each string (a single backslash followed by n, standard JSON escaping) —
not a literal backslash-backslash-n. Output ONLY the JSON array, no
markdown fences, no commentary.""",
}

_TEST_DEF_START = {
    "python": re.compile(r"(?m)^def test_\w+\s*\("),
    "javascript": re.compile(r"(?m)^function test_\w+\s*\("),
}


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


def _extract_json_array(text: str, language: str = "python") -> list:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    try:
        tests = json.loads(text)
    except json.JSONDecodeError:
        balanced = _find_balanced_array(text)
        if balanced is None:
            # Real failure observed live (specifically with JS output):
            # some models ignore the "wrap in a JSON array of strings"
            # instruction entirely and just write plain test source
            # instead, often with a leading comment per test. If the raw
            # text still looks like real test functions, recover by
            # running the whole response through the same splitter used
            # for a bundled multi-def JSON entry, rather than raising.
            fallback = _split_multi_def_tests([text], language=language)
            if fallback and all(_TEST_DEF_START[language].search(t) for t in fallback):
                return fallback
            raise ValueError(f"Could not find a JSON array in Skeptic output: {text!r}")
        tests = json.loads(balanced)

    # Models sometimes over-escape newlines inside the JSON string values
    # (emitting a literal "\n" two-character sequence rather than a real
    # line break), which produces syntactically invalid source. Detect that
    # case per-string and unescape it.
    fixed = []
    for t in tests:
        if not isinstance(t, str):
            # Real failure observed live: despite the instructions saying
            # "a list of strings," a model can emit a stray non-string
            # element (e.g. a bare number) in the array. One malformed
            # entry shouldn't lose every other real test in the array.
            continue
        if "\n" not in t and "\\n" in t:
            t = t.encode().decode("unicode_escape")
        fixed.append(t)
    return _split_multi_def_tests(fixed, language=language)


def _split_multi_def_tests(tests: list[str], language: str = "python") -> list[str]:
    # Despite instructions to write one self-contained function per array
    # entry, models sometimes bundle several test functions into a single
    # string. The sandbox harness auto-discovers/invokes every test_*
    # function in a run, so an unsplit blob makes one failing sub-test mask
    # or misattribute the result of the others sharing that entry. Split any
    # entry with more than one top-level test def into separate entries.
    test_def_start = _TEST_DEF_START[language]
    split = []
    for t in tests:
        starts = [m.start() for m in test_def_start.finditer(t)]
        if len(starts) <= 1:
            split.append(t)
            continue
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(t)
            split.append(t[start:end].rstrip())
    return split


def find_bugs(spec: dict, code: str, retries: int = 2, language: str = "python") -> list[str]:
    prover_model = os.environ["PROVER_MODEL"]
    skeptic_model = os.environ["SKEPTIC_MODEL"]
    assert prover_model != skeptic_model, (
        "PROVER_MODEL and SKEPTIC_MODEL must differ — the adversarial "
        "pressure depends on genuinely different blind spots."
    )

    prompt = f"Spec:\n{json.dumps(spec, indent=2)}\n\nImplementation:\n{code}"
    last_error = None
    for _ in range(retries + 1):
        try:
            # Reasoning models can spend their whole token budget on the
            # hidden "reasoning" field before writing the actual JSON array,
            # especially on real (non-toy) code — observed live against an
            # 85-line file where the default budget wasn't enough. A wider
            # cap here specifically, not raised globally in llm.py, since
            # most callers don't need it.
            raw = complete(
                prompt=prompt, model=skeptic_model, system=SYSTEM_PROMPTS[language], max_tokens=8000
            )
        except RuntimeError as e:
            last_error = e
            continue
        try:
            return _extract_json_array(raw, language=language)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
    raise last_error
