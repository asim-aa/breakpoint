# Breakpoint

An adversarial code-generation system. One agent (the Prover) writes an
implementation; a second agent (the Skeptic) writes tests aimed at breaking
it. Tests run for real in a sandbox — verdicts come from execution, not from
an LLM's opinion of correctness.

## Structure

- `graph.py` — LangGraph wiring (framer → prover → skeptic → sandbox → loop → arbiter)
- `sandbox.py` — isolated subprocess execution of Prover code + Skeptic tests
- `state.py` — shared LangGraph state schema
- `nodes/` — framer, prover, skeptic, arbiter
- `tests/` — pytest suite

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env  # fill in OPENROUTER_API_KEY, PROVER_MODEL, SKEPTIC_MODEL
./venv/bin/pytest
```

## Security notes

`sandbox.run_test` runs generated code in a separate subprocess (never
`exec()` in-process), in a fresh temp directory, with a stripped-down
environment (no inherited API keys) and a hard wall-clock timeout.

**Known gaps, not yet solved:**

- **No OS-level sandboxing.** The subprocess can still make syscalls like
  `os.system`, read/write outside the temp dir, or open network sockets —
  confirmed manually: sandboxed code calling `os.system("echo pwned")`
  executes successfully. There is no seccomp/container/VM boundary here.
  Treat this as a code-review harness for trusted-ish LLM output on a
  single dev machine, not a boundary safe for genuinely untrusted or
  hostile code.
- **CPU/memory resource limits (`RLIMIT_CPU`, `RLIMIT_AS` via the `resource`
  module) are only applied on Linux.** On macOS (and other non-Linux
  platforms) the only enforced ceiling is the wall-clock `timeout_seconds`
  passed to `subprocess.run` — a process that allocates a lot of memory but
  returns before the timeout will not be stopped.
- **No network blocking is implemented at this layer.** The stripped
  environment removes API keys, but does not prevent a sandboxed process
  from opening a socket if the host machine allows it.

These are acceptable for a personal dev-machine prototype; before running
this against fully untrusted code, add a real container/VM boundary (e.g.
gVisor, Firecracker, or Docker with a locked-down seccomp profile and
`--network none`).
