"""Tests for nodes/validator.py. The static syntax check needs no LLM call
and is tested directly. The semantic-judgment path (which does call an
LLM) is tested with nodes.validator.complete monkeypatched, so this whole
suite runs for $0 with no network access — consistent with how the rest
of this test directory avoids touching a real provider."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nodes.validator as validator
from nodes.validator import _static_syntax_check, validate_test


def test_static_check_catches_real_walrus_misuse():
    # The exact real failure that motivated building this validator: a
    # live Skeptic test misused the walrus operator inside a bare assert,
    # which is invalid syntax, and — before this fix — was indistinguishable
    # from a real bug, permanently blocking convergence.
    broken = (
        "def test_negative_numbers():\n"
        "    assert nums := [-3, 4, 7, 2]\n"
        "    assert two_sum_indices(nums, 1) in ([0, 3], [3, 0])\n"
    )
    result = _static_syntax_check(broken)
    assert result is not None
    assert "does not compile" in result


def test_static_check_passes_valid_syntax():
    fine = "def test_empty():\n    assert f([]) == []\n"
    assert _static_syntax_check(fine) is None


def test_static_check_passes_syntactically_valid_but_semantically_false_assertion():
    # A mathematically false assertion (-3 + -1 != 1) is syntactically
    # fine — catching that it's WRONG requires the semantic (LLM) check,
    # not the free static one. This test documents that boundary.
    false_assertion = "def test_x():\n    assert -3 + -1 == 1\n"
    assert _static_syntax_check(false_assertion) is None


def test_validate_test_short_circuits_on_syntax_error_without_calling_llm(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("complete() should not be called for a syntactically broken test")

    monkeypatch.setattr(validator, "complete", fail_if_called)

    broken = "def test_x():\n    assert nums := [1, 2]\n"
    result = validate_test(spec={}, test_code=broken, error="SyntaxError")
    assert result["valid"] is False
    assert "does not compile" in result["reason"]


def test_validate_test_parses_llm_json_verdict(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    monkeypatch.setattr(
        validator, "complete", lambda **kwargs: '{"valid": false, "reason": "contradicts spec"}'
    )
    result = validate_test(spec={}, test_code="def test_x():\n    assert True\n", error=None)
    assert result["valid"] is False
    assert result["reason"] == "contradicts spec"


def test_validate_test_defaults_to_valid_on_provider_failure(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")

    def raise_error(**kwargs):
        raise RuntimeError("provider hiccup")

    monkeypatch.setattr(validator, "complete", raise_error)
    result = validate_test(spec={}, test_code="def test_x():\n    assert True\n", error=None)
    # Defaulting to valid on an inconclusive check is a deliberate choice:
    # the risk of a false "invalid" (silently discarding a real bug) is
    # worse than retrying once more against a test that turns out bad.
    assert result["valid"] is True


def test_validate_test_defaults_to_valid_on_malformed_llm_json(monkeypatch):
    monkeypatch.setenv("PROVER_MODEL", "fake-model")
    monkeypatch.setattr(validator, "complete", lambda **kwargs: "not valid json at all")
    result = validate_test(spec={}, test_code="def test_x():\n    assert True\n", error=None)
    assert result["valid"] is True
