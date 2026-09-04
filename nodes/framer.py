"""Turns a plain-English request into a formal spec dict."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import complete

SYSTEM_PROMPT = """You are the Framer in an adversarial code-generation system.
Given a plain-English coding request, produce a formal spec as STRICT JSON
with exactly these keys, and no others:

{
  "function_name": "string",
  "inputs": [{"name": "string", "type": "string", "description": "string"}],
  "output": {"type": "string", "description": "string"},
  "constraints": ["string", ...],
  "examples": [{"input": ..., "output": ...}, ...]
}

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


def frame(request: str, retries: int = 2) -> dict:
    model = os.environ["PROVER_MODEL"]
    last_error = None
    for _ in range(retries + 1):
        raw = complete(
            prompt=f"Coding request: {request}",
            model=model,
            system=SYSTEM_PROMPT,
        )
        try:
            return _extract_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
    raise last_error
