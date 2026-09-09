"""Executes Prover code + Skeptic tests in an isolated subprocess.

This is the ground truth for the whole project: pass/fail comes from real
execution, never from a model's opinion.
"""

import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

# Preload on Linux only, guarded so importing this module never fails on
# platforms (e.g. macOS dev machines) where the resource module exists but
# RLIMIT_AS behaves unreliably with the Python interpreter's own allocator.
try:
    import resource

    HAS_RESOURCE = sys.platform.startswith("linux")
except ImportError:
    resource = None
    HAS_RESOURCE = False

CPU_LIMIT_SECONDS = 5
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024  # 256 MB

DOCKER_MEMORY_LIMIT = "256m"
DOCKER_CPU_LIMIT = "1"
DOCKER_PIDS_LIMIT = "64"

# Both pinned by digest, not just tag — a compromised/republished tag
# can't silently change what runs inside the sandbox. Manifest-LIST
# digests (cover every platform Docker publishes for the image, e.g.
# amd64 and arm64), fetched live from the registry, not guessed:
# https://hub.docker.com/_/python — python:3.12-slim as of 2026-09-09,
#   image version 3.12.14-slim-trixie.
# https://hub.docker.com/_/node — node:20-slim as of 2026-09-09,
#   image version 20-bookworm-slim.
# Re-pin periodically to pick up security patches — an old digest never
# updates itself.
_LANGUAGES = {
    "python": {
        "docker_image": "python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea",
        "filename": "run.py",
        "docker_cmd": ["python", "run.py"],
        "docker_env": ["PYTHONDONTWRITEBYTECODE=1"],
    },
    "javascript": {
        "docker_image": "node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0",
        "filename": "run.js",
        "docker_cmd": ["node", "run.js"],
        "docker_env": [],
    },
}

_JS_TEST_NAME_RE = re.compile(r"(?m)^function\s+(test_\w+)\s*\(")


@dataclass
class SandboxResult:
    passed: bool
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None
    isolation: str = "subprocess"  # "docker" when real OS-level isolation actually ran


def _preexec_fn():
    # Runs in the child process after fork, before exec — applies hard
    # resource ceilings independent of anything the sandboxed code does.
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))


def _build_harness(code: str, test_code: str, language: str) -> str:
    if language == "python":
        return (
            f"{code}\n\n"
            f"{test_code}\n\n"
            "if __name__ == '__main__':\n"
            "    import inspect, sys as _sys\n"
            "    _mod = _sys.modules['__main__']\n"
            "    _test_fns = [v for k, v in vars(_mod).items()\n"
            "                 if k.startswith('test_') and inspect.isfunction(v)]\n"
            "    for _fn in _test_fns:\n"
            "        _fn()\n"
        )

    # JavaScript: a file run via `node file.js` wraps the whole file in a
    # module-scope function, so top-level `function` declarations never
    # land on `global` the way Python's module attributes do — there's no
    # equivalent of `vars(_mod)` to auto-discover them. Instead, extract
    # each test_* function's name directly from test_code (by this point
    # it's already a single self-contained test — see
    # skeptic._split_multi_def_tests) and call it by name explicitly.
    test_names = _JS_TEST_NAME_RE.findall(test_code)
    calls = "\n".join(f"{name}();" for name in test_names)
    return f"const assert = require('assert');\n\n{code}\n\n{test_code}\n\n{calls}\n"


@functools.lru_cache(maxsize=None)
def _docker_available(language: str) -> bool:
    """True if a real Docker daemon is reachable AND the sandbox image for
    this language is ready to run offline. Checked (and the image pulled,
    if missing) once per language per process — pulling inline during an
    actual sandboxed run would blow a 5-second test timeout on a cold
    image cache."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False

    image = _LANGUAGES[language]["docker_image"]
    have_image = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if have_image.returncode != 0:
        try:
            subprocess.run(["docker", "pull", image], capture_output=True, timeout=180, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
    return True


def _run_in_docker(tmpdir: str, timeout_seconds: int, language: str) -> SandboxResult:
    """Runs the sandboxed script inside a locked-down container: no
    network at all (not just stripped env vars), every Linux capability
    dropped, no privilege escalation, a read-only root filesystem, a
    process-count ceiling against fork bombs, and cgroup-enforced
    CPU/memory limits that (unlike RLIMIT_AS) work reliably regardless of
    host platform or the interpreter's own allocator. Runs as the host's
    own uid:gid — not root, and avoids bind-mount permission mismatches
    from a fixed low-privilege uid not owning the mounted temp dir."""
    lang = _LANGUAGES[language]
    container_name = f"breakpoint-sandbox-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--memory",
        DOCKER_MEMORY_LIMIT,
        "--memory-swap",
        DOCKER_MEMORY_LIMIT,  # no swap beyond the memory limit itself
        "--pids-limit",
        DOCKER_PIDS_LIMIT,
        "--cpus",
        DOCKER_CPU_LIMIT,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]
    for env_var in lang["docker_env"]:
        cmd += ["-e", env_var]
    cmd += [
        "-v",
        f"{tmpdir}:/sandbox:ro",
        "-w",
        "/sandbox",
        lang["docker_image"],
        *lang["docker_cmd"],
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as e:
        # subprocess's timeout kills the `docker run` client, not
        # necessarily the container the daemon is still running — kill it
        # explicitly so a timed-out run doesn't leave an orphaned process.
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        return SandboxResult(
            passed=False,
            stdout=(e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=(e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or ""),
            timed_out=True,
            error="timed out",
            isolation="docker",
        )

    passed = proc.returncode == 0
    error = None if passed else f"exited with code {proc.returncode}"
    return SandboxResult(
        passed=passed, stdout=proc.stdout, stderr=proc.stderr, timed_out=False, error=error, isolation="docker"
    )


def _subprocess_command(script_path: Path, language: str) -> list[str]:
    if language == "python":
        return [sys.executable, str(script_path)]

    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "JavaScript sandboxing needs either Docker or a local `node` binary; neither was found."
        )
    return [node, str(script_path)]


def _run_in_subprocess(tmpdir: str, script_path: Path, timeout_seconds: int, language: str) -> SandboxResult:
    # Strip the environment to the minimum needed to run the interpreter —
    # no inherited API keys, no network-relevant env vars.
    minimal_env = {"PATH": "/usr/bin:/bin"}

    kwargs = {}
    if HAS_RESOURCE and language == "python":
        # preexec_fn applies rlimits to the child we're about to exec —
        # meaningful for `python run.py`. Node has no equivalent RLIMIT_AS
        # reliability concern to work around here, and this whole path is
        # itself already the fallback for when Docker (which enforces real
        # cgroup limits for either language) isn't available.
        kwargs["preexec_fn"] = _preexec_fn
    # KNOWN GAP: on platforms without the resource module (or where it's
    # disabled above), no CPU/memory ceiling is enforced beyond the
    # wall-clock timeout below. See README "Security notes".

    try:
        proc = subprocess.run(
            _subprocess_command(script_path, language),
            cwd=tmpdir,
            env=minimal_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            **kwargs,
        )
    except subprocess.TimeoutExpired as e:
        return SandboxResult(
            passed=False,
            stdout=(e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=(e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or ""),
            timed_out=True,
            error="timed out",
        )

    passed = proc.returncode == 0
    error = None if passed else f"exited with code {proc.returncode}"
    return SandboxResult(
        passed=passed,
        stdout=proc.stdout,
        stderr=proc.stderr,
        timed_out=False,
        error=error,
    )


def run_test(code: str, test_code: str, timeout_seconds: int = 5, language: str = "python") -> SandboxResult:
    if language not in _LANGUAGES:
        raise ValueError(f"Unsupported language: {language!r} (supported: {list(_LANGUAGES)})")

    combined = _build_harness(code, test_code, language)

    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / _LANGUAGES[language]["filename"]
        script_path.write_text(combined)

        if _docker_available(language):
            try:
                return _run_in_docker(tmpdir, timeout_seconds, language)
            except (subprocess.SubprocessError, OSError):
                # Docker was reachable at the availability check but this
                # specific invocation failed (e.g. daemon died mid-run) —
                # fall back to the subprocess sandbox rather than losing
                # this test entirely.
                pass

        return _run_in_subprocess(tmpdir, script_path, timeout_seconds, language)
