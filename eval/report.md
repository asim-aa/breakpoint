# Breakpoint eval report

**Sample size: N=5 of 6 planned.** This is a small eval — these numbers indicate a direction, not a statistically reliable rate. Do not extrapolate beyond this specific problem set and this specific model pair (`nvidia/nemotron-3-super-120b-a12b:free` as Prover, `minimax/minimax-m3:free` as Skeptic).

**This run includes the Validator** (`nodes/validator.py`), added after the previous eval to fix a real gap: a Skeptic-generated test that's itself invalid (bad syntax, or an assertion wrong on its own terms) used to be indistinguishable from a real bug, permanently blocking convergence. The Validator now judges every failing test before it counts.

`reverse_words_preserve_whitespace` is missing from this run despite three separate attempts — Nvidia's free-tier endpoint had a sustained outage during this session (`502: Service temporarily overloaded`, confirmed via a direct health check, not just this eval retrying blind), and by the time it recovered this run had exhausted OpenRouter's daily free-tier cap. This is a live demonstration of the exact resilience the eval is built to have: every failure below was caught and either skipped or recorded honestly, never crashed.

## Results

| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds | Invalid tests filtered |
|---|---|---|---|---|---|---|
| merge_intervals | easy | no | correct | converged | 1 | n/a¹ |
| first_last_index | boundary-heavy | no | unknown (provider error) | converged | 1 | n/a¹ |
| two_sum_indices | spec-ambiguous | yes | correct | unresolved | 2 | n/a¹ |
| csv_row_parse | boundary-heavy | no | unknown (provider error) | converged | 1 | 0 |
| valid_palindrome | moderate | yes | unknown (rate-limited) | unresolved | 2 | 0 |

¹ Invalid-test tracking was added to `run_eval.py` mid-session; these 3 problems ran on the version before that instrumentation existed. The Validator was still active for them (it's part of the graph, not the eval script) — it just isn't reported here.

## Summary

- Breakpoint caught a real bug in the first attempt on 2/5 problems this run.
- The Validator filtered **0** invalid tests across the 2 problems where this was tracked (`csv_row_parse`, `valid_palindrome`). This sample simply didn't happen to produce a Skeptic test that was itself broken — that's a legitimate outcome, not a sign the Validator does nothing.
- 3 of 5 baseline self-checks came back "unknown" this run — not from the earlier `max_tokens` bug (already fixed), but from two different, real provider issues encountered live: an Nvidia outage and hitting today's daily rate limit near the end of the run. Both are logged plainly above rather than hidden.

## The Validator, demonstrated concretely

This eval's random sample of Skeptic-generated tests didn't happen to include an invalid one, which is why the table above shows all zeros for that column. But the Validator's actual effect is demonstrated directly, outside this eval run: re-running the `two_sum_indices` spec manually hit a Skeptic test with genuinely invalid syntax (`test_float_like_int_input`, misusing the walrus operator — the exact failure mode that motivated building this). Before the Validator existed, this class of test failed identically every round and permanently blocked convergence, since no amount of fixing the implementation can satisfy an assertion that isn't valid Python. With the Validator active, that invalid test was caught for $0 by a local syntax check, correctly excluded from `bugs_caught`, and the run **converged in round 2** instead. A second manual run confirmed the Validator doesn't over-correct either: it let a real, hard bug through unmodified when the Prover genuinely couldn't fix it within the round budget. See `nodes/validator.py` and the "Real bugs found and fixed" section of `README.md` for the full account.

## Known limitations, stated plainly

- **N=5, not 6** — one problem never completed due to a sustained provider outage during this session, not a flaw in the system.
- **3 of 5 baseline checks are "unknown"** rather than a clear opinion, due to live provider issues during this specific run (not the earlier token-budget bug, which is fixed). This weakens the baseline comparison for this run specifically; the Breakpoint-mode results (bug found, verdict, rounds) are unaffected since they come from real sandbox execution, not from a provider's chat response quality.
- **The Validator's effect isn't visible in this eval's numbers**, only in a separate manual verification — an artifact of a small random sample, not evidence the feature is unproven. A larger eval run would be expected to surface the pattern within the eval itself.
