"""Tests for action/diff_utils.py against a REAL disposable git repo, not
mocked subprocess calls — git diff parsing is exactly the kind of thing
that's worth proving against the real tool rather than assuming its
output format."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "action"))

import pytest

from diff_utils import get_changed_python_files


def _run(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")

    (repo / "base.py").write_text("def base():\n    pass\n")
    (repo / "README.md").write_text("# hello\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-q", "-m", "base commit")

    return repo


def test_detects_a_new_python_file(git_repo):
    (git_repo / "new_feature.py").write_text("def f():\n    return 1\n")
    _run(git_repo, "add", "new_feature.py")
    _run(git_repo, "commit", "-q", "-m", "add feature")

    changed = get_changed_python_files("main~1", cwd=str(git_repo))
    assert changed == ["new_feature.py"]


def test_ignores_non_python_files(git_repo):
    (git_repo / "notes.md").write_text("more notes\n")
    (git_repo / "changed.py").write_text("def g():\n    return 2\n")
    _run(git_repo, "add", ".")
    _run(git_repo, "commit", "-q", "-m", "mixed change")

    changed = get_changed_python_files("main~1", cwd=str(git_repo))
    assert changed == ["changed.py"]


def test_detects_a_modified_existing_file(git_repo):
    (git_repo / "base.py").write_text("def base():\n    return 42\n")
    _run(git_repo, "add", "base.py")
    _run(git_repo, "commit", "-q", "-m", "modify base")

    changed = get_changed_python_files("main~1", cwd=str(git_repo))
    assert changed == ["base.py"]


def test_no_changes_returns_empty_list(git_repo):
    changed = get_changed_python_files("main", cwd=str(git_repo))
    assert changed == []


def test_raises_clear_error_for_unknown_ref(git_repo):
    with pytest.raises(RuntimeError, match="git diff against"):
        get_changed_python_files("nonexistent-branch", cwd=str(git_repo))


def test_raises_clear_error_when_not_a_git_repo(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    with pytest.raises(RuntimeError):
        get_changed_python_files("main", cwd=str(not_a_repo))
