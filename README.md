# Breakpoint

**An adversarial code-generation system where correctness is decided by real execution, not by a second LLM's opinion.**

One agent (the **Prover**) implements a spec. A second agent (the **Skeptic**) — running on a *different* model, on purpose — writes tests specifically aimed at breaking that implementation. Every test actually runs, in an isolated subprocess, and the pass/fail result is a fact, not a vibe. Failures feed back to the Prover with the real traceback; the loop retries within a bounded round budget; an Arbiter produces a final verdict, a confidence score, and a persisted record of every attempt.

```
"Does this code look correct?"          →  a coin flip, or one model grading another model's homework
"Did this code survive real execution?" →  a fact
```

That distinction is the entire point of this project.

![Breakpoint demo: the Prover writes an implementation, the Skeptic's tests run for real, one fails on a genuine edge case, and cli.py history shows persisted results across runs](demo/breakpoint-demo.gif)

*(Replays real, previously-captured output from actual runs during development — see [demo/play.sh](demo/play.sh) for the source. `test_none_input` failing isn't a model's opinion — it's an actual, real crash that happened when the sandbox ran that exact input against that exact code.)*

## Why this exists

Most "AI writes code" demos verify correctness by asking a language model whether the code *looks* right. That's judgment layered on judgment — no more reliable than asking the same model to grade its own test. Breakpoint replaces that with something unambiguous: generated tests are executed for real, against generated code, in a sandboxed subprocess with no network access and a hard timeout. Nothing in this system's verdicts is ever "the model said so."

## Architecture

```
                 ┌─────────┐      ┌─────────┐      ┌──────────┐      ┌──────────┐
  request  ───▶  │ Framer  │ ───▶ │ Prover  │ ───▶ │ Skeptic  │ ───▶ │ Sandbox  │
                 │ → spec  │      │ → code  │      │ → tests  │      │ real exec│
                 └─────────┘      └─────────┘      └──────────┘      └────┬─────┘
                                        ▲                                 │
                                        │        round < max_rounds       │
                                        └─────── fix + retry ─────────────┤
                                                                          │ all pass, or
                                                                          │ round budget hit
                                                                          ▼
                                                                    ┌──────────┐
                                                                    │ Arbiter  │
                                                                    │ verdict +│
                                                                    │confidence│
                                                                    └────┬─────┘
                                                                         ▼
                                                                  SQLite (specs /
                                                                  attempts / tests)
```

Built as a LangGraph `StateGraph` with one conditional edge — the retry loop is the thing that makes this a *system* rather than a linear pipeline. Every round re-runs **every** previously-generated test, not just the newest ones, so a fix can't silently reintroduce a bug that was already caught.

## Tech stack

| Layer | Tool | Role |
|---|---|---|
| Orchestration | **LangGraph** | Owns the Framer → Prover → Skeptic → Sandbox → (loop) → Arbiter graph and the round-budget state |
| Model access | **OpenRouter** | Prover and Skeptic pinned to *different* models — the adversarial pressure comes from genuinely different blind spots, not prompt framing |
| Execution | **Python `subprocess`** | The actual ground truth: runs Skeptic's tests against Prover's code with a hard timeout, stripped environment, isolated temp directory |
| Persistence | **SQLite** | Every spec, every round's attempt, every test result — queryable, not just printed to a terminal |
| Language | **Python 3.12** | Whole project, stdlib-first (`sqlite3`, `subprocess`, `argparse` — no unnecessary dependencies) |

## Status

| Phase | What it adds | Status |
|---|---|---|
| V1 | Framer + Prover, real sandbox, no adversary yet | ✅ done |
| V2 | Skeptic (different model) + full LangGraph wiring | ✅ done |
| V3 | Retry loop, bounded by a round budget | ✅ done |
| V4a | Arbiter (verdict / confidence / coverage) + SQLite persistence + CLI | ✅ done |
| V4b | 6-problem eval vs. a non-adversarial self-check baseline | ✅ done — see [eval/report.md](eval/report.md) |

## Eval results

On the full 6-problem set: **the Skeptic caught a real bug in the Prover's first attempt on 5 of 6 problems.** On 3 of those 5, a non-adversarial self-check — the same model asked "does this look correct?" with no execution — said **"correct"** on the exact implementation that a real, executed test proved had a bug. That gap between "looks right" and "survives execution" is the entire thesis of this project, reproduced across half the problem set. This is a small sample from one model pair — see [eval/report.md](eval/report.md) for the full breakdown, methodology, and known limitations, stated plainly rather than smoothed over.

## Structure

```
graph.py            LangGraph wiring: framer → prover → skeptic → sandbox → (loop) → arbiter
state.py            Shared LangGraph state schema (BreakpointState)
sandbox.py          Isolated subprocess execution — the ground truth for every verdict
llm.py              Thin OpenRouter chat-completions client (429 backoff, token-budget handling)
storage.py          SQLite persistence: specs / attempts / tests tables
cli.py              `breakpoint run "<request>"` and `breakpoint history`
manual_run.py       Ad-hoc single-request runner with a full round-by-round trace printed
nodes/
  framer.py         Plain-English request → formal spec (JSON: inputs/output/constraints/examples)
  prover.py         Spec (+ optional failure trace) → implementation
  skeptic.py        Spec + code → adversarial pytest-style tests, on a different model than the Prover
  arbiter.py        Final verdict + confidence heuristic (formula documented in-code, not pretended to be rigorous)
eval/
  problems.json     6 hand-written problems: easy, boundary-heavy, and deliberately spec-ambiguous
  run_eval.py       Runs the full graph + a baseline self-check on all 6, writes eval/report.md
tests/
  test_sandbox.py   The only tests that don't touch an LLM — sandbox correctness, fully self-contained
```

## Quickstart

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in OPENROUTER_API_KEY, PROVER_MODEL, SKEPTIC_MODEL
```

`PROVER_MODEL` and `SKEPTIC_MODEL` **must differ** — `skeptic.py` asserts this at runtime. Any two OpenRouter chat models work; free-tier `:free` slugs keep this at $0/call (check `https://openrouter.ai/api/v1/models` for the current roster — free slugs get retired and replaced over time).

```bash
# Sandbox's own test suite — no API key or LLM calls needed
./venv/bin/pytest

# One-off run with a full round-by-round trace
./venv/bin/python manual_run.py "merge overlapping intervals"

# CLI: run + persist to SQLite
./venv/bin/python cli.py run "parse a CSV row, respecting quoted commas" --max-rounds 3
./venv/bin/python cli.py history

# The 6-problem eval
./venv/bin/python eval/run_eval.py
```

### A note on OpenRouter's free tier

The free-tier daily cap is **50 requests/day, account-wide** — every `:free` model shares one bucket, it isn't per-model. A single retry-loop run can cost 5–10 requests; the full 6-problem eval can need 30–40+. A one-time $10 balance raises the cap to 1000/day without changing the per-call cost of `:free` models (the $10 is a threshold OpenRouter checks, not something free models spend down). `llm.py` retries on `429` using the `Retry-After` header, and the eval runner degrades gracefully — skipping a problem or stopping early with an honestly-labeled partial report — rather than crashing on quota exhaustion.

## Real bugs found and fixed while building this

Documented here rather than glossed over — debugging an adversarial LLM pipeline surfaces failure modes a single happy-path demo never shows, and the diagnosis matters more than the fix:

- **Silent error-swallowing in the retry loop.** `sandbox_node` originally handed the Prover a useless `"exited with code 1"` on every retry instead of the actual traceback, because `result.error` is never `None` on failure so the `or result.stderr` fallback never triggered. The Prover was retrying blind, with no signal to actually fix anything. Fixed to prioritize the real stderr diagnostic.
- **Reasoning-model leakage into code output.** Some free-tier models embed unterminated reasoning prose directly inside their returned code block, producing a `SyntaxError` that looks identical across every test run against it — easy to misread as "the code is universally broken" rather than "the extraction let garbage through." `prover.py` now validates with `compile()` and retries, and falls back to returning the best attempt (rather than raising and crashing the whole graph) if every retry still fails to compile — the round-level retry loop gets a chance to recover instead of one node's exhausted internal retries taking the whole run down.
- **Multi-test contamination from the Skeptic.** Despite explicit instructions to write one self-contained test function per JSON array entry, the Skeptic sometimes bundles several `def test_...` functions into a single entry. Since the sandbox harness auto-discovers every `test_*` function in a combined run, this silently mixed pass/fail results and misattributed which test actually failed — caught directly from a live trace where a result labeled `test_empty_list` had a traceback pointing at a completely different function, `test_negative_numbers`, bundled into the same string. Fixed by splitting any multi-def blob into separate entries before they ever reach the sandbox.
- **Over-escaped JSON newlines.** Models occasionally emit `\\n` instead of `\n` inside JSON string values containing multi-line test code, producing literal backslash-n characters instead of real line breaks — syntactically invalid Python that fails identically on every test. (A useful tell, learned the hard way: if *all* tests fail identically, including trivial ones like an empty-input check, suspect a pipeline defect before assuming a real bug.) Detected and unescaped defensively in `skeptic.py`.
- **A live "hardcode-to-cheat" pattern.** On one run, the Skeptic generated a syntactically invalid test (misusing the walrus operator inside an `assert`) alongside a mathematically wrong assertion. Rather than recognizing these as bad tests, the Prover responded by literally special-casing the exact failing input (`if nums == [10**9, -10**9, 5] and target == 5: return [0, 2]`) instead of fixing the general algorithm — a real, reproducible failure pattern in how a weaker model responds to bad feedback, and exactly the kind of thing this project's bug-pattern-tracking premise exists to surface. This also exposed a real scope gap: an invalid Skeptic test can permanently block convergence, which is why a production version of this system would need the Arbiter to validate a failing test against the spec's actual contract before counting it as a real bug — not implemented here, since V4 as scoped only asks the Arbiter for a verdict/confidence report. Worth doing before trusting `bugs_caught` counts at face value.

## Security notes

`sandbox.run_test` runs generated code in a separate subprocess (never `exec()` in-process), in a fresh temp directory, with a stripped-down environment (no inherited API keys) and a hard wall-clock timeout.

**Known gaps, not glossed over:**

- **No OS-level sandboxing.** The subprocess can still make syscalls like `os.system`, read/write outside the temp dir, or open network sockets — confirmed manually: sandboxed code calling `os.system("echo pwned")` executes successfully. There is no seccomp/container/VM boundary here. Treat this as a code-review harness for trusted-ish LLM output on a single dev machine, not a boundary safe for genuinely untrusted or hostile code.
- **CPU/memory resource limits (`RLIMIT_CPU`, `RLIMIT_AS` via the `resource` module) are only applied on Linux.** On macOS and other non-Linux platforms, the only enforced ceiling is the wall-clock `timeout_seconds` passed to `subprocess.run` — a process that allocates a lot of memory but returns before the timeout won't be stopped.
- **No network blocking is implemented at this layer.** The stripped environment removes API keys, but doesn't prevent a sandboxed process from opening a socket if the host machine allows it.

Before running this against genuinely untrusted code, add a real container/VM boundary — gVisor, Firecracker, or Docker with a locked-down seccomp profile and `--network none`.

## License

MIT — see [LICENSE](LICENSE).
