"""Implements a function against a spec."""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

SYSTEM_PROMPT = """You are the Prover in an adversarial code-generation system.
Given a formal spec (and optionally a previous failure trace), write a
single, correct Python function implementation.

Respond with a single ```python fenced code block containing ONLY the
function (plus any small helpers it needs). Do not write any prose,
reasoning, or explanation before or after the code block."""


def _extract_code(text: str) -> str:
    text = text.strip()
    fence_match = re.search(r"```(?:python)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    # No fence: the model may have prefaced the code with prose. Cut
    # everything before the first top-level def/import/class.
    def_match = re.search(r"^(def |import |from |class )", text, re.MULTILINE)
    if def_match:
        return text[def_match.start():].strip()
    return text


def prove(spec: dict, prior_failure: dict | None = None, retries: int = 2) -> str:
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

    last_code = None
    for _ in range(retries + 1):
        raw = complete(prompt=prompt, model=model, system=SYSTEM_PROMPT)
        code = _extract_code(raw)
        last_code = code
        try:
            # Reasoning models sometimes leak unterminated prose into the
            # code block (e.g. an unclosed string), which "compiles" to
            # nothing useful but crashes every test identically. Catch that
            # here rather than burning a full sandbox round discovering it.
            compile(code, "<prover_output>", "exec")
            return code
        except SyntaxError:
            continue

    # Every retry produced invalid syntax. Rather than raising and crashing
    # the whole graph run, return the last attempt as-is: the sandbox will
    # surface the same SyntaxError as a normal test failure, which becomes
    # next round's prior_failure — letting the outer round-budget retry loop
    # (which has its own limit) decide whether to keep trying, instead of
    # one node's exhausted internal retries taking down the entire run.
    return last_code
