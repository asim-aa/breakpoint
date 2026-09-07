"""Detects which Python files actually changed in a PR, so the Action can
verify what's new in a diff instead of requiring an exact --file argument
on every invocation — closes the V1 limitation stated in the README
("not parsed out of a multi-file diff")."""

import subprocess


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
