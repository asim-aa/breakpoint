"""Detects which Python files actually changed in a PR, so the Action can
verify what's new in a diff instead of requiring an exact --file argument
on every invocation — closes the V1 limitation stated in the README
("not parsed out of a multi-file diff")."""

import ast
import re
import subprocess
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -[\d,]+ \+(\d+)(?:,(\d+))? @@")


def get_changed_python_files(base_ref: str, cwd: str | None = None) -> list[str]:
    """Returns every added/modified/renamed .py file between base_ref and
    HEAD. Raises RuntimeError with the actual git error message on failure
    (e.g. base_ref doesn't exist, or this isn't a git repo) rather than
    letting a bare CalledProcessError traceback surface in CI logs."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git diff against '{base_ref}' failed: {e.stderr.strip()}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError("git is not available in this environment") from e

    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return [f for f in files if f.endswith(".py")]


def _changed_line_ranges(base_ref: str, file_path: str, cwd: str | None = None) -> list[tuple[int, int]]:
    """Line ranges (1-indexed, inclusive) touched in the NEW version of
    file_path, read straight from unified-diff hunk headers (`-U0`: no
    surrounding context lines, so every reported range is an actual
    addition/change, never unrelated nearby code)."""
    try:
        result = subprocess.run(
            ["git", "diff", "-U0", "--diff-filter=ACMR", f"{base_ref}...HEAD", "--", file_path],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git diff against '{base_ref}' failed: {e.stderr.strip()}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError("git is not available in this environment") from e

    ranges = []
    for line in result.stdout.splitlines():
        m = _HUNK_RE.match(line)
        if not m:
            continue
        new_start = int(m.group(1))
        new_count = int(m.group(2)) if m.group(2) is not None else 1
        if new_count == 0:
            # A hunk with a 0 new-line count is a pure deletion — nothing
            # was added to the new file at this point, so there's no range
            # to report here.
            continue
        ranges.append((new_start, new_start + new_count - 1))
    return ranges


def get_changed_functions(base_ref: str, file_path: str, cwd: str | None = None) -> list[dict]:
    """Narrows a changed file down to just the top-level function(s) whose
    body actually overlaps the diff, instead of handing an LLM the whole
    file for every change — smaller prompts, and a report that names
    exactly which function changed rather than the whole file.

    Deliberately top-level functions only, not methods or nested
    functions: extracting either without its enclosing class/function
    would produce code that can't run standalone in the sandbox (wrong
    indentation, or a bare `self` with no class). A change to a method or
    to module-level code outside any function returns an empty list here,
    and the caller falls back to verifying the whole file — this narrows
    scope where it safely can, it doesn't reduce coverage where it can't.
    """
    hunks = _changed_line_ranges(base_ref, file_path, cwd=cwd)
    if not hunks:
        return []

    full_path = (Path(cwd) / file_path) if cwd else Path(file_path)
    source = full_path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines(keepends=True)
    matched = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        end_line = node.end_lineno
        if any(h_start <= end_line and h_end >= start_line for h_start, h_end in hunks):
            matched.append(
                {
                    "name": node.name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "source": "".join(lines[start_line - 1 : end_line]),
                }
            )
    return matched
