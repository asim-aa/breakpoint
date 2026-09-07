"""breakpoint CLI: `breakpoint run "<request>"` and `breakpoint history`."""

import argparse

from dotenv import load_dotenv

load_dotenv()

import memory
import storage
from graph import build_graph
from state import DEFAULT_MAX_ROUNDS


def _record_bugs_for_memory(spec_id: int, history: list, db_path: str = storage.DB_PATH):
    # Same dedup as arbiter.py's bugs_caught: a bug that failed identically
    # across several retry rounds is one catch, not several. Keeps the
    # first error message seen for each distinct test.
    real_bugs = {}
    for record in history:
        for r in record["results"]:
            if not r["passed"] and r.get("valid", True) and r["test_code"] not in real_bugs:
                real_bugs[r["test_code"]] = r.get("error")

    if not real_bugs:
        return  # no model load at all when a run converges clean

    try:
        for test_code, error in real_bugs.items():
            memory.record_bug(spec_id=spec_id, test_code=test_code, error=error, db_path=db_path)
    except Exception as e:
        # The pattern memory is a bonus, not the point of `breakpoint run` -
        # a failure here (e.g. no network for the model's first download)
        # shouldn't take down real, already-computed run results.
        print(f"(bug-pattern memory update skipped: {e})")


def cmd_run(args):
    app = build_graph()
    result = app.invoke(
        {
            "request": args.request,
            "spec": {},
            "code": "",
            "pending_tests": [],
            "all_test_codes": [],
            "tests": [],
            "round": 1,
            "max_rounds": args.max_rounds,
            "round_passed": False,
            "prior_failure": None,
            "verdict": None,
            "history": [],
            "report": None,
        },
        config={"recursion_limit": args.max_rounds * 4 + 10},
    )

    spec_id = storage.save_run(args.request, result["spec"], result["history"], result["report"])
    _record_bugs_for_memory(spec_id, result["history"])

    report = result["report"]
    print(f"Spec ID:        {spec_id}")
    print(f"Verdict:        {report['verdict']}")
    print(f"Rounds taken:   {report['rounds_taken']}")
    print(f"Total tests run:{report['total_tests_run']}")
    print(f"Bugs caught:    {report['bugs_caught']}")
    print(f"Confidence:     {report['confidence']}")


def cmd_history(args):
    rows = storage.list_history(limit=args.limit)
    if not rows:
        print("No runs recorded yet.")
        return
    header = f"{'id':>4}  {'verdict':<14}{'rounds':>7}{'bugs':>6}  request"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['id']:>4}  {row['last_verdict']:<14}{row['rounds']:>7}"
            f"{row['bugs_caught']:>6}  {row['request']}"
        )


def main():
    parser = argparse.ArgumentParser(prog="breakpoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the full graph on a request")
    run_parser.add_argument("request")
    run_parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    run_parser.set_defaults(func=cmd_run)

    history_parser = subparsers.add_parser("history", help="list past runs")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.set_defaults(func=cmd_history)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
