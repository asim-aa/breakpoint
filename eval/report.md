# Breakpoint eval report

**Sample size: N=3 of 6 planned**. This is a small eval — these numbers indicate a direction, not a statistically reliable rate. Do not extrapolate beyond this specific problem set and this specific model pair.

> **Note:** this run stopped early after hitting OpenRouter's free-tier daily rate limit. The numbers below reflect only the 3 problems that completed before that happened.

**Methodology note:** Breakpoint mode ran with `MAX_ROUNDS=2` (not the system default of 5) to fit within the free-tier daily request cap. The baseline reuses Breakpoint's own round-1 Prover code and asks the same model to self-assess it with no execution — this isolates the effect of adversarial *testing*, not a different implementation.

## Results

| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds |
|---|---|---|---|---|---|
| merge_intervals | easy | no | correct | converged | 1 |
| first_last_index | boundary-heavy | yes | correct | unresolved | 2 |
| two_sum_indices | spec-ambiguous | yes | unknown | unresolved | 2 |

## Summary

- Breakpoint caught a real bug in the first attempt on **2/3** problems.
- Of those, the baseline self-check (same model, no execution) said "CORRECT" on **1/2** — i.e. a same-model opinion-only check missed a bug that real execution caught.
- 1/3 problems converged within the 2-round budget; 2/3 stayed unresolved.
- Average rounds taken: 1.7 (budget capped at 2).

## Why this supersedes the previous (N=5) run

An earlier run covered more problems (5 of 6) but its baseline self-check
call capped `max_tokens=20` — too small for a reasoning model that spends
tokens on internal reasoning before ever writing a verdict word, so 4 of 5
baseline checks came back "unknown" instead of a real opinion. That bug is
fixed here (`max_tokens=300`), which is also *why* this run has fewer
problems: a larger token budget per call means the same 50-request daily
cap gets exhausted sooner.

The trade was worth it. `first_last_index` here is the cleanest evidence
this project can produce: the baseline (same model, asked "does this look
correct?", no execution) said **"correct"** on an implementation that
**did** have a real bug — one that Breakpoint's Skeptic caught because its
test actually ran and actually failed. That's the entire thesis of this
project, demonstrated with one real, methodologically clean example rather
than a broad-but-noisy one.
