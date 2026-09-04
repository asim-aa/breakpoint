# Breakpoint

An adversarial code-generation system. One agent (the **Prover**) writes an
implementation; a second agent (the **Skeptic**), running on a *different*
model, writes tests aimed at breaking it. The tests run for real in an
isolated subprocess sandbox — verdicts come from actual execution, never
from an LLM's opinion of whether the code "looks correct."

If the Skeptic's tests fail, the failure trace goes back to the Prover for
another attempt, bounded by a round budget. An Arbiter produces a final
verdict, confidence score, and bug count once the loop stops (converged or
round-budget exhausted), and every run is persisted to SQLite.

```
Framer ──▶ Prover ──▶ Skeptic ──▶ Sandbox ──▶ [round < max? loop to Prover] ──▶ Arbiter
 request      code      tests      real exec         : else                     verdict
   →           →          →           →                                      + confidence
  spec                                                                       + persistence
```

## Status

V1–V4 of the build plan are implemented and verified:

| Phase | What it adds | Status |
|---|---|---|
| V1 | Framer + Prover, real sandbox, no adversary | ✅ done |
| V2 | Skeptic (different model) + full LangGraph wiring | ✅ done |
| V3 | Retry loop, bounded by round budget | ✅ done |
| V4a | Arbiter (verdict/confidence/coverage) + SQLite persistence + CLI | ✅ done |
| V4b | 6-problem eval vs. a non-adversarial self-check baseline | ⏳ built, blocked on OpenRouter free-tier daily quota (see below) |

## Structure

```
graph.py            LangGraph wiring: framer → prover → skeptic → sandbox → (loop) → arbiter
state.py            Shared LangGraph state schema (BreakpointState)
sandbox.py          Isolated subprocess execution — the ground truth for every verdict
llm.py              Thin OpenRouter chat-completions client (retries, token-budget handling)
storage.py          SQLite persistence: specs / attempts / tests tables
cli.py              `breakpoint run "<request>"` and `breakpoint history`
manual_run.py       Ad-hoc single-request runner with a full round-by-round trace printed
nodes/
  framer.py         Plain-English request → formal spec (JSON: inputs/output/constraints/examples)
  prover.py         Spec (+ optional failure trace) → implementation
  skeptic.py        Spec + code → adversarial pytest-style tests, on a different model than the Prover
  arbiter.py        Final verdict + confidence heuristic (formula documented in-code)
eval/
  problems.json     6 hand-written problems, mixed easy / boundary-heavy / spec-ambiguous
  run_eval.py       Runs the full graph + a baseline self-check on all 6, writes eval/report.md
tests/
  test_sandbox.py   The only tests that don't touch an LLM — sandbox correctness, self-contained
```

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

```
OPENROUTER_API_KEY=sk-or-...
PROVER_MODEL=nvidia/nemotron-3-super-120b-a12b:free
SKEPTIC_MODEL=minimax/minimax-m3:free
```

`PROVER_MODEL` and `SKEPTIC_MODEL` **must differ** — `skeptic.py` asserts
this at runtime, since the adversarial pressure depends on genuinely
different model blind spots, not prompt framing alone. Any two free-tier
OpenRouter chat models work; check `https://openrouter.ai/api/v1/models`
for the current `:free` roster, since it changes over time and specific
free slugs get retired.

Run the sandbox's own test suite (no API key needed, no LLM calls):

```bash
./venv/bin/pytest
```

## Usage

```bash
# One-off run with a full round-by-round trace
./venv/bin/python manual_run.py "merge overlapping intervals"

# CLI: run + persist to SQLite
./venv/bin/python cli.py run "parse a CSV row, respecting quoted commas" --max-rounds 3

# List past runs
./venv/bin/python cli.py history

# Run the eval (6 problems, writes eval/report.md)
./venv/bin/python eval/run_eval.py
```

## OpenRouter free-tier constraints (read before running the eval)

The free-tier daily cap is **50 requests/day, account-wide**, and it isn't
per-model — every `:free` model shares the same bucket. A single `cli.py
run` with a multi-round retry loop can use 5–10 requests; the 6-problem
eval can need 30–40+. Adding a one-time $10 balance to an OpenRouter
account raises this to 1000/day without changing the per-call cost of
`:free` models (they stay $0/call either way — the $10 is a threshold
OpenRouter checks, not something the free models spend down).

`llm.py` retries on `429` with the `Retry-After` header, and both
`run_eval.py` and the graph's parsing layers degrade gracefully (skip a
problem, or stop the eval early and write a partial, honestly-labeled
report) rather than crashing on quota exhaustion or a transient upstream
provider error.

## Real bugs found and fixed while building this

Documented here rather than glossed over, since debugging an adversarial
LLM pipeline surfaces failure modes that don't show up in a single happy-path
demo:

- **Silent error-swallowing in the retry loop.** `sandbox_node` originally
  handed the Prover a useless `"exited with code 1"` on every retry instead
  of the actual traceback, because `result.error` is never `None` on
  failure so the `or result.stderr` fallback never triggered. The Prover
  was retrying blind. Fixed to prioritize the real stderr diagnostic.
- **Reasoning-model leakage into code output.** Some free-tier models embed
  unterminated reasoning prose directly inside their returned code block,
  producing a `SyntaxError` that looks identical across every test run
  against it. `prover.py` now validates with `compile()` and retries, and
  falls back to returning the best attempt (rather than raising and
  crashing the whole graph) if every retry still fails to compile — the
  round-level retry loop gets a chance to recover instead of one node's
  exhausted internal retries taking down the run.
- **Multi-test contamination from the Skeptic.** Despite explicit
  instructions to write one self-contained test function per JSON array
  entry, the Skeptic sometimes bundles several `def test_...` functions
  into a single entry. Since the sandbox harness auto-discovers every
  `test_*` function in a combined run, this silently mixed pass/fail
  results and misattributed which test actually failed (caught directly
  from a live trace where a result labeled `test_empty_list` had a
  traceback pointing at a different function, `test_negative_numbers`,
  bundled in the same string). Fixed by splitting any multi-def blob into
  separate entries before they reach the sandbox.
- **Over-escaped JSON newlines.** Models occasionally emit `\\n` instead of
  `\n` inside JSON string values containing multi-line test code, producing
  literal backslash-n characters instead of real line breaks —
  syntactically invalid Python that fails identically on every test
  (a useful tell: if *all* tests fail identically, including trivial ones,
  suspect a pipeline defect before assuming a real bug). Detected and
  unescaped defensively in `skeptic.py`.
- **A live "hardcode-to-cheat" pattern.** On one run, the Skeptic generated
  a syntactically invalid test (misusing the walrus operator inside an
  `assert`) and a mathematically wrong assertion. Rather than recognizing
  these as bad tests, the Prover responded by literally special-casing the
  exact failing input (`if nums == [10**9, -10**9, 5] and target == 5:
  return [0, 2]`) instead of fixing the general algorithm — a real,
  reproducible bug pattern in how a weaker model responds to bad feedback,
  and exactly the kind of thing this project's bug-pattern-tracking premise
  is meant to surface. This specific failure mode (an invalid Skeptic test
  permanently blocking convergence) is why the charter assigns
  test-contract validation to the Arbiter — not yet implemented here, since
  V4 as scoped only asks the Arbiter for a verdict/confidence report, not
  test-validity filtering. Worth doing before trusting `bugs_caught` counts
  at face value.

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
