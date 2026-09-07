"""Decides convergence and produces the final verdict/coverage report."""


def arbitrate(state: dict) -> dict:
    history = state.get("history", [])
    rounds_taken = len(history)
    total_tests_run = sum(len(r["results"]) for r in history)

    # A "bug caught" is a distinct test that failed at least once AND was
    # judged a legitimate test (not an invalid one — see nodes/validator.py)
    # across the whole run, deduped by source text. Falls back to counting
    # every failure as valid (r.get("valid", True)) for state produced
    # before the validator existed.
    failed_test_codes = {
        r["test_code"]
        for record in history
        for r in record["results"]
        if not r["passed"] and r.get("valid", True)
    }
    bugs_caught = len(failed_test_codes)

    verdict = "converged" if state.get("round_passed") else "unresolved"

    # Confidence heuristic (deliberately simple, not statistically rigorous):
    # - Converged: starts at 1.0, loses 0.15 per extra round beyond the
    #   first (more rounds needed = more was wrong initially), floored at 0.4
    #   so a hard-won convergence still counts as real signal.
    # - Unresolved: capped below "converged" regardless of anything else,
    #   scaled by how much of the last round's test suite actually passed —
    #   an unresolved run that was 9/10 tests away from done still reads as
    #   more promising than one that failed everything.
    if verdict == "converged":
        confidence = max(0.4, 1.0 - 0.15 * (rounds_taken - 1))
    else:
        last_round = history[-1] if history else None
        if last_round and last_round["results"]:
            # A test that failed but was judged invalid doesn't count
            # against confidence — it isn't evidence the Prover is wrong.
            passed_frac = sum(
                1 for r in last_round["results"] if r["passed"] or not r.get("valid", True)
            ) / len(last_round["results"])
        else:
            passed_frac = 0.0
        confidence = 0.3 * passed_frac

    return {
        "verdict": verdict,
        "rounds_taken": rounds_taken,
        "total_tests_run": total_tests_run,
        "bugs_caught": bugs_caught,
        "confidence": round(confidence, 2),
    }
