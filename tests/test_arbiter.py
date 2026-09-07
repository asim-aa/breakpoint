"""Tests for nodes/arbiter.py's verdict, bug-count, and confidence logic —
pure computation over already-computed history, no LLM calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.arbiter import arbitrate


def _result(test_code, passed, valid=True):
    return {"test_code": test_code, "passed": passed, "valid": valid}


def test_converged_round_one_has_max_confidence():
    state = {
        "round_passed": True,
        "history": [{"round": 1, "results": [_result("t1", True), _result("t2", True)]}],
    }
    report = arbitrate(state)
    assert report["verdict"] == "converged"
    assert report["rounds_taken"] == 1
    assert report["confidence"] == 1.0
    assert report["bugs_caught"] == 0


def test_converged_after_retries_has_lower_confidence():
    state = {
        "round_passed": True,
        "history": [
            {"round": 1, "results": [_result("t1", False), _result("t2", True)]},
            {"round": 2, "results": [_result("t1", True), _result("t2", True)]},
        ],
    }
    report = arbitrate(state)
    assert report["verdict"] == "converged"
    assert report["rounds_taken"] == 2
    assert report["confidence"] < 1.0
    assert report["bugs_caught"] == 1  # t1 failed once, deduped


def test_unresolved_scales_confidence_by_passed_fraction():
    mostly_passing = {
        "round_passed": False,
        "history": [{"round": 1, "results": [_result("t1", False)] + [_result(f"t{i}", True) for i in range(9)]}],
    }
    all_failing = {
        "round_passed": False,
        "history": [{"round": 1, "results": [_result("t1", False), _result("t2", False)]}],
    }
    mostly_report = arbitrate(mostly_passing)
    all_fail_report = arbitrate(all_failing)
    assert mostly_report["verdict"] == "unresolved"
    assert mostly_report["confidence"] > all_fail_report["confidence"]
    assert all_fail_report["confidence"] == 0.0


def test_invalid_tests_excluded_from_bugs_caught():
    # This is the Validator's whole point: a failing test that was judged
    # invalid shouldn't count as a real bug.
    state = {
        "round_passed": False,
        "history": [
            {
                "round": 1,
                "results": [_result("real_bug", False, valid=True), _result("bad_test", False, valid=False)],
            }
        ],
    }
    report = arbitrate(state)
    assert report["bugs_caught"] == 1


def test_invalid_failures_count_toward_confidence_as_if_passed():
    # An invalid failing test isn't evidence the Prover is wrong, so it
    # shouldn't drag confidence down the way a real failure would.
    all_invalid = {
        "round_passed": False,
        "history": [{"round": 1, "results": [_result("bad1", False, valid=False), _result("bad2", False, valid=False)]}],
    }
    all_real = {
        "round_passed": False,
        "history": [{"round": 1, "results": [_result("real1", False, valid=True), _result("real2", False, valid=True)]}],
    }
    assert arbitrate(all_invalid)["confidence"] > arbitrate(all_real)["confidence"]


def test_missing_valid_key_defaults_to_valid_for_backward_compatibility():
    # State produced before the Validator existed won't have a "valid" key
    # on each result at all — arbitrate() must not crash on that, and
    # should treat those failures as real (the only behavior that existed
    # at the time).
    state = {
        "round_passed": False,
        "history": [{"round": 1, "results": [{"test_code": "t1", "passed": False}]}],
    }
    report = arbitrate(state)
    assert report["bugs_caught"] == 1


def test_empty_history_does_not_crash():
    state = {"round_passed": False, "history": []}
    report = arbitrate(state)
    assert report["rounds_taken"] == 0
    assert report["total_tests_run"] == 0
    assert report["bugs_caught"] == 0
    assert report["confidence"] == 0.0
