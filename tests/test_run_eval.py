"""Tests for eval/run_eval.py's report-writing safety net: a run that
completes zero problems must never overwrite an existing, real report
with an empty one. run_breakpoint_mode/run_baseline_mode are monkeypatched
— this never touches a real LLM."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import run_eval


def test_write_report_used_directly_still_works(tmp_path, monkeypatch):
    # write_report itself has no overwrite guard by design — the guard
    # lives in main(), which decides whether to call it at all. Confirms
    # write_report is unaffected by that guard for a normal, non-empty run.
    report_path = tmp_path / "report.md"
    monkeypatch.setitem(run_eval.REPORT_PATH, "python", report_path)
    run_eval.write_report(
        results=[
            {
                "id": "a", "difficulty": "easy", "breakpoint_round1_bug_found": True,
                "breakpoint_verdict": "converged", "breakpoint_rounds_taken": 1,
                "breakpoint_bugs_caught": 1, "invalid_tests_filtered": 0,
                "baseline_self_check": "correct",
            }
        ],
        total_problems=1,
        stopped_early=False,
    )
    assert "N=1" in report_path.read_text()


def test_main_refuses_to_overwrite_existing_report_with_zero_results(tmp_path, monkeypatch):
    report_path = tmp_path / "report.md"
    problems_path = tmp_path / "problems.json"
    original_content = "# a real, previously-earned report\n\nN=5, real data here.\n"
    report_path.write_text(original_content)
    problems_path.write_text(
        '[{"id": "p1", "difficulty": "easy", "request": "do a thing"}]'
    )

    monkeypatch.setitem(run_eval.REPORT_PATH, "python", report_path)
    monkeypatch.setitem(run_eval.PROBLEMS_PATH, "python", problems_path)

    def always_fails(request, language="python"):
        raise RuntimeError("provider hiccup — nothing ever completes this run")

    monkeypatch.setattr(run_eval, "run_breakpoint_mode", always_fails)
    monkeypatch.setattr(sys, "argv", ["run_eval.py"])

    run_eval.main()

    # The existing report must survive untouched — a 0-result run has
    # nothing worth reporting and must not destroy real prior data.
    assert report_path.read_text() == original_content


def test_main_writes_an_honest_empty_report_when_none_existed_before(tmp_path, monkeypatch, capsys):
    report_path = tmp_path / "report.md"
    problems_path = tmp_path / "problems.json"
    problems_path.write_text(
        '[{"id": "p1", "difficulty": "easy", "request": "do a thing"}]'
    )

    monkeypatch.setitem(run_eval.REPORT_PATH, "python", report_path)
    monkeypatch.setitem(run_eval.PROBLEMS_PATH, "python", problems_path)

    def always_fails(request, language="python"):
        raise RuntimeError("provider hiccup")

    monkeypatch.setattr(run_eval, "run_breakpoint_mode", always_fails)
    monkeypatch.setattr(sys, "argv", ["run_eval.py"])

    run_eval.main()

    # No prior report to protect — writing an honest "N=0" report is fine,
    # it documents the failure rather than hiding it.
    assert report_path.exists()
    assert "N=0" in report_path.read_text()


def test_main_writes_normally_when_results_are_non_empty(tmp_path, monkeypatch):
    report_path = tmp_path / "report.md"
    problems_path = tmp_path / "problems.json"
    old_content = "# stale report from a previous run\n"
    report_path.write_text(old_content)
    problems_path.write_text(
        '[{"id": "p1", "difficulty": "easy", "request": "do a thing"}]'
    )

    monkeypatch.setitem(run_eval.REPORT_PATH, "python", report_path)
    monkeypatch.setitem(run_eval.PROBLEMS_PATH, "python", problems_path)

    monkeypatch.setattr(
        run_eval,
        "run_breakpoint_mode",
        lambda request, language="python": {
            "spec": {}, "round1_code": "def f(): pass", "round1_bug_found": False,
            "round1_failing_tests": [], "verdict": "converged", "rounds_taken": 1,
            "bugs_caught": 0, "invalid_tests_filtered": 0,
        },
    )
    monkeypatch.setattr(run_eval, "run_baseline_mode", lambda spec, code: "correct")
    monkeypatch.setattr(sys, "argv", ["run_eval.py"])

    run_eval.main()

    # A real result overwrites stale prior content, same as always.
    new_content = report_path.read_text()
    assert new_content != old_content
    assert "N=1" in new_content
