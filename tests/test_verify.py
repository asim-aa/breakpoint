"""Tests for verify.py's orchestration logic — infer_spec_from_code,
find_bugs, run_test, and validate_test are all monkeypatched, since each
is already tested in isolation elsewhere. This only tests that
verify_existing_code wires them together correctly: no retry loop, a
real bug produces "bugs_found", an invalid-only failure produces
"no_bugs_found", and no LLM call happens for tests that flat-out pass."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import verify
from sandbox import SandboxResult


def _passing():
    return SandboxResult(passed=True, stdout="", stderr="", timed_out=False, error=None)


def _failing(stderr="AssertionError: boom"):
    return SandboxResult(passed=False, stdout="", stderr=stderr, timed_out=False, error="exited with code 1")


def test_verify_reports_no_bugs_found_when_everything_passes(monkeypatch):
    monkeypatch.setattr(verify, "infer_spec_from_code", lambda code, context="": {"function_name": "f"})
    monkeypatch.setattr(verify, "find_bugs", lambda spec, code: ["def test_a():\n    pass"])
    monkeypatch.setattr(verify, "run_test", lambda code, test_code: _passing())

    result = verify.verify_existing_code("def f(x):\n    return x")

    assert result["verdict"] == "no_bugs_found"
    assert result["real_bugs"] == []


def test_verify_reports_bugs_found_for_a_real_failure(monkeypatch):
    monkeypatch.setattr(verify, "infer_spec_from_code", lambda code, context="": {"function_name": "f"})
    monkeypatch.setattr(verify, "find_bugs", lambda spec, code: ["def test_none():\n    assert f(None)"])
    monkeypatch.setattr(verify, "run_test", lambda code, test_code: _failing())
    monkeypatch.setattr(verify, "validate_test", lambda spec, test_code, error: {"valid": True, "reason": ""})

    result = verify.verify_existing_code("def f(x):\n    return x")

    assert result["verdict"] == "bugs_found"
    assert len(result["real_bugs"]) == 1
    assert result["invalid_tests"] == []


def test_verify_does_not_call_validator_for_passing_tests(monkeypatch):
    validator_calls = []
    monkeypatch.setattr(verify, "infer_spec_from_code", lambda code, context="": {"function_name": "f"})
    monkeypatch.setattr(verify, "find_bugs", lambda spec, code: ["def test_a():\n    pass"])
    monkeypatch.setattr(verify, "run_test", lambda code, test_code: _passing())
    monkeypatch.setattr(verify, "validate_test", lambda *a, **k: validator_calls.append(1))

    verify.verify_existing_code("def f(x):\n    return x")
    assert validator_calls == []  # a passing test is never sent to the Validator


def test_verify_excludes_invalid_failures_from_verdict(monkeypatch):
    # The exact scenario the Validator exists for: a failing test that's
    # itself invalid must not count as a real bug.
    monkeypatch.setattr(verify, "infer_spec_from_code", lambda code, context="": {"function_name": "f"})
    monkeypatch.setattr(verify, "find_bugs", lambda spec, code: ["def test_bad():\n    assert -3 + -1 == 1"])
    monkeypatch.setattr(verify, "run_test", lambda code, test_code: _failing())
    monkeypatch.setattr(verify, "validate_test", lambda spec, test_code, error: {"valid": False, "reason": "false assertion"})

    result = verify.verify_existing_code("def f(x):\n    return x")

    assert result["verdict"] == "no_bugs_found"
    assert result["real_bugs"] == []
    assert len(result["invalid_tests"]) == 1


def test_verify_passes_context_through_to_spec_inference(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        verify, "infer_spec_from_code",
        lambda code, context="": captured.setdefault("context", context) or {"function_name": "f"},
    )
    monkeypatch.setattr(verify, "find_bugs", lambda spec, code: [])

    verify.verify_existing_code("def f(x):\n    return x", context="fixes a null bug")
    assert captured["context"] == "fixes a null bug"


def test_verify_handles_zero_generated_tests(monkeypatch):
    monkeypatch.setattr(verify, "infer_spec_from_code", lambda code, context="": {"function_name": "f"})
    monkeypatch.setattr(verify, "find_bugs", lambda spec, code: [])

    result = verify.verify_existing_code("def f(x):\n    return x")
    assert result["verdict"] == "no_bugs_found"
    assert result["tests"] == []
