"""Implements a function against a spec."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

SYSTEM_PROMPTS = {
    "python": """You are the Prover in an adversarial code-generation system.
Given a formal spec (and optionally a previous failure trace), write a
single, correct Python function implementation. Use snake_case for the
function and parameter names.

Respond with a single ```python fenced code block containing ONLY the
function (plus any small helpers it needs). Do not write any prose,
reasoning, or explanation before or after the code block.""",
    "javascript": """You are the Prover in an adversarial code-generation system.
Given a formal spec (and optionally a previous failure trace), write a
single, correct JavaScript function implementation. Use camelCase for the
function and parameter names. Write a plain top-level function declaration
(`function name(...) { ... }`) — not an arrow function assigned to a
const/let, not a class, not `module.exports` — so it's callable directly
by name elsewhere in the same file. Target plain Node.js (CommonJS): no
import/export statements, no TypeScript syntax.

Respond with a single ```javascript fenced code block containing ONLY the
function (plus any small helpers it needs). Do not write any prose,
reasoning, or explanation before or after the code block.""",
}

# Fallback for when a model skips the code fence entirely and just starts
# writing code (or prefaces it with prose) — cuts everything before the
# first line that looks like the start of real code for that language.
_NO_FENCE_START = {
    "python": re.compile(r"^(def |import |from |class )", re.MULTILINE),
    "javascript": re.compile(r"^(function |const |let |var |class )", re.MULTILINE),
}


def _extract_code(text: str, language: str = "python") -> str:
    text = text.strip()
    # `(?:\w+)?` skips whatever language tag follows the opening fence
    # (python, javascript, js, or none at all) rather than assuming one
    # specific tag — matching only the literal word "python" here used to
    # silently capture a wrong language's tag as if it were code.
    fence_match = re.search(r"```(?:\w+)?\s*\n?(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    no_fence_start = _NO_FENCE_START[language]
    match = no_fence_start.search(text)
    if match:
        return text[match.start():].strip()
    return text


def _is_valid_python(code: str) -> bool:
    try:
        compile(code, "<prover_output>", "exec")
        return True
    except SyntaxError:
        return False


def _is_valid_javascript(code: str) -> bool:
    # Real failure observed live: a reasoning model can leak pure
    # explanatory prose into the "code" field with zero actual
    # JavaScript in it — no fence, doesn't start with a recognized
    # keyword, so _extract_code returns it unchanged. Node's own parser
    # (via `node --check`, same technique validator.py uses for
    # Skeptic-generated tests) catches that here, symmetric with the
    # Python compile() check above, instead of silently burning a whole
    # sandbox round finding out.
    if shutil.which("node") is None:
        return True  # can't confirm either way; defer to the sandbox round
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(["node", "--check", path], capture_output=True, timeout=5)
    finally:
        os.unlink(path)
    return proc.returncode == 0


_IS_VALID = {
    "python": _is_valid_python,
    "javascript": _is_valid_javascript,
}


def prove(spec: dict, prior_failure: dict | None = None, retries: int = 2, language: str = "python") -> str:
    model = os.environ["PROVER_MODEL"]

    prompt = f"Spec:\n{spec}\n"
    if prior_failure:
        prompt += (
            f"\nYour previous implementation failed this test:\n"
            f"{prior_failure.get('test_code', '')}\n"
            f"With this error:\n{prior_failure.get('error', '')}\n"
            f"Fix the implementation so this test passes, without breaking "
            f"the spec's other constraints."
        )

    is_valid = _IS_VALID[language]
    last_code = None
    for _ in range(retries + 1):
        raw = complete(prompt=prompt, model=model, system=SYSTEM_PROMPTS[language])
        code = _extract_code(raw, language=language)
        last_code = code
        # Reasoning models sometimes leak unterminated prose or pure
        # explanation into the code output (e.g. an unclosed string, or
        # no code at all), which fails every test identically. Catch that
        # here rather than burning a full sandbox round discovering it.
        if is_valid(code):
            return code

    # Every retry produced invalid syntax. Rather than raising and
    # crashing the whole graph run, return the last attempt as-is: the
    # sandbox will surface the same failure as a normal test failure,
    # which becomes next round's prior_failure — letting the outer
    # round-budget retry loop (which has its own limit) decide whether to
    # keep trying, instead of one node's exhausted internal retries taking
    # down the entire run.
    return last_code
