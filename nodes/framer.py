"""Turns a plain-English request into a formal spec dict."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

_NAMING_CONVENTION = {
    "python": "snake_case",
    "javascript": "camelCase",
}

_DISPLAY_NAME = {
    "python": "Python",
    "javascript": "JavaScript",
}

SYSTEM_PROMPT_TEMPLATE = """You are the Framer in an adversarial code-generation system.
Given a plain-English coding request, produce a formal spec as STRICT JSON
with exactly these keys, and no others:

{{
  "function_name": "string",
  "inputs": [{{"name": "string", "type": "string", "description": "string"}}],
  "output": {{"type": "string", "description": "string"}},
  "constraints": ["string", ...],
  "examples": [{{"input": ..., "output": ...}}, ...]
}}

The target language is {language}: function_name and every input name
should follow {convention} naming.

Output ONLY the JSON object. No markdown fences, no commentary, no
explanation before or after."""


def _find_balanced_object(text: str) -> str | None:
    start = text.find("{")
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Models sometimes wrap output in ```json fences, or add prose before
    # or after the JSON despite instructions not to. Try the fenced form
    # first, then fall back to scanning for the first balanced {...} block.
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    balanced = _find_balanced_object(text)
    if balanced is None:
        raise ValueError(f"Could not find a JSON object in Framer output: {text!r}")
    return json.loads(balanced)


def frame(request: str, retries: int = 2, language: str = "python") -> dict:
    model = os.environ["PROVER_MODEL"]
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        language=_DISPLAY_NAME[language], convention=_NAMING_CONVENTION[language]
    )
    last_error = None
    for _ in range(retries + 1):
        raw = complete(
            prompt=f"Coding request: {request}",
            model=model,
            system=system_prompt,
        )
        try:
            return _extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
    raise last_error


INFER_SYSTEM_PROMPT_TEMPLATE = """You are the Framer in an adversarial code-verification
system. You are given an EXISTING {language} function implementation — not
a request to write new code — and your job is to infer the formal spec it
appears to implement, as STRICT JSON with exactly these keys, and no others:

{{
  "function_name": "string",
  "inputs": [{{"name": "string", "type": "string", "description": "string"}}],
  "output": {{"type": "string", "description": "string"}},
  "constraints": ["string", ...],
  "examples": [{{"input": ..., "output": ...}}, ...]
}}

Infer constraints from what the code's logic and any docstring/comments
imply it should handle — do not just describe what the code currently
does if a docstring or the function's apparent intent suggests it should
handle more (e.g. if it looks like it's meant to handle empty input but
doesn't, the constraint should still say empty input must be handled;
this is exactly what a Skeptic will later test against).

Output ONLY the JSON object. No markdown fences, no commentary, no
explanation before or after."""


def infer_spec_from_code(code: str, context: str = "", retries: int = 2, language: str = "python") -> dict:
    model = os.environ["PROVER_MODEL"]
    system_prompt = INFER_SYSTEM_PROMPT_TEMPLATE.format(language=_DISPLAY_NAME[language])
    prompt = f"Existing implementation:\n{code}"
    if context:
        prompt += f"\n\nAdditional context (e.g. PR description):\n{context}"

    last_error = None
    for _ in range(retries + 1):
        raw = complete(prompt=prompt, model=model, system=system_prompt)
        try:
            return _extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
    raise last_error
