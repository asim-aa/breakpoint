# Breakpoint

[![Tests](https://github.com/asim-aa/breakpoint/actions/workflows/test.yml/badge.svg)](https://github.com/asim-aa/breakpoint/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

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
                 ┌─────────┐   ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐
  request  ───▶  │ Framer  │──▶│ Prover  │──▶│ Skeptic  │──▶│ Sandbox  │──▶│ Validator │
                 │ → spec  │   │ → code  │   │ → tests  │   │ real exec│   │ real bug? │
                 └─────────┘   └────┬────┘   └──────────┘   └──────────┘   └─────┬─────┘
                                    ▲                                            │
                                    │             round < max_rounds             │
                                    └────────── fix + retry ─────────────────────┤
                                                                                 │ no real
                                                                                 │ failures left,
                                                                                 │ or round budget hit
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

Built as a LangGraph `StateGraph` with one conditional edge — the retry loop is the thing that makes this a *system* rather than a linear pipeline. Every round re-runs **every** previously-generated test, not just the newest ones, so a fix can't silently reintroduce a bug that was already caught. The Validator exists because a failing test isn't automatically a real bug — see "Real bugs found and fixed" below for the failure mode that motivated it.

## Tech stack

| Layer | Tool | Role |
|---|---|---|
| Orchestration | **LangGraph** | Owns the Framer → Prover → Skeptic → Sandbox → (loop) → Arbiter graph and the round-budget state |
| Model access | **OpenRouter** | Prover and Skeptic pinned to *different* models — the adversarial pressure comes from genuinely different blind spots, not prompt framing |
| Execution | **Python `subprocess`** | The actual ground truth: runs Skeptic's tests against Prover's code with a hard timeout, stripped environment, isolated temp directory |
| Persistence | **SQLite** | Every spec, every round's attempt, every test result — queryable, not just printed to a terminal |
| Bug-pattern memory | **Hugging Face** (`sentence-transformers`, local) | Embeds each real bug's signature and clusters it against previously-seen patterns — runs entirely on-device, $0/call, no OpenRouter dependency |
| Dashboard | **Flask** (local only) | Read-only web view over the same SQLite data the CLI prints — no JS framework, no build step |
| Language | **Python 3.12** | The system itself. Stdlib-first (`sqlite3`, `subprocess`, `argparse` — dependencies added only where they earn their place) |
| Target languages | **Python, JavaScript** | What the Prover/Skeptic/sandbox actually generate and test — `--language javascript` runs the identical adversarial pipeline against Node instead of Python |

## Status

| Phase | What it adds | Status |
|---|---|---|
| V1 | Framer + Prover, real sandbox, no adversary yet | ✅ done |
| V2 | Skeptic (different model) + full LangGraph wiring | ✅ done |
| V3 | Retry loop, bounded by a round budget | ✅ done |
| V4a | Arbiter (verdict / confidence / coverage) + SQLite persistence + CLI | ✅ done |
| V4b | 6-problem eval vs. a non-adversarial self-check baseline | ✅ done — see [eval/report.md](eval/report.md) |
| — | Validator: test-contract validation (not in original V4 scope, added after finding the gap live) | ✅ done |
| — | Bug-pattern memory: clusters real bugs across runs (the original charter's "Memory" component) | ✅ done — `breakpoint patterns` |
| — | Per-model bug report: which bug patterns a specific Prover model reliably produces | ✅ done — `breakpoint model-report [model]` |
| — | Multi-model leaderboard: compares Prover/Skeptic pairs by convergence rate and bugs caught | ✅ done — `breakpoint leaderboard` |
| — | GitHub Action: verifies an existing PR's code (single file or auto-detected diff), not just generated code | ✅ done — run live 4x: found/fixed a real crash, then caught a real planted bug end-to-end, see below |
| — | Function-level diff extraction: narrows `diff-base` mode to just the changed function(s), not the whole file | ✅ done — `action/diff_utils.get_changed_functions` |
| — | Real OS-level sandbox isolation: network-none, dropped capabilities, read-only rootfs, non-root, cgroup limits | ✅ done — Docker when available, honest subprocess fallback otherwise, see Security notes |
| — | Multi-language support: the full adversarial pipeline (Framer/Prover/Skeptic/sandbox/Validator) targets JavaScript, not just Python | ✅ done — `--language javascript`, run live end-to-end including a real 6-problem eval, see below. GitHub Action's `--file` mode also detects `.js`; `diff-base` mode's function-level extraction stays Python-only |
| — | Web dashboard: browse runs/patterns/leaderboard visually instead of in a terminal | ✅ done — `breakpoint dashboard` |

## Eval results

The eval now runs with the Validator active (see below), on the full N=6 problem set — the 6th problem needed three attempts across two sessions to complete, due to a retired free-tier Skeptic model and a sustained Nvidia outage, both logged plainly rather than hidden (see the methodology note in the report). Earlier eval runs (before the Validator existed) showed the core thesis clearly: **a non-adversarial self-check said "correct" on 3 different implementations that real, executed tests proved were buggy.** Full breakdown, exact numbers, and known limitations for the current run — stated plainly, not smoothed over — are in [eval/report.md](eval/report.md).

**The more interesting result from this update isn't in the eval's table at all.** Re-running a spec manually hit the exact failure mode that motivated building the Validator: a Skeptic test with genuinely invalid Python syntax, which — before this fix — would have failed identically every round and permanently blocked convergence, since no amount of fixing the implementation can satisfy an assertion that isn't valid code. With the Validator active, that test was caught for $0 by a local syntax check and correctly excluded, and the run **converged in round 2** instead of retrying forever. A second run confirmed the Validator doesn't over-correct either, letting a real, hard bug through when the Prover genuinely couldn't fix it in time. See `nodes/validator.py` and [eval/report.md](eval/report.md) for the full account.

## Multi-language support

The whole adversarial pipeline — Framer infers a spec, Prover implements it, Skeptic writes tests aimed at breaking it, the sandbox runs them for real, the Validator judges any failure — targets JavaScript as well as Python, not through a parallel implementation but the same code path with a `language` parameter threaded through every node. `breakpoint run "<request>" --language javascript` runs it end to end against Node instead of Python; the sandbox picks the matching Docker image (`node:20-slim`, pinned by digest the same way as Python's) when Docker is available, or a local `node` binary otherwise.

**Run live, not just unit-tested against mocks.** Against a real spec ("reverse the words in a sentence, keeping the words themselves in the same order but each word's characters reversed"), the pipeline produced a correct `camelCase`-named JavaScript implementation, a live Skeptic model wrote 14 real adversarial tests using Node's `assert` module, all ran for real, one was correctly excluded by the Validator (a stray markdown-fence artifact in that one test's extracted source — the Validator's `node --check` caught it, exactly the invalid-test class it exists for), and the run converged. A second live run against the same spec — before a fix landed — surfaced a real gap and is documented below.

**Two real bugs found live while building this, not glossed over:**
- **A stray non-string element in the Skeptic's JSON array crashed extraction.** Despite the "respond with a JSON array of strings" instruction, a model can emit a non-string entry alongside real ones. `_extract_json_array` now skips a non-string entry instead of crashing on it — one malformed entry doesn't cost every real test in the same response.
- **A reasoning model can leak pure prose into the Prover's "code" field with zero actual code in it.** Python's `prove()` already guarded against this with a `compile()` check and retry; the JavaScript path initially didn't (documented as a known, deliberate gap at the time), and a live run demonstrated exactly why that gap mattered — round 2 silently treated a paragraph of reasoning as "the implementation," which correctly failed every test but wasted a full retry round discovering it. Closed by adding the same guard for JavaScript via `node --check` (already built for the Validator's own syntax check), giving both languages an equivalent safety net.

**A real 6-problem JavaScript eval, not just one example.** [eval/report_javascript.md](eval/report_javascript.md) runs the exact same 6 problems and difficulty labels as the Python eval ([eval/report.md](eval/report.md)) — same requests, run through the identical pipeline against a different target language, so the two are a direct comparison rather than separate problem sets. Result: **2/5 problems caught a real bug in the first attempt**, the same-model no-execution baseline missed both, and 4/5 converged — directionally consistent with the Python eval. `csv_row_parse` is honestly missing, not silently dropped: the Skeptic model exhausted its retries returning empty content even at an 8000-token budget (a real, reproducible limitation for this specific problem's complexity), and a same-day retry then hit OpenRouter's shared daily rate limit before completing — left as a stated gap rather than retried indefinitely against a finite quota. `run_eval.py --language javascript` reruns it.

**What's honestly still Python-only:** the GitHub Action's `--file` mode detects `.js` and passes the right language through, but `--diff-base` mode's function-level extraction (`ast`-based) has no JavaScript equivalent yet — that would need a JS-aware parser, not just a language flag, and isn't scoped into this pass.

## GitHub Action

Everything above generates *new* code from a spec. That's not what a PR verifier needs — a PR already has code; the point is judging whether *that* code is correct, without rewriting it. So this is a genuinely different mode, not just `cli.py run` wrapped in YAML:

```
verify_existing_code(code, context) -> Framer infers a spec FROM the code
                                     -> Skeptic writes adversarial tests
                                     -> Sandbox executes them for real
                                     -> Validator filters invalid tests
                                     -> verdict: bugs_found | no_bugs_found
```

No Prover, no retry loop — this reports findings on code a human already wrote; it doesn't rewrite anyone's PR. `action.yml` wraps this as a real composite GitHub Action:

```yaml
- uses: asim-aa/breakpoint@main
  with:
    file: src/merge.py
    context: ${{ github.event.pull_request.body }}
    openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}
    prover-model: nvidia/nemotron-3-super-120b-a12b:free
    skeptic-model: inclusionai/ling-3.0-flash-fin:free
```

**Two ways to point it at code**: `file` for one explicit path, or `diff-base` to auto-detect every changed `.py` file against a git ref (e.g. a PR's base branch) — closing what used to be a stated V1 gap ("not parsed out of a multi-file diff"). `action/diff_utils.py` handles the detection with a plain `git diff --name-only`; verified against a real disposable git repo *and* against this project's own actual commit history (`get_changed_python_files("HEAD~1")` correctly returned the exact 8 `.py` files changed in a real prior commit here, no more no less). Using `diff-base` in a workflow needs `fetch-depth: 0` on the checkout step — GitHub's default shallow clone doesn't have the history to diff against.

**Function-level diff extraction.** `diff-base` mode doesn't stop at "which files changed" — `get_changed_functions` reads the actual unified-diff hunks (`git diff -U0`, so every reported line range is a real addition, never surrounding context) and, via `ast`, narrows each changed file down to just the top-level function(s) whose body the diff actually overlaps. Each function is verified on its own — a smaller prompt per target, and a report that names `file.py::function_name`, not just the file. Deliberately top-level functions only: a method or nested function extracted alone can't run standalone in the sandbox (wrong indentation, or a bare `self` with no class), so a change to one of those — or to module-level code outside any function — falls back to verifying the whole file, the same as before this existed. This is a real narrowing, not a heuristic: proven against a real disposable git repo (`tests/test_diff_utils.py`) — the one modified function among several is the only one returned, an untouched sibling function's source never leaks into another function's extract, decorators are included, and a module-level constant change correctly returns nothing (triggering the whole-file fallback) rather than a false match.

**Run live to confirm it end to end**, not just against unit tests: two real commits to a scratch two-function file — the second touching only one function (`first_index_at_least`), introducing a genuine off-by-one (`<=` instead of `<`, which skips past the correct answer whenever the target has duplicates in the array). Triggering `diff-base` mode against that diff produced a report labeled exactly `demo/scratch_two_functions.py::first_index_at_least` — the untouched sibling function never appeared — and the live Skeptic caught **6 real failures**, every one a duplicate/boundary case that specific bug actually breaks. (This also live-caught a second, unrelated real bug: `verify-example.yml`'s `file` input defaulted to a fixed path, and GitHub's `workflow_dispatch` silently falls back to an input's default when an explicitly-empty override is submitted — so diff-base mode was practically unreachable from a manual trigger despite the workflow's own description promising otherwise. Fixed by defaulting `file` to `""`, matching what the description already said.)

Verify-only either way — a real bug found is a finding for a human, never an auto-applied fix. `.github/workflows/verify-example.yml` demonstrates it against this repo's own code, but is deliberately `workflow_dispatch`-only (manual trigger), not wired into every push/PR — this repo's own CI (`test.yml`) already runs on every push, and OpenRouter's shared daily quota is tight enough (see below) that an auto-triggering example would silently compete with it.

**This has now run live end to end, and caught a real bug.** The first live trigger — against this repo's own `nodes/validator.py` — immediately crashed the whole Action: the Skeptic model burned its entire token budget on internal reasoning before emitting the test array, and `find_bugs()` had no retry path for that failure mode. Fixed (`find_bugs` now requests a wider token budget and retries on that specific error; `action/verify_pr.py`'s per-file loop now catches provider errors per file instead of crashing the whole run — see commit history). Re-triggered twice more: both hit a second, unrelated live issue — a sustained 502 "Service temporarily overloaded" from Nvidia's free-tier Prover endpoint (the same outage that delayed the eval above) — and both times the fix did its job, degrading to a clean `⚠️ Skipped — provider error` report instead of crashing again.

Once Nvidia's endpoint cooperated, it got a full clean run: triggered against a real, deliberately-planted bug in a scratch file (a classic binary-search off-by-one, `while lo < hi` instead of `lo <= hi`, missing the case where the target sits exactly at the final `lo == hi` position) with no hint left for the model. The live Skeptic inferred the spec, wrote adversarial tests blind, ran them for real, and **caught 4 real failures** — covering exactly the boundary cases that bug actually breaks (single-element arrays, and targets at the first/last index). The diff-detection logic (`action/diff_utils.py`) was already proven separately against real git repos and this project's own commit history.

## Web dashboard

```bash
./venv/bin/python cli.py dashboard   # http://localhost:5050
```

A small local Flask app, read-only, over the exact same SQLite data the CLI already prints — browse runs, drill into any one's full round-by-round trace (code + every test's pass/fail), bug patterns, the model leaderboard, and a per-model bug-pattern breakdown, without a terminal full of table output. It doesn't trigger new runs; `breakpoint run` stays how you actually use the system, this is just a better way to look at what it's already recorded.

Verified against this project's own real historical data (not synthetic fixtures): pointing it at the actual `breakpoint.db` from earlier development correctly rendered a real 2-round trace including the exact "hardcode-to-cheat" bug documented above (`if s == "abXba": return False`), and correctly showed the model-leaderboard and bug-pattern pages as legacy-empty for runs that predate those features — the same honest degradation the CLI itself has, not a dashboard-specific special case.

## Structure

```
graph.py            LangGraph wiring: framer → prover → skeptic → sandbox → (loop) → arbiter
state.py            Shared LangGraph state schema (BreakpointState)
sandbox.py          Isolated execution for Python or JavaScript — the ground truth for every verdict. Docker (real OS-level isolation) when available, a bare subprocess (or local node) otherwise
llm.py              Thin OpenRouter chat-completions client (429 backoff, token-budget handling)
storage.py          SQLite persistence: specs / attempts / tests / bug_patterns tables
memory.py           Bug-pattern clustering: embeds each real bug, matches or creates a cluster
cli.py              `breakpoint run "<request>"`, `history`, `patterns`, `model-report`, `leaderboard`, `dashboard`
dashboard.py        Local read-only Flask app over the same SQLite data the CLI prints
templates/          Jinja2 templates for the dashboard (no JS framework, no build step)
manual_run.py       Ad-hoc single-request runner with a full round-by-round trace printed
nodes/
  framer.py         Plain-English request → formal spec (JSON: inputs/output/constraints/examples)
  prover.py         Spec (+ optional failure trace) → implementation
  skeptic.py        Spec + code → adversarial pytest-style tests, on a different model than the Prover
  validator.py      Judges whether a failing test is a real bug or an invalid test (bad syntax, or an assertion that's wrong on its own terms) — a free local syntax check first, an LLM call only if that passes
  arbiter.py        Final verdict + confidence heuristic (formula documented in-code, not pretended to be rigorous)
eval/
  problems.json     6 hand-written problems: easy, boundary-heavy, and deliberately spec-ambiguous
  problems_javascript.json  The exact same 6 problems, for a direct Python vs. JavaScript comparison
  run_eval.py       Runs the full graph + a baseline self-check on all 6, writes eval/report.md (--language javascript writes eval/report_javascript.md)
verify.py           Verifies EXISTING code (no Prover, no retry loop) — the GitHub Action's core
action.yml          Composite GitHub Action wrapping verify.py for CI use
action/
  verify_pr.py      CLI entry point: --file or --diff-base, runs verify.py per file, formats + optionally posts a PR comment
  diff_utils.py     Auto-detects changed .py files from a real git diff, and narrows each to its changed top-level function(s) via ast — no API calls needed
tests/              186 tests covering the sandbox and every pure-logic module — most $0/no-network, a few requiring one-time model download or Docker/node (gracefully skipped without them, run for real in CI)
```

## Quickstart

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in OPENROUTER_API_KEY, PROVER_MODEL, SKEPTIC_MODEL
```

`PROVER_MODEL` and `SKEPTIC_MODEL` **must differ** — `skeptic.py` asserts this at runtime. Any two OpenRouter chat models work; free-tier `:free` slugs keep this at $0/call (check `https://openrouter.ai/api/v1/models` for the current roster — free slugs get retired and replaced over time).

```bash
# Full test suite (186 tests) — no OpenRouter API key needed
./venv/bin/pytest

# One-off run with a full round-by-round trace
./venv/bin/python manual_run.py "merge overlapping intervals"

# CLI: run + persist to SQLite
./venv/bin/python cli.py run "parse a CSV row, respecting quoted commas" --max-rounds 3
./venv/bin/python cli.py history

# Same pipeline, targeting JavaScript instead of Python
./venv/bin/python cli.py run "reverse each word's characters, keep word order" --language javascript

# See recurring bug patterns across every run so far
./venv/bin/python cli.py patterns

# Which bug patterns a specific model reliably produces — no schema
# change needed, this just joins bug_patterns against specs.prover_model
./venv/bin/python cli.py model-report                       # every model
./venv/bin/python cli.py model-report "nvidia/nemotron-3-super-120b-a12b:free"

# Compare Prover/Skeptic model pairs (accumulates as you change .env
# across sessions — see note below)
./venv/bin/python cli.py leaderboard

# Browse all of the above visually instead of in a terminal
./venv/bin/python cli.py dashboard

# The 6-problem eval
./venv/bin/python eval/run_eval.py
```

The leaderboard has no dedicated "run every model pair" script on purpose: `storage.save_run` already records whichever `PROVER_MODEL`/`SKEPTIC_MODEL` pair produced each run, so switching `.env` between sessions and using `breakpoint run` as normal is enough to build it up over time — no extra API usage beyond what you'd spend anyway, which matters given how tight OpenRouter's free-tier daily cap is (see below).

### A note on OpenRouter's free tier

The free-tier daily cap is **50 requests/day, account-wide** — every `:free` model shares one bucket, it isn't per-model. A single retry-loop run can cost 5–10 requests; the full 6-problem eval can need 30–40+. A one-time $10 balance raises the cap to 1000/day without changing the per-call cost of `:free` models (the $10 is a threshold OpenRouter checks, not something free models spend down). `llm.py` retries on `429` using the `Retry-After` header, and the eval runner degrades gracefully — skipping a problem or stopping early with an honestly-labeled partial report — rather than crashing on quota exhaustion.

## Real bugs found and fixed while building this

Documented here rather than glossed over — debugging an adversarial LLM pipeline surfaces failure modes a single happy-path demo never shows, and the diagnosis matters more than the fix:

- **Silent error-swallowing in the retry loop.** `sandbox_node` originally handed the Prover a useless `"exited with code 1"` on every retry instead of the actual traceback, because `result.error` is never `None` on failure so the `or result.stderr` fallback never triggered. The Prover was retrying blind, with no signal to actually fix anything. Fixed to prioritize the real stderr diagnostic.
- **Reasoning-model leakage into code output.** Some free-tier models embed unterminated reasoning prose directly inside their returned code block, producing a `SyntaxError` that looks identical across every test run against it — easy to misread as "the code is universally broken" rather than "the extraction let garbage through." `prover.py` now validates with `compile()` and retries, and falls back to returning the best attempt (rather than raising and crashing the whole graph) if every retry still fails to compile — the round-level retry loop gets a chance to recover instead of one node's exhausted internal retries taking the whole run down.
- **Multi-test contamination from the Skeptic.** Despite explicit instructions to write one self-contained test function per JSON array entry, the Skeptic sometimes bundles several `def test_...` functions into a single entry. Since the sandbox harness auto-discovers every `test_*` function in a combined run, this silently mixed pass/fail results and misattributed which test actually failed — caught directly from a live trace where a result labeled `test_empty_list` had a traceback pointing at a completely different function, `test_negative_numbers`, bundled into the same string. Fixed by splitting any multi-def blob into separate entries before they ever reach the sandbox.
- **Over-escaped JSON newlines.** Models occasionally emit `\\n` instead of `\n` inside JSON string values containing multi-line test code, producing literal backslash-n characters instead of real line breaks — syntactically invalid Python that fails identically on every test. (A useful tell, learned the hard way: if *all* tests fail identically, including trivial ones like an empty-input check, suspect a pipeline defect before assuming a real bug.) Detected and unescaped defensively in `skeptic.py`.
- **A crash on the GitHub Action's first-ever live run.** The verify-only pipeline (`verify_existing_code`) was built and unit-tested against mocks, but had never made a real OpenRouter call end to end. The first live trigger — against this repo's own `nodes/validator.py` — crashed immediately: the Skeptic model burned its entire 4000-token budget on internal reasoning before emitting the JSON test array, `llm.py` correctly raised a `RuntimeError` for the resulting empty content, but `find_bugs()`'s retry loop only caught JSON-decode errors, not that. Worse, `action/verify_pr.py`'s per-file loop had no error handling at all, so one bad response took the whole Action down with an unhandled traceback — the exact "unproven wiring around proven parts" gap this README used to flag honestly. Fixed by widening `find_bugs`'s token budget, retrying on that specific `RuntimeError`, and giving the per-file loop the same provider-error resilience `eval/run_eval.py` already had. Re-triggered live afterward and confirmed the fix: two subsequent runs both hit a second, unrelated real issue (Nvidia's free-tier Prover having a sustained 502 outage) and both degraded gracefully — a clean skip and a green run — instead of crashing.
- **A live "hardcode-to-cheat" pattern, and the fix it motivated.** On one run, the Skeptic generated a syntactically invalid test (misusing the walrus operator inside an `assert`) alongside a mathematically wrong assertion. Rather than recognizing these as bad tests, the Prover responded by literally special-casing the exact failing input (`if nums == [10**9, -10**9, 5] and target == 5: return [0, 2]`) instead of fixing the general algorithm. Worse, the invalid test itself failed identically every round with no way for the Prover to ever satisfy it, permanently blocking convergence. Fixed with a dedicated **Validator** node (`nodes/validator.py`), inserted between Sandbox and the retry decision: a free, local `compile()` check first catches syntactically broken tests (like the walrus-operator misuse) for $0, and anything that compiles but might still be semantically wrong (e.g. an assertion that contradicts the spec) gets one LLM call judging test validity against the spec's actual contract — completely separate from judging the implementation. A test judged invalid doesn't count toward `bugs_caught`, doesn't get fed back to the Prover as `prior_failure`, and doesn't block the round from converging. Verified live: a real run against a `two_sum` spec hit a Skeptic test with genuinely invalid syntax (`test_float_like_int_input`), the Validator caught it for free, and the run converged in round 2 instead of retrying forever against an unfixable assertion.

## Security notes

`sandbox.run_test` runs generated code in a separate process, in a fresh temp directory, with a hard wall-clock timeout. It has two backends, chosen automatically:

**Docker, when available** (`sandbox._run_in_docker`) — a real OS-level boundary, not just a stripped environment: `--network none` (a genuinely absent network namespace, not just missing env vars), `--cap-drop ALL` + `--security-opt no-new-privileges`, a `--read-only` root filesystem with the sandboxed code mounted read-only, a `--pids-limit` against fork bombs, and cgroup-enforced `--memory`/`--cpus` ceilings — the last of these also fixes the old Linux-only limitation below, since cgroup accounting doesn't depend on the host platform or the interpreter's own allocator the way `RLIMIT_AS` did. Runs as the host's own uid:gid (not root, and it avoids bind-mount permission mismatches a fixed low-privilege uid would hit) inside an ephemeral `python:3.12-slim` container, killed and removed on timeout or exit either way.

**A bare subprocess, as a fallback** — when Docker isn't installed or its daemon isn't reachable, or if a specific Docker invocation fails (e.g. the daemon dies mid-run). Same as this project always used: a fresh temp dir, a stripped environment (no inherited API keys), a wall-clock timeout, and `RLIMIT_CPU`/`RLIMIT_AS` via the `resource` module on Linux only. Every `SandboxResult` carries an `isolation` field (`"docker"` or `"subprocess"`) so which boundary actually ran is never silently hidden.

**Verified, not just configured:** `tests/test_sandbox.py` has three Docker-specific tests — network access, a filesystem write outside the sandbox, and confirming the process runs as a non-root uid — each `assert`ing the operation actually fails from *inside* the sandboxed code. They're skipped on machines without Docker (this project's own dev machine included) but run for real in CI, since GitHub's hosted runners have a live Docker daemon by default — this is proven live in this repo's own CI, not just asserted in prose.

**Known gaps, not glossed over — even with Docker:**

- **This isn't a claim of being escape-proof.** Docker's default seccomp profile blocks a long list of dangerous syscalls, but it's not gVisor or Firecracker — a real container-escape kernel exploit is a different threat class this doesn't defend against. Treat this as a hardened boundary for adversarially-generated-but-not-malicious LLM output, not a boundary safe against a determined attacker with kernel 0-days.
- **The subprocess fallback has all the same gaps it always did**: no seccomp/container/VM boundary, `os.system` and arbitrary syscalls work, no network blocking beyond a stripped environment, and CPU/memory limits only enforced on Linux. This path exists specifically for environments without Docker — running there is a real, honestly-labeled downgrade (`isolation="subprocess"`), not a silent one.
- **The image is pinned by digest, not just tag** (`sandbox.DOCKER_IMAGE`, fetched live from the registry, not guessed) — a compromised or republished `3.12-slim` tag can't silently change what runs inside the sandbox. The tradeoff: a pinned digest doesn't self-update, so re-pinning periodically to pick up upstream security patches is a manual step, not automatic.

Docker Desktop or a Linux Docker Engine install gets you the hardened path locally; nothing else to configure — `sandbox.py` detects and uses it automatically.

## License

MIT — see [LICENSE](LICENSE).
