"""Entry point for the Breakpoint GitHub Action: verifies existing Python
code adversarially and reports findings — never rewrites code.

Two ways to point it at code: --file for one explicit path, or --diff-base
to auto-detect every changed .py file against a git ref (e.g. the PR's
base branch) — closing the "not parsed out of a multi-file diff"
limitation this file used to state. Either way, still verify-only: a real
bug found is a finding for a human, never an auto-applied fix.

In --diff-base mode, each changed file is narrowed further to just the
top-level function(s) the diff actually touched (function-level diff
extraction — see diff_utils.get_changed_functions): a smaller prompt per
target, and a report that names the function, not just the file. A file
falls back to whole-file verification when the diff doesn't map cleanly
onto a function body (e.g. only a method or module-level code changed).
"""

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from diff_utils import get_changed_functions, get_changed_python_files
from verify import verify_existing_code

_LANGUAGE_BY_SUFFIX = {".py": "python", ".js": "javascript"}


def language_for_file(file_path: str) -> str:
    # diff-base mode only ever detects .py files (get_changed_python_files
    # filters to that extension, and get_changed_functions' AST-based
    # extraction is Python-only), so this only meaningfully varies for
    # --file mode. Defaults to python for an unrecognized extension rather
    # than erroring, matching this Action's original (Python-only) behavior.
    return _LANGUAGE_BY_SUFFIX.get(Path(file_path).suffix, "python")


def targets_for_file(file_path: str, diff_base: str | None) -> list[tuple[str, str]]:
    """(label, code) pairs to verify for one changed file. --file mode (no
    diff_base) always verifies the whole file — there's no base ref to
    diff against. --diff-base mode tries function-level extraction first;
    an empty result (diff didn't map onto a top-level function) falls back
    to the whole file, same as --file mode."""
    if diff_base:
        try:
            functions = get_changed_functions(diff_base, file_path)
        except RuntimeError as e:
            # get_changed_python_files already succeeded against this same
            # base_ref, so this is unexpected — but it's still just a
            # narrowing step, not the verification itself. Fall back
            # rather than losing the whole file over it.
            print(f"  Could not extract function-level diff for {file_path}, verifying whole file: {e}")
            functions = []
        if functions:
            return [(f"{file_path}::{fn['name']}", fn["source"]) for fn in functions]

    return [(file_path, Path(file_path).read_text())]


def format_report(file_path: str, result: dict) -> str:
    lines = [f"## Breakpoint verification: `{file_path}`", ""]

    if result["verdict"] == "no_bugs_found":
        lines.append(
            f"✅ **No real bugs found** — {len(result['tests'])} adversarial test(s) "
            f"generated and executed, all passed."
        )
    else:
        lines.append(
            f"❌ **{len(result['real_bugs'])} real bug(s) found** by actual execution, "
            f"not an LLM's opinion:"
        )
        lines.append("")
        for bug in result["real_bugs"]:
            first_line = bug["test_code"].splitlines()[0] if bug["test_code"] else ""
            lines.append(f"- `{first_line}`")
            if bug["error"]:
                error_snippet = bug["error"].strip().splitlines()[-1]
                lines.append(f"  - {error_snippet}")

    if result["invalid_tests"]:
        lines.append("")
        lines.append(
            f"<sub>{len(result['invalid_tests'])} generated test(s) were judged invalid "
            f"(bad syntax, or an assertion wrong on its own terms) and excluded — "
            f"see nodes/validator.py.</sub>"
        )

    lines.append("")
    lines.append(
        "<sub>Verified by [Breakpoint](https://github.com/asim-aa/breakpoint) — "
        "an adversarial Skeptic model wrote these tests, they ran for real in a "
        "sandbox, this is not a second LLM's opinion of the code.</sub>"
    )
    return "\n".join(lines)


def post_pr_comment(body: str) -> None:
    with open("/tmp/breakpoint_report.md", "w") as f:
        f.write(body)
    subprocess.run(
        ["gh", "pr", "comment", "--body-file", "/tmp/breakpoint_report.md"],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--file", help="Path to one file to verify (.py or .js)")
    target.add_argument(
        "--diff-base",
        help="Git ref to diff against (e.g. origin/main) — auto-detects every "
        "changed .py file instead of requiring an exact path",
    )
    parser.add_argument("--context", default="", help="Optional context, e.g. a PR description")
    parser.add_argument(
        "--post-comment",
        action="store_true",
        help="Post the report as a PR comment (requires `gh` auth and a PR context)",
    )
    args = parser.parse_args()

    if args.file:
        files = [args.file]
    else:
        try:
            files = get_changed_python_files(args.diff_base)
        except RuntimeError as e:
            print(f"Could not determine changed files: {e}")
            sys.exit(2)
        if not files:
            print(f"No changed .py files found against {args.diff_base} — nothing to verify.")
            sys.exit(0)

    reports = []
    any_bugs_found = False
    for file_path in files:
        language = language_for_file(file_path)
        for label, code in targets_for_file(file_path, args.diff_base):
            try:
                result = verify_existing_code(code, context=args.context, language=language)
            except (httpx.HTTPStatusError, RuntimeError) as e:
                # A provider hiccup on one target shouldn't sink the whole
                # run — report it plainly and keep verifying the rest, same
                # resilience pattern as eval/run_eval.py.
                print(f"  Provider failure verifying {label}, skipping: {e}")
                reports.append(
                    f"## Breakpoint verification: `{label}`\n\n"
                    f"⚠️ **Skipped** — provider error: {e}"
                )
                continue
            reports.append(format_report(label, result))
            if result["verdict"] == "bugs_found":
                any_bugs_found = True

    combined_report = "\n\n---\n\n".join(reports)
    print(combined_report)

    if args.post_comment:
        post_pr_comment(combined_report)

    sys.exit(1 if any_bugs_found else 0)


if __name__ == "__main__":
    main()
