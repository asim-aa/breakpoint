"""Executes Prover code + Skeptic tests in an isolated subprocess.

This is the ground truth for the whole project: pass/fail comes from real
execution, never from a model's opinion.
"""

import functools
import os
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

DOCKER_IMAGE = "python:3.12-slim"
DOCKER_MEMORY_LIMIT = "256m"
DOCKER_CPU_LIMIT = "1"
DOCKER_PIDS_LIMIT = "64"


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


@functools.lru_cache(maxsize=1)
def _docker_available() -> bool:
    """True if a real Docker daemon is reachable AND the sandbox image is
    ready to run offline. Checked (and the image pulled, if missing) once
    per process — pulling inline during an actual sandboxed run would blow
    a 5-second test timeout on a cold image cache."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False

    have_image = subprocess.run(
        ["docker", "image", "inspect", DOCKER_IMAGE], capture_output=True
    )
    if have_image.returncode != 0:
        try:
            subprocess.run(
                ["docker", "pull", DOCKER_IMAGE], capture_output=True, timeout=180, check=True
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
    return True


def _run_in_docker(tmpdir: str, timeout_seconds: int) -> SandboxResult:
    """Runs the sandboxed script inside a locked-down container: no
    network at all (not just stripped env vars), every Linux capability
    dropped, no privilege escalation, a read-only root filesystem, a
    process-count ceiling against fork bombs, and cgroup-enforced
    CPU/memory limits that (unlike RLIMIT_AS) work reliably regardless of
    host platform or the interpreter's own allocator. Runs as the host's
    own uid:gid — not root, and avoids bind-mount permission mismatches
    from a fixed low-privilege uid not owning the mounted temp dir."""
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
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-v",
        f"{tmpdir}:/sandbox:ro",
        "-w",
        "/sandbox",
        DOCKER_IMAGE,
        "python",
        "run.py",
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


def _run_in_subprocess(tmpdir: str, script_path: Path, timeout_seconds: int) -> SandboxResult:
    # Strip the environment to the minimum needed to run Python — no
    # inherited API keys, no network-relevant env vars.
    minimal_env = {"PATH": "/usr/bin:/bin"}

    kwargs = {}
    if HAS_RESOURCE:
        kwargs["preexec_fn"] = _preexec_fn
    # KNOWN GAP: on platforms without the resource module (or where it's
    # disabled above), no CPU/memory ceiling is enforced beyond the
    # wall-clock timeout below. See README "Security notes". This whole
    # path is itself the fallback for when Docker isn't available — see
    # _docker_available() and run_test() below.

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
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


def run_test(code: str, test_code: str, timeout_seconds: int = 5) -> SandboxResult:
    combined = (
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

    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / "run.py"
        script_path.write_text(combined)

        if _docker_available():
            try:
                return _run_in_docker(tmpdir, timeout_seconds)
            except (subprocess.SubprocessError, OSError):
                # Docker was reachable at the availability check but this
                # specific invocation failed (e.g. daemon died mid-run) —
                # fall back to the subprocess sandbox rather than losing
                # this test entirely.
                pass

        return _run_in_subprocess(tmpdir, script_path, timeout_seconds)
