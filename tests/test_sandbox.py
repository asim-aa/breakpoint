import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandbox import run_test


def test_passing_assertion():
    code = "def add(a, b):\n    return a + b\n"
    test = "def test_add():\n    assert add(2, 3) == 5\n"
    result = run_test(code, test)
    assert result.passed is True
    assert result.timed_out is False


def test_failing_assertion():
    code = "def add(a, b):\n    return a - b\n"
    test = "def test_add():\n    assert add(2, 3) == 5\n"
    result = run_test(code, test)
    assert result.passed is False
    assert "AssertionError" in result.stderr


def test_infinite_loop_times_out():
    code = "def loop_forever():\n    while True:\n        pass\n"
    test = "def test_loop():\n    loop_forever()\n"
    result = run_test(code, test, timeout_seconds=2)
    assert result.timed_out is True
    assert result.passed is False


def test_unhandled_exception():
    code = "def divide(a, b):\n    return a / b\n"
    test = "def test_divide():\n    divide(1, 0)\n"
    result = run_test(code, test)
    assert result.passed is False
    assert result.error is not None
    assert "ZeroDivisionError" in result.stderr
