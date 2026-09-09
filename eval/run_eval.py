"""Runs the eval set through Breakpoint's full graph, and through a
non-adversarial "self-check" baseline, and reports real numbers.

Scope note: run with a small MAX_ROUNDS (2) rather than the full default (5)
to fit within OpenRouter's free-tier daily request cap while still measuring
the core claim — did the Skeptic catch a real bug in the Prover's first
attempt that the baseline self-check missed. Also: if a call hits the daily
rate limit mid-run, the eval stops and reports on however many problems it
got through, rather than crashing with a half-written report.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import httpx

from graph import build_graph
from llm import complete

EVAL_MAX_ROUNDS = 2
EVAL_DIR = Path(__file__).resolve().parent

# The JavaScript problem set uses the exact same 6 requests and difficulty
# labels as the Python one, on purpose — this makes the two reports an
# apples-to-apples comparison of the same conceptual problems run through
# a different target language, not a different, harder-or-easier set.
PROBLEMS_PATH = {
    "python": EVAL_DIR / "problems.json",
    "javascript": EVAL_DIR / "problems_javascript.json",
}
REPORT_PATH = {
    "python": EVAL_DIR / "report.md",
    "javascript": EVAL_DIR / "report_javascript.md",
}


def run_breakpoint_mode(request: str, language: str = "python") -> dict:
    app = build_graph()
    result = app.invoke(
        {
            "request": request,
            "language": language,
            "spec": {},
            "code": "",
            "pending_tests": [],
            "all_test_codes": [],
            "tests": [],
            "round": 1,
            "max_rounds": EVAL_MAX_ROUNDS,
            "round_passed": False,
            "prior_failure": None,
            "verdict": None,
            "history": [],
            "report": None,
        },
        config={"recursion_limit": EVAL_MAX_ROUNDS * 4 + 10},
    )
    round1 = result["history"][0] if result["history"] else None

    # Track the Validator's activity across every round: how many failing
    # tests it judged invalid (bad syntax, or an assertion wrong on its own
    # terms) versus real bugs. This is the direct evidence for whether
    # nodes/validator.py is doing anything in practice, not just in theory.
    invalid_tests_seen = {
        r["test_code"]
        for record in result["history"]
        for r in record["results"]
        if not r["passed"] and not r.get("valid", True)
    }

    return {
        "spec": result["spec"],
        "round1_code": round1["code"] if round1 else result["code"],
        "round1_bug_found": (round1 is not None) and (not round1["round_passed"]),
        "round1_failing_tests": (
            [r for r in round1["results"] if not r["passed"]] if round1 else []
        ),
        "verdict": result["report"]["verdict"],
        "rounds_taken": result["report"]["rounds_taken"],
        "bugs_caught": result["report"]["bugs_caught"],
        "invalid_tests_filtered": len(invalid_tests_seen),
    }


def run_baseline_mode(spec: dict, code: str) -> str:
    # Non-adversarial self-check: same model that wrote the code just asked
    # to judge its own work, with no execution — this is exactly the
    # "does this look right?" pattern Breakpoint exists to replace.
    prompt = (
        f"Spec:\n{json.dumps(spec, indent=2)}\n\n"
        f"Implementation:\n{code}\n\n"
        f"Does this implementation correctly satisfy the spec? "
        f"Answer with exactly one word: CORRECT or INCORRECT."
    )
    model = os.environ["PROVER_MODEL"]
    # A generous token budget matters here: reasoning models spend tokens on
    # their "reasoning" field before ever writing CORRECT/INCORRECT, so a
    # tight cap (e.g. 20) can truncate the response before the verdict word
    # appears at all, showing up as a false "unknown" rather than a real
    # baseline opinion. Observed in practice — see eval/report.md's notes.
    raw = complete(prompt=prompt, model=model, max_tokens=300)
    text = raw.upper()
    if "INCORRECT" in text:
        return "incorrect"
    if "CORRECT" in text:
        return "correct"
    return "unknown"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--language", choices=["python", "javascript"], default="python",
        help="target language for the eval run (default: python)",
    )
    args = parser.parse_args()
    language = args.language

    with open(PROBLEMS_PATH[language]) as f:
        problems = json.load(f)

    results = []
    stopped_early = False

    for i, problem in enumerate(problems):
        request = problem["request"]
        print(f"[{i + 1}/{len(problems)}] {problem['id']}: {request}", flush=True)

        try:
            bp = run_breakpoint_mode(request, language=language)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print("  Daily rate limit hit during Breakpoint mode — stopping eval early.", flush=True)
                stopped_early = True
                break
            print(f"  Transient provider error during Breakpoint mode, skipping this problem: {e}", flush=True)
            continue
        except RuntimeError as e:
            # llm.py raises RuntimeError for provider-level failures embedded
            # in a 200 response (observed live: an Nvidia "temporarily
            # overloaded" 502 wrapped in a normal-looking JSON body) — these
            # aren't HTTP errors, but they're just as transient. Skip this
            # one problem rather than losing the whole eval to one flaky call.
            print(f"  Provider failure during Breakpoint mode, skipping this problem: {e}", flush=True)
            continue

        try:
            baseline_verdict = run_baseline_mode(bp["spec"], bp["round1_code"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print("  Daily rate limit hit during baseline mode — recording as unknown.", flush=True)
                baseline_verdict = "unknown (rate-limited)"
            else:
                print(f"  Transient provider error during baseline mode: {e}", flush=True)
                baseline_verdict = "unknown (provider error)"
        except RuntimeError as e:
            print(f"  Provider failure during baseline mode: {e}", flush=True)
            baseline_verdict = "unknown (provider error)"

        row = {
            "id": problem["id"],
            "difficulty": problem["difficulty"],
            "breakpoint_round1_bug_found": bp["round1_bug_found"],
            "breakpoint_verdict": bp["verdict"],
            "breakpoint_rounds_taken": bp["rounds_taken"],
            "breakpoint_bugs_caught": bp["bugs_caught"],
            "invalid_tests_filtered": bp["invalid_tests_filtered"],
            "baseline_self_check": baseline_verdict,
        }
        results.append(row)
        print(
            f"  round1_bug_found={row['breakpoint_round1_bug_found']} "
            f"baseline_self_check={row['baseline_self_check']} "
            f"final_verdict={row['breakpoint_verdict']} "
            f"invalid_tests_filtered={row['invalid_tests_filtered']}",
            flush=True,
        )

    write_report(results, len(problems), stopped_early, language=language)
    print(f"\nReport written to {REPORT_PATH[language]}", flush=True)


def write_report(results: list, total_problems: int, stopped_early: bool, language: str = "python"):
    n = len(results)
    breakpoint_caught = sum(1 for r in results if r["breakpoint_round1_bug_found"])
    baseline_missed = sum(
        1
        for r in results
        if r["breakpoint_round1_bug_found"] and r["baseline_self_check"] == "correct"
    )
    converged = sum(1 for r in results if r["breakpoint_verdict"] == "converged")
    unresolved = sum(1 for r in results if r["breakpoint_verdict"] == "unresolved")
    avg_rounds = (
        sum(r["breakpoint_rounds_taken"] for r in results) / n if n else 0
    )

    display_language = {"python": "Python", "javascript": "JavaScript"}[language]

    lines = []
    lines.append(f"# Breakpoint eval report ({display_language})\n")
    lines.append(
        f"**Sample size: N={n}"
        + (f" of {total_problems} planned" if n < total_problems else "")
        + f"**. Target language: **{display_language}**. This is a small eval — "
        "these numbers indicate a direction, not a statistically reliable "
        "rate. Do not extrapolate beyond this specific problem set and this "
        "specific model pair.\n"
    )
    if language == "javascript":
        lines.append(
            "**Comparison note:** this uses the exact same 6 problems and "
            "difficulty labels as [report.md](report.md) (the Python eval) — "
            "same requests, run through the identical pipeline against a "
            "different target language — so the two reports are a direct "
            "comparison, not two unrelated problem sets.\n"
        )
    if stopped_early:
        lines.append(
            "> **Note:** this run stopped early after hitting OpenRouter's "
            "free-tier daily rate limit. The numbers below reflect only the "
            f"{n} problems that completed before that happened.\n"
        )
    lines.append(
        "**Methodology note:** Breakpoint mode ran with `MAX_ROUNDS=2` "
        "(not the system default of 5) to fit within the free-tier daily "
        "request cap. The baseline reuses Breakpoint's own round-1 Prover "
        "code and asks the same model to self-assess it with no execution — "
        "this isolates the effect of adversarial *testing*, not a different "
        "implementation.\n"
    )

    lines.append("## Results\n")
    lines.append(
        "| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds | Invalid tests filtered |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['id']} | {r['difficulty']} | "
            f"{'yes' if r['breakpoint_round1_bug_found'] else 'no'} | "
            f"{r['baseline_self_check']} | {r['breakpoint_verdict']} | "
            f"{r['breakpoint_rounds_taken']} | {r['invalid_tests_filtered']} |"
        )

    total_invalid = sum(r["invalid_tests_filtered"] for r in results)

    lines.append("\n## Summary\n")
    lines.append(f"- Breakpoint caught a real bug in the first attempt on **{breakpoint_caught}/{n}** problems.")
    lines.append(
        f"- Of those, the baseline self-check (same model, no execution) said "
        f"\"CORRECT\" on **{baseline_missed}/{breakpoint_caught if breakpoint_caught else 0}** — "
        f"i.e. a same-model opinion-only check missed a bug that real execution caught."
    )
    lines.append(f"- {converged}/{n} problems converged within the 2-round budget; {unresolved}/{n} stayed unresolved.")
    lines.append(f"- Average rounds taken: {avg_rounds:.1f} (budget capped at {EVAL_MAX_ROUNDS}).")
    lines.append(
        f"- The Validator (`nodes/validator.py`) filtered **{total_invalid}** distinct "
        f"invalid test(s) across this run — Skeptic-generated tests judged not to be "
        f"real bugs (bad syntax, or an assertion wrong on its own terms), excluded "
        f"from `bugs_caught` and never fed back to the Prover."
    )

    with open(REPORT_PATH[language], "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
