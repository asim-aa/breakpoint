# Breakpoint eval report

**Sample size: N=5 of 6 planned**. This is a small eval — these numbers indicate a direction, not a statistically reliable rate. Do not extrapolate beyond this specific problem set and this specific model pair.

**Methodology note:** Breakpoint mode ran with `MAX_ROUNDS=2` (not the system default of 5) to fit within the free-tier daily request cap. The baseline reuses Breakpoint's own round-1 Prover code and asks the same model to self-assess it with no execution — this isolates the effect of adversarial *testing*, not a different implementation.

## Results

| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds |
|---|---|---|---|---|---|
| merge_intervals | easy | yes | unknown | unresolved | 2 |
| two_sum_indices | spec-ambiguous | no | unknown | converged | 1 |
| csv_row_parse | boundary-heavy | yes | incorrect | unresolved | 2 |
| reverse_words_preserve_whitespace | spec-ambiguous | yes | unknown | unresolved | 2 |
| valid_palindrome | moderate | yes | unknown | unresolved | 2 |

## Summary

- Breakpoint caught a real bug in the first attempt on **4/5** problems.
- Of those, the baseline self-check (same model, no execution) said "CORRECT" on **0/4** — i.e. the baseline never falsely vouched for a buggy implementation in this run.
- 1/5 problems converged within the 2-round budget; 4/5 stayed unresolved.
- Average rounds taken: 1.8 (budget capped at 2).

**Known limitation in this run:** 4 of 5 baseline self-checks came back
"unknown" rather than a clear CORRECT/INCORRECT. Root cause: the baseline
call originally capped `max_tokens=20`, too small for `PROVER_MODEL`
(`nvidia/nemotron-3-super-120b-a12b:free`), a reasoning model that spends
tokens on its internal `reasoning` field before ever writing a verdict word
— the response was getting truncated before the answer appeared. Fixed in
`run_eval.py` (raised to `max_tokens=300`) for future runs, but this
specific report's baseline comparison is weaker evidence than the
Breakpoint-mode results, which come from real sandbox execution and are
unaffected by this. The one baseline verdict that *did* come through
clearly (`csv_row_parse`: "incorrect") is a real, correctly-parsed result.

**One problem (`first_last_index`) was skipped**, not failed: a transient
upstream provider error (Nvidia returned a 502 "temporarily overloaded"
mid-run) was caught and logged, and the eval continued to the next problem
rather than crashing — exactly the resilience behavior this run was meant
to validate.
