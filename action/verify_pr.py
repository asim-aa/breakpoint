"""Entry point for the Breakpoint GitHub Action: verifies one existing
Python file adversarially and reports findings — never rewrites code.

V1 scope, stated plainly: verifies a single file/function per invocation
(passed explicitly, not parsed out of a multi-file diff), and only
reports what it finds. If a real bug turns up, that's a finding for a
human to act on, not something this auto-fixes into the PR.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from verify import verify_existing_code


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
    parser.add_argument("--file", required=True, help="Path to the Python file to verify")
    parser.add_argument("--context", default="", help="Optional context, e.g. a PR description")
    parser.add_argument(
        "--post-comment",
        action="store_true",
        help="Post the report as a PR comment (requires `gh` auth and a PR context)",
    )
    args = parser.parse_args()

    code = Path(args.file).read_text()
    result = verify_existing_code(code, context=args.context)
    report = format_report(args.file, result)

    print(report)

    if args.post_comment:
        post_pr_comment(report)

    sys.exit(1 if result["verdict"] == "bugs_found" else 0)


if __name__ == "__main__":
    main()
