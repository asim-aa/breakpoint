"""Manual sanity check for the full framer -> prover -> skeptic -> sandbox
-> (retry loop) -> arbiter graph."""

import sys

from dotenv import load_dotenv

load_dotenv()

from graph import build_graph
from state import DEFAULT_MAX_ROUNDS


def run(request: str, max_rounds: int = DEFAULT_MAX_ROUNDS):
    app = build_graph()
    result = app.invoke(
        {
            "request": request,
            "spec": {},
            "code": "",
            "pending_tests": [],
            "all_test_codes": [],
            "tests": [],
            "round": 1,
            "max_rounds": max_rounds,
            "round_passed": False,
            "prior_failure": None,
            "verdict": None,
            "history": [],
        },
        config={"recursion_limit": max_rounds * 4 + 10},
    )

    print(f"=== Request: {request} ===\n")

    for record in result["history"]:
        print(f"--- Round {record['round']} ---")
        print(f"New tests generated this round: {len(record['new_tests'])}")
        for r in record["results"]:
            status = "PASS" if r["passed"] else "FAIL"
            first_line = r["test_code"].splitlines()[0] if r["test_code"] else ""
            print(f"  [{status}] {first_line}")
            if not r["passed"]:
                print(f"         error: {r['error']}")
        print()

    print("--- Final code ---")
    print(result["code"])
    print(f"\n=== Verdict: {result['verdict']} (after {len(result['history'])} round(s)) ===\n")
    return result


if __name__ == "__main__":
    request = sys.argv[1] if len(sys.argv) > 1 else "merge overlapping intervals"
    run(request)
