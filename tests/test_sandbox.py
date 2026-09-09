import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import sandbox
from sandbox import _docker_available, run_test

# Whichever backend is actually reachable in this environment — these
# core tests assert real behavior (pass/fail/timeout), not which backend
# produced it, so they hold regardless of whether Docker is installed
# here. The Docker-specific tests below are the ones that only prove
# something new when Docker is actually available (e.g. real CI).
EXPECTED_ISOLATION = "docker" if _docker_available("python") else "subprocess"

requires_docker = pytest.mark.skipif(
    not _docker_available("python"), reason="Docker not available in this environment"
)
requires_node = pytest.mark.skipif(
    shutil.which("node") is None and not _docker_available("javascript"),
    reason="Neither Docker nor a local node binary is available",
)


def test_docker_images_are_pinned_by_digest():
    # A bare tag (e.g. "python:3.12-slim") can be silently republished by
    # its maintainer — pinning by digest means the sandbox always runs the
    # exact image that was reviewed, not whatever the tag currently points
    # to. Guards against someone reverting a sandbox._LANGUAGES entry back
    # to a plain tag without noticing.
    for language, config in sandbox._LANGUAGES.items():
        image = config["docker_image"]
        assert "@sha256:" in image, language
        digest = image.split("@sha256:", 1)[1]
        assert len(digest) == 64, language
        assert all(c in "0123456789abcdef" for c in digest), language


def test_passing_assertion():
    code = "def add(a, b):\n    return a + b\n"
    test = "def test_add():\n    assert add(2, 3) == 5\n"
    result = run_test(code, test)
    assert result.passed is True
    assert result.timed_out is False
    assert result.isolation == EXPECTED_ISOLATION


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


def test_rejects_unsupported_language():
    with pytest.raises(ValueError, match="Unsupported language"):
        run_test("code", "test", language="ruby")


def test_falls_back_to_subprocess_when_docker_invocation_fails(monkeypatch):
    # Docker looked available at the startup check but this specific
    # invocation fails (e.g. the daemon died mid-run) — one flaky call
    # shouldn't lose the test, it should fall back and still run it.
    monkeypatch.setattr(sandbox, "_docker_available", lambda language: True)

    def raise_error(tmpdir, timeout_seconds, language):
        raise OSError("docker daemon went away")

    monkeypatch.setattr(sandbox, "_run_in_docker", raise_error)

    code = "def add(a, b):\n    return a + b\n"
    test = "def test_add():\n    assert add(2, 3) == 5\n"
    result = run_test(code, test)
    assert result.passed is True
    assert result.isolation == "subprocess"


# --- JavaScript ----------------------------------------------------------
# Real execution, not mocked — via Docker if available, otherwise a local
# `node` binary (present on this dev machine even without Docker).


@requires_node
def test_javascript_passing_assertion():
    code = "function add(a, b) {\n  return a + b;\n}\n"
    test = "function test_add() {\n  assert.strictEqual(add(2, 3), 5);\n}\n"
    result = run_test(code, test, language="javascript")
    assert result.passed is True
    assert result.timed_out is False


@requires_node
def test_javascript_failing_assertion():
    code = "function add(a, b) {\n  return a - b;\n}\n"
    test = "function test_add() {\n  assert.strictEqual(add(2, 3), 5);\n}\n"
    result = run_test(code, test, language="javascript")
    assert result.passed is False
    assert "AssertionError" in result.stderr


@requires_node
def test_javascript_unhandled_exception():
    code = "function boom() {\n  throw new Error('kaboom');\n}\n"
    test = "function test_boom() {\n  boom();\n}\n"
    result = run_test(code, test, language="javascript")
    assert result.passed is False
    assert "kaboom" in result.stderr


@requires_node
def test_javascript_infinite_loop_times_out():
    code = "function loopForever() {\n  while (true) {}\n}\n"
    test = "function test_loop() {\n  loopForever();\n}\n"
    result = run_test(code, test, timeout_seconds=2, language="javascript")
    assert result.timed_out is True
    assert result.passed is False


def test_javascript_without_node_or_docker_raises_clear_error(monkeypatch):
    monkeypatch.setattr(sandbox, "_docker_available", lambda language: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="JavaScript sandboxing needs"):
        run_test("function f() {}", "function test_f() { f(); }", language="javascript")


# --- Docker-specific isolation guarantees -------------------------------
# These only prove something when Docker is actually reachable — the bare
# subprocess sandbox never blocked network or filesystem access at the OS
# level, so there's nothing new to assert without Docker running. Skipped
# here if Docker isn't installed (e.g. this dev machine); they run for
# real in CI, where GitHub's hosted runners have a live Docker daemon.


@requires_docker
def test_docker_sandbox_blocks_real_network_access():
    code = (
        "def can_reach_network():\n"
        "    import socket\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
        "        return True\n"
        "    except OSError:\n"
        "        return False\n"
    )
    test = "def test_no_network():\n    assert can_reach_network() is False\n"
    result = run_test(code, test)
    assert result.isolation == "docker"
    assert result.passed is True  # the network call really did fail


@requires_docker
def test_docker_sandbox_has_a_read_only_root_filesystem():
    code = (
        "def try_write_outside_sandbox():\n"
        "    try:\n"
        "        with open('/etc/breakpoint_pwned', 'w') as f:\n"
        "            f.write('x')\n"
        "        return True\n"
        "    except OSError:\n"
        "        return False\n"
    )
    test = "def test_write_blocked():\n    assert try_write_outside_sandbox() is False\n"
    result = run_test(code, test)
    assert result.isolation == "docker"
    assert result.passed is True  # the write really was rejected


@requires_docker
def test_docker_sandbox_runs_as_a_non_root_user():
    code = "def current_uid():\n    import os\n    return os.getuid()\n"
    test = "def test_not_root():\n    assert current_uid() != 0\n"
    result = run_test(code, test)
    assert result.isolation == "docker"
    assert result.passed is True
