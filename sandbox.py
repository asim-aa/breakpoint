"""Executes Prover code + Skeptic tests in an isolated subprocess.

This is the ground truth for the whole project: pass/fail comes from real
execution, never from a model's opinion.
"""

import subprocess
import sys
import tempfile
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


@dataclass
class SandboxResult:
    passed: bool
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None


def _preexec_fn():
    # Runs in the child process after fork, before exec — applies hard
    # resource ceilings independent of anything the sandboxed code does.
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))


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

        # Strip the environment to the minimum needed to run Python — no
        # inherited API keys, no network-relevant env vars.
        minimal_env = {"PATH": "/usr/bin:/bin"}

        kwargs = {}
        if HAS_RESOURCE:
            kwargs["preexec_fn"] = _preexec_fn
        # KNOWN GAP: on platforms without the resource module (or where it's
        # disabled above), no CPU/memory ceiling is enforced beyond the
        # wall-clock timeout below. See README "Security notes".

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
