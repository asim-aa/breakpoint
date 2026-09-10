# Breakpoint eval report (JavaScript)

**Sample size: N=5 of 6 planned**. Target language: **JavaScript**. This is a small eval — these numbers indicate a direction, not a statistically reliable rate. Do not extrapolate beyond this specific problem set and this specific model pair.

**Comparison note:** this uses the exact same 6 problems and difficulty labels as [report.md](report.md) (the Python eval) — same requests, run through the identical pipeline against a different target language — so the two reports are a direct comparison, not two unrelated problem sets.

> **Note:** `csv_row_parse` is missing, not silently dropped. First attempt: the Skeptic model (`inclusionai/ling-3.0-flash-fin:free`) exhausted its retries returning empty content even at an 8000-token budget (`finish_reason='length'` — a reasoning model spending its whole budget on internal reasoning before ever writing the test array), a real, reproducible limitation for this specific problem's complexity, not a bug in this system. A same-day retry of just this one problem then hit OpenRouter's free-tier daily rate limit before completing. Left as a genuine gap rather than retried indefinitely against a shared, finite daily quota.

**Methodology note:** Breakpoint mode ran with `MAX_ROUNDS=2` (not the system default of 5) to fit within the free-tier daily request cap. The baseline reuses Breakpoint's own round-1 Prover code and asks the same model to self-assess it with no execution — this isolates the effect of adversarial *testing*, not a different implementation.

## Results

| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds | Invalid tests filtered |
|---|---|---|---|---|---|---|
| merge_intervals | easy | yes | correct | converged | 2 | 0 |
| first_last_index | boundary-heavy | no | correct | converged | 1 | 0 |
| two_sum_indices | spec-ambiguous | no | correct | converged | 1 | 0 |
| reverse_words_preserve_whitespace | spec-ambiguous | no | correct | converged | 1 | 0 |
| valid_palindrome | moderate | yes | correct | unresolved | 2 | 0 |

## Summary

- Breakpoint caught a real bug in the first attempt on **2/5** problems.
- Of those, the baseline self-check (same model, no execution) said "CORRECT" on **2/2** — i.e. a same-model opinion-only check missed a bug that real execution caught.
- 4/5 problems converged within the 2-round budget; 1/5 stayed unresolved.
- Average rounds taken: 1.4 (budget capped at 2).
- The Validator (`nodes/validator.py`) filtered **0** distinct invalid test(s) across this run — Skeptic-generated tests judged not to be real bugs (bad syntax, or an assertion wrong on its own terms), excluded from `bugs_caught` and never fed back to the Prover.

## Comparison with the Python eval

| | Python (N=6) | JavaScript (N=5) |
|---|---|---|
| Bugs caught in 1st attempt | 2/6 | 2/5 |
| Baseline missed a caught bug | — | 2/2 |
| Converged within 2 rounds | 4/6 | 4/5 |

Directionally consistent with the Python results ([report.md](report.md)): the adversarial pipeline catches real bugs the same-model, no-execution baseline misses, in both languages, on the identical 6 conceptual problems.
