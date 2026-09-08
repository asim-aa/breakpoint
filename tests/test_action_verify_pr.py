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


def test_file_and_diff_base_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--file", "a.py", "--diff-base", "main"])
    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()
    assert exc_info.value.code == 2  # argparse's own usage-error code


def test_diff_base_verifies_every_detected_file(monkeypatch, tmp_path):
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("def a(): pass")
    f2.write_text("def b(): pass")

    calls = []
    monkeypatch.setattr(verify_pr, "get_changed_python_files", lambda base_ref: [str(f1), str(f2)])
    monkeypatch.setattr(
        verify_pr,
        "verify_existing_code",
        lambda code, context="": calls.append(code) or {
            "verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": [],
        },
    )
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--diff-base", "main"])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()

    assert exc_info.value.code == 0
    assert len(calls) == 2  # both detected files were actually verified


def test_diff_base_exits_nonzero_if_any_file_has_bugs(monkeypatch, tmp_path):
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("def a(): pass")
    f2.write_text("def b(): pass")

    results = iter([
        {"verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": []},
        {"verdict": "bugs_found", "tests": [], "real_bugs": [{"test_code": "t", "error": "e"}], "invalid_tests": []},
    ])
    monkeypatch.setattr(verify_pr, "get_changed_python_files", lambda base_ref: [str(f1), str(f2)])
    monkeypatch.setattr(verify_pr, "verify_existing_code", lambda code, context="": next(results))
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--diff-base", "main"])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()
    assert exc_info.value.code == 1  # nonzero because ONE of the two files had a real bug


def test_diff_base_with_no_changed_files_exits_zero_without_verifying(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(verify_pr, "get_changed_python_files", lambda base_ref: [])
    monkeypatch.setattr(verify_pr, "verify_existing_code", lambda *a, **k: called.append(1))
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--diff-base", "main"])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()

    assert exc_info.value.code == 0
    assert called == []
    assert "nothing to verify" in capsys.readouterr().out


# --- targets_for_file (function-level diff extraction) -----------------


def test_targets_for_file_verifies_whole_file_without_a_diff_base(tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("def f(): pass")

    targets = verify_pr.targets_for_file(str(f), None)
    assert targets == [(str(f), "def f(): pass")]


def test_targets_for_file_uses_each_extracted_function_when_present(monkeypatch, tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("def a(): pass\ndef b(): pass")

    monkeypatch.setattr(
        verify_pr,
        "get_changed_functions",
        lambda base_ref, file_path: [
            {"name": "a", "start_line": 1, "end_line": 1, "source": "def a(): pass\n"},
            {"name": "b", "start_line": 2, "end_line": 2, "source": "def b(): pass\n"},
        ],
    )

    targets = verify_pr.targets_for_file(str(f), "main")
    assert targets == [
        (f"{f}::a", "def a(): pass\n"),
        (f"{f}::b", "def b(): pass\n"),
    ]


def test_targets_for_file_falls_back_to_whole_file_when_no_functions_matched(monkeypatch, tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("CONST = 1\n")

    monkeypatch.setattr(verify_pr, "get_changed_functions", lambda base_ref, file_path: [])

    targets = verify_pr.targets_for_file(str(f), "main")
    assert targets == [(str(f), "CONST = 1\n")]


def test_targets_for_file_falls_back_to_whole_file_on_extraction_error(monkeypatch, tmp_path, capsys):
    f = tmp_path / "foo.py"
    f.write_text("def f(): pass")

    def raise_error(base_ref, file_path):
        raise RuntimeError("git diff against 'main' failed: unknown revision")

    monkeypatch.setattr(verify_pr, "get_changed_functions", raise_error)

    targets = verify_pr.targets_for_file(str(f), "main")
    assert targets == [(str(f), "def f(): pass")]
    assert "verifying whole file" in capsys.readouterr().out


def test_diff_base_labels_reports_by_function_when_extraction_succeeds(monkeypatch, tmp_path):
    f = tmp_path / "foo.py"
    f.write_text("def a(): pass\ndef b(): pass")

    monkeypatch.setattr(verify_pr, "get_changed_python_files", lambda base_ref: [str(f)])
    monkeypatch.setattr(
        verify_pr,
        "get_changed_functions",
        lambda base_ref, file_path: [
            {"name": "a", "start_line": 1, "end_line": 1, "source": "def a(): pass\n"},
        ],
    )
    calls = []
    monkeypatch.setattr(
        verify_pr,
        "verify_existing_code",
        lambda code, context="": calls.append(code)
        or {"verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": []},
    )
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--diff-base", "main"])

    with pytest.raises(SystemExit):
        verify_pr.main()

    assert calls == ["def a(): pass\n"]  # only the changed function was verified, not the whole file


def test_provider_failure_on_one_file_does_not_crash_the_rest(monkeypatch, tmp_path, capsys):
    # Live-observed: a reasoning Skeptic model can burn its whole token
    # budget and make find_bugs raise RuntimeError. One flaky file
    # shouldn't sink verification of every other changed file.
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("def a(): pass")
    f2.write_text("def b(): pass")

    def flaky(code, context=""):
        if "def a" in code:
            raise RuntimeError("Model x returned empty content (finish_reason='length').")
        return {"verdict": "no_bugs_found", "tests": [], "real_bugs": [], "invalid_tests": []}

    monkeypatch.setattr(verify_pr, "get_changed_python_files", lambda base_ref: [str(f1), str(f2)])
    monkeypatch.setattr(verify_pr, "verify_existing_code", flaky)
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--diff-base", "main"])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()

    assert exc_info.value.code == 0  # the surviving file found no bugs
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "finish_reason" in out


def test_diff_base_error_exits_with_code_2_and_clear_message(monkeypatch, capsys):
    def raise_error(base_ref):
        raise RuntimeError("git diff against 'bad-ref' failed: unknown revision")

    monkeypatch.setattr(verify_pr, "get_changed_python_files", raise_error)
    monkeypatch.setattr(sys, "argv", ["verify_pr.py", "--diff-base", "bad-ref"])

    with pytest.raises(SystemExit) as exc_info:
        verify_pr.main()

    assert exc_info.value.code == 2
    assert "Could not determine changed files" in capsys.readouterr().out
