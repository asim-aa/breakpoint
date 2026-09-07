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


def _format_patterns_table(patterns: list[dict], requests: dict[int, str]) -> list[str]:
    header = f"{'id':>4}  {'freq':>4}  {'description':<28}  examples"
    lines = [header, "-" * len(header)]
    for p in patterns:
        example_names = ", ".join(
            requests.get(sid, f"spec#{sid}")[:32] for sid in p["example_spec_ids"]
        )
        lines.append(f"{p['id']:>4}  {p['frequency']:>4}  {p['description']:<28}  {example_names}")
    return lines


def cmd_patterns(args):
    patterns = memory.list_patterns()
    if not patterns:
        print("No bug patterns recorded yet — run `breakpoint run` on a few specs first.")
        return

    all_spec_ids = sorted({sid for p in patterns for sid in p["example_spec_ids"]})
    requests = storage.get_requests_by_ids(all_spec_ids)

    for line in _format_patterns_table(patterns, requests):
        print(line)


def _format_leaderboard_table(rows: list[dict]) -> list[str]:
    # 40 chars comfortably fits real OpenRouter model slugs (the longest
    # in this project's own usage is 38 chars) without mid-word truncation.
    header = (
        f"{'prover model':<40}  {'skeptic model':<40}  "
        f"{'runs':>4}  {'converged':>9}  {'bugs':>4}  {'avg rounds':>10}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['prover_model'][:40]:<40}  {row['skeptic_model'][:40]:<40}  "
            f"{row['total_runs']:>4}  {row['converged']:>9}  "
            f"{row['total_bugs_caught']:>4}  {row['avg_rounds']:>10}"
        )
    return lines


def cmd_leaderboard(args):
    rows = storage.get_leaderboard()
    if not rows:
        print("No runs recorded yet — run `breakpoint run` a few times first.")
        return

    for line in _format_leaderboard_table(rows):
        print(line)
    print(
        "\nAccumulates across sessions as you change PROVER_MODEL/SKEPTIC_MODEL "
        "in .env and keep using `breakpoint run` — no separate multi-pair runner needed."
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

    patterns_parser = subparsers.add_parser(
        "patterns", help="list recorded bug patterns, most frequent first"
    )
    patterns_parser.set_defaults(func=cmd_patterns)

    leaderboard_parser = subparsers.add_parser(
        "leaderboard", help="compare Prover/Skeptic model pairs across all recorded runs"
    )
    leaderboard_parser.set_defaults(func=cmd_leaderboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
