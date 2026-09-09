"""Judges whether a failing test represents a genuine bug or an invalid
test that shouldn't count against the Prover.

This exists because of a real, reproduced failure mode: the Skeptic
sometimes writes a test that is itself broken — invalid syntax, or an
assertion that's simply wrong on its own terms (e.g. `assert -3 + -1 == 1`)
or that exercises input the spec never promised to support. Before this
validator existed, such a test was indistinguishable from a real bug: it
failed every round, got fed back to the Prover as `prior_failure` forever,
and permanently blocked convergence — the Prover cannot fix code to satisfy
an assertion that is mathematically false. See README.md's "Real bugs
found and fixed" section for the live example that motivated this.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

SYSTEM_PROMPT = """You are the Validator in an adversarial code-generation
system. You are given a formal spec and a test that FAILED against an
implementation. Your only job is to judge whether the TEST is legitimate —
NOT whether the implementation is correct. Assume the implementation might
be wrong; that is not your concern here.

A test is INVALID if any of the following is true:
- its expected value contradicts the spec's stated constraints or examples
- it exercises an input explicitly outside what the spec's constraints allow
- its assertion is simply false on its own terms, independent of any
  implementation (e.g. an arithmetic or logical falsehood)

Otherwise the test is VALID — a real, legitimate bug report against the
implementation.

Respond with STRICT JSON: {"valid": true or false, "reason": "one sentence"}
Output ONLY the JSON object, no commentary, no markdown fences."""


def _static_syntax_check_python(test_code: str) -> str | None:
    # Free, local, no LLM call: a test that doesn't even compile on its own
    # is definitely invalid, regardless of what the implementation does.
    try:
        compile(test_code, "<test>", "exec")
        return None
    except SyntaxError as e:
        return f"test itself does not compile: {e}"


def _static_syntax_check_javascript(test_code: str) -> str | None:
    # Same idea as the Python path, via `node --check` (syntax-only, never
    # executes the code) — there's no in-process JS parser in the stdlib.
    # KNOWN GAP: if `node` isn't installed, this can't confirm anything
    # either way and returns None (falls through to the semantic LLM
    # check), rather than blocking on a missing local dependency.
    if shutil.which("node") is None:
        return None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(test_code)
        path = f.name
    try:
        proc = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=5)
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        return f"test itself does not compile: {proc.stderr.strip()}"
    return None


_STATIC_SYNTAX_CHECKS = {
    "python": _static_syntax_check_python,
    "javascript": _static_syntax_check_javascript,
}


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def validate_test(spec: dict, test_code: str, error: str | None, language: str = "python") -> dict:
    static_issue = _STATIC_SYNTAX_CHECKS[language](test_code)
    if static_issue:
        return {"valid": False, "reason": static_issue}

    model = os.environ["PROVER_MODEL"]
    prompt = (
        f"Spec:\n{json.dumps(spec, indent=2)}\n\n"
        f"Failing test:\n{test_code}\n\n"
        f"Failure output:\n{error or ''}"
    )
    try:
        raw = complete(prompt=prompt, model=model, system=SYSTEM_PROMPT, max_tokens=150)
        result = _extract_json(raw)
        return {
            "valid": bool(result.get("valid", True)),
            "reason": result.get("reason", ""),
        }
    except Exception as e:
        # If validation itself fails (a provider hiccup, bad JSON), default
        # to trusting the test rather than silently discarding a possibly
        # real bug — the risk of a false "invalid" is worse than the risk
        # of retrying once more against a test that turns out to be bad.
        return {"valid": True, "reason": f"validation inconclusive, defaulting to valid: {e}"}
