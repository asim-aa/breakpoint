"""Verifies EXISTING code, rather than generating new code from a spec.

This is the mode a GitHub Action (or any CI hook) actually needs: given a
function that already exists in a PR, don't ask an LLM to write new code —
infer what it's supposed to do, adversarially test it for real, and report
whether it survives. There's deliberately no retry loop here: this isn't
the Prover/Skeptic fix-and-retest cycle from graph.py, it's a one-shot
verdict on code a human already wrote. Auto-fixing someone's PR without
being asked is out of scope — this reports findings, it doesn't rewrite
anyone's code.
"""

from nodes.framer import infer_spec_from_code
from nodes.skeptic import find_bugs
from nodes.validator import validate_test
from sandbox import run_test


def verify_existing_code(code: str, context: str = "", language: str = "python") -> dict:
    spec = infer_spec_from_code(code, context=context, language=language)
    test_codes = find_bugs(spec, code, language=language)

    results = []
    for test_code in test_codes:
        result = run_test(code, test_code, language=language)
        diagnostic = None
        if not result.passed:
            diagnostic = result.stderr.strip() if result.stderr.strip() else result.error
        results.append({"test_code": test_code, "passed": result.passed, "error": diagnostic})

    validated = []
    for r in results:
        if r["passed"]:
            validated.append({**r, "valid": True, "validation_reason": None})
        else:
            verdict = validate_test(spec, r["test_code"], r["error"], language=language)
            validated.append(
                {**r, "valid": verdict["valid"], "validation_reason": verdict["reason"]}
            )

    real_bugs = [r for r in validated if not r["passed"] and r["valid"]]
    invalid_tests = [r for r in validated if not r["passed"] and not r["valid"]]

    return {
        "spec": spec,
        "tests": validated,
        "real_bugs": real_bugs,
        "invalid_tests": invalid_tests,
        "verdict": "bugs_found" if real_bugs else "no_bugs_found",
    }
