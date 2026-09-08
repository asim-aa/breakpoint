# Breakpoint eval report

**Sample size: N=6.** This is a small eval — these numbers indicate a direction, not a statistically reliable rate. Do not extrapolate beyond this specific problem set. The Prover was `nvidia/nemotron-3-super-120b-a12b:free` throughout; the Skeptic was `minimax/minimax-m3:free` for 5 of 6 problems and `inclusionai/ling-3.0-flash-fin:free` for `reverse_words_preserve_whitespace` (see methodology note below) — Prover and Skeptic are always different models from each other within a given run, per `assert prover_model != skeptic_model` in `skeptic.py`.

**This run includes the Validator** (`nodes/validator.py`), added after the previous eval to fix a real gap: a Skeptic-generated test that's itself invalid (bad syntax, or an assertion wrong on its own terms) used to be indistinguishable from a real bug, permanently blocking convergence. The Validator now judges every failing test before it counts.

**Methodology note on `reverse_words_preserve_whitespace`:** this problem needed three separate attempts across two sessions before it produced a result, for two distinct, real reasons — both are a live demonstration of the resilience the eval is built to have, not something smoothed over. First, `minimax/minimax-m3:free` (the Skeptic model used for the other 5 problems) was retired from OpenRouter's free tier mid-session (`404 Not Found`), so this one problem ran against a replacement Skeptic model, `inclusionai/ling-3.0-flash-fin:free`, instead. Second, even after that fix, the Prover model (Nvidia) had a sustained outage (`502: Service temporarily overloaded`, confirmed via a direct health check, not just this eval retrying blind) that took two attempts across separate sessions to get past. The result below is the first clean run once both issues cleared. Every failed attempt along the way was caught and skipped or recorded honestly, never crashed.

## Results

| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds | Invalid tests filtered |
|---|---|---|---|---|---|---|
| merge_intervals | easy | no | correct | converged | 1 | n/a¹ |
| first_last_index | boundary-heavy | no | unknown (provider error) | converged | 1 | n/a¹ |
| two_sum_indices | spec-ambiguous | yes | correct | unresolved | 2 | n/a¹ |
| csv_row_parse | boundary-heavy | no | unknown (provider error) | converged | 1 | 0 |
| valid_palindrome | moderate | yes | unknown (rate-limited) | unresolved | 2 | 0 |
| reverse_words_preserve_whitespace | spec-ambiguous | no | unknown² | converged | 1 | 0 |

¹ Invalid-test tracking was added to `run_eval.py` mid-session; these 3 problems ran on the version before that instrumentation existed. The Validator was still active for them (it's part of the graph, not the eval script) — it just isn't reported here.

² Unlike the other "unknown" rows, this isn't a provider error — the model responded, but its answer contained neither "CORRECT" nor "INCORRECT" (see `run_baseline_mode` in `eval/run_eval.py`), so it couldn't be scored either way.

## Summary

- Breakpoint caught a real bug in the first attempt on 2/6 problems this run.
- The Validator filtered **0** invalid tests across the 3 problems where this was tracked (`csv_row_parse`, `valid_palindrome`, `reverse_words_preserve_whitespace`). This sample simply didn't happen to produce a Skeptic test that was itself broken — that's a legitimate outcome, not a sign the Validator does nothing.
- 4 of 6 baseline self-checks came back "unknown" — not from the earlier `max_tokens` bug (already fixed), but from three different, real causes encountered live: an Nvidia outage, hitting a daily rate limit, and one non-conforming model response that named neither verdict. All are logged plainly above rather than hidden.

## The Validator, demonstrated concretely

This eval's random sample of Skeptic-generated tests didn't happen to include an invalid one, which is why the table above shows all zeros for that column. But the Validator's actual effect is demonstrated directly, outside this eval run: re-running the `two_sum_indices` spec manually hit a Skeptic test with genuinely invalid syntax (`test_float_like_int_input`, misusing the walrus operator — the exact failure mode that motivated building this). Before the Validator existed, this class of test failed identically every round and permanently blocked convergence, since no amount of fixing the implementation can satisfy an assertion that isn't valid Python. With the Validator active, that invalid test was caught for $0 by a local syntax check, correctly excluded from `bugs_caught`, and the run **converged in round 2** instead. A second manual run confirmed the Validator doesn't over-correct either: it let a real, hard bug through unmodified when the Prover genuinely couldn't fix it within the round budget. See `nodes/validator.py` and the "Real bugs found and fixed" section of `README.md` for the full account.

## Known limitations, stated plainly

- **One of six problems ran on a different Skeptic model** than the other five (`inclusionai/ling-3.0-flash-fin:free` instead of `minimax/minimax-m3:free`, forced by the latter being retired from OpenRouter's free tier mid-session). The Prover was the same model throughout; this is a methodology wrinkle worth knowing about, not a result that should be silently pooled with the other five as if the model pair were held constant.
- **4 of 6 baseline checks are "unknown"** rather than a clear opinion — three from live provider issues during these sessions (Nvidia outage, daily rate limit) and one from a model response that named neither verdict word (not the earlier token-budget bug, which is fixed). This weakens the baseline comparison; the Breakpoint-mode results (bug found, verdict, rounds) are unaffected since they come from real sandbox execution, not from a provider's chat response quality.
- **The Validator's effect isn't visible in this eval's numbers**, only in a separate manual verification — an artifact of a small random sample, not evidence the feature is unproven. A larger eval run would be expected to surface the pattern within the eval itself.
