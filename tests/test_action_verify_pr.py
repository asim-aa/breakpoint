"""Tests for action/verify_pr.py: report formatting (pure) and the CLI's
exit-code contract. verify_existing_code and PR-commenting are mocked —
this never touches a real LLM, sandbox, or the `gh` CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "action"))

import pytest

import verify_pr


def test_format_report_no_bugs_found():
    result = {"verdict": "no_bugs_found", "tests": [{"test_code": "t", "passed": True}], "real_bugs": [], "invalid_tests": []}
    report = verify_pr.format_report("src/foo.py", result)
    assert "No real bugs found" in report
    assert "src/foo.py" in report


def test_format_report_bugs_found_lists_each_one():
    result = {
        "verdict": "bugs_found",
        "tests": [],
        "real_bugs": [
            {"test_code": "def test_none_input():\n    assert f(None)", "error": "TypeError: boom"},
        ],
        "invalid_tests": [],
    }
    report = verify_pr.format_report("src/foo.py", result)
    assert "1 real bug(s) found" in report
    assert "test_none_input" in report
    assert "TypeError: boom" in report


def test_format_report_notes_invalid_tests_when_present():
    result = {
        "verdict": "no_bugs_found",
        "tests": [],
        "real_bugs": [],
        "invalid_tests": [{"test_code": "bad", "error": "e"}],
    }
    report = verify_pr.format_report("src/foo.py", result)
    assert "1 generated test(s) were judged invalid" in report


def test_main_exits_zero_when_no_bugs_found(monkeypatch, tmp_path, capsys):
    f = tmp_path / "foo.py"
    f.write_text("def f(x):\n    return x")

    monkeypatch.setattr(verify_pr, "verify_existing_code", lambda code, context="": {
        "verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": [],
    })
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--file", str(f)])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()
    assert exc_info.value.code == 0


def test_main_exits_nonzero_when_bugs_found(monkeypatch, tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("def f(x):\n    return x")

    monkeypatch.setattr(verify_pr, "verify_existing_code", lambda code, context="": {
        "verdict": "bugs_found", "tests": [], "real_bugs": [{"test_code": "t", "error": "e"}], "invalid_tests": [],
    })
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--file", str(f)])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()
    assert exc_info.value.code == 1


def test_main_does_not_post_comment_by_default(monkeypatch, tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("def f(x):\n    return x")

    posted = []
    monkeypatch.setattr(verify_pr, "post_pr_comment", lambda body: posted.append(body))
    monkeypatch.setattr(verify_pr, "verify_existing_code", lambda code, context="": {
        "verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": [],
    })
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--file", str(f)])

    with pytest.raises(SystemExit):
        verify_pr.main()
    assert posted == []


def test_main_posts_comment_when_flag_given(monkeypatch, tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("def f(x):\n    return x")

    posted = []
    monkeypatch.setattr(verify_pr, "post_pr_comment", lambda body: posted.append(body))
    monkeypatch.setattr(verify_pr, "verify_existing_code", lambda code, context="": {
        "verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": [],
    })
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--file", str(f), "--post-comment"])

    with pytest.raises(SystemExit):
        verify_pr.main()
    assert len(posted) == 1
