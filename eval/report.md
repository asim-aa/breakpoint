# Breakpoint eval report

**Sample size: N=6 of 6 planned.** This is a small eval — these numbers indicate a direction, not a statistically reliable rate. Do not extrapolate beyond this specific problem set and this specific model pair (`nvidia/nemotron-3-super-120b-a12b:free` as Prover, `minimax/minimax-m3:free` as Skeptic).

**Methodology note:** Breakpoint mode ran with `MAX_ROUNDS=2` (not the system default of 5) to fit within OpenRouter's free-tier daily request cap. The baseline reuses Breakpoint's own round-1 Prover code and asks the same model to self-assess it with no execution — this isolates the effect of adversarial *testing*, not a different implementation. Run across two sessions (the free tier's 50 requests/day cap doesn't stretch to a full 6-problem run with a generous per-call token budget in one sitting).

## Results

| Problem | Difficulty | Bug in 1st attempt? | Baseline self-check | Final verdict | Rounds |
|---|---|---|---|---|---|
| merge_intervals | easy | no | correct | converged | 1 |
| first_last_index | boundary-heavy | yes | correct | unresolved | 2 |
| two_sum_indices | spec-ambiguous | yes | unknown | unresolved | 2 |
| csv_row_parse | boundary-heavy | yes | correct | unresolved | 2 |
| reverse_words_preserve_whitespace | spec-ambiguous | yes | correct | unresolved | 2 |
| valid_palindrome | moderate | yes | unknown | unresolved | 2 |

## Summary

- Breakpoint caught a real bug in the first attempt on **5/6** problems.
- Of those 5, the baseline self-check (same model, no execution) said **"CORRECT" on 3/5** — a same-model opinion-only check missed a real bug that Breakpoint's actual test execution caught, on more than half the problems that had one. The other 2 came back "unknown" (see limitation note below) rather than a false "correct," so they're inconclusive, not a baseline success.
- 1/6 problems converged within the 2-round budget; 5/6 stayed unresolved (the round budget was deliberately capped at 2 to fit the free-tier quota — the system default is 5, and V3's testing showed harder specs typically need more rounds to fully converge).
- Average rounds taken: 1.83 (budget capped at 2).

## The core result, concretely

On **3 of 6 problems** (`first_last_index`, `csv_row_parse`, `reverse_words_preserve_whitespace`), the exact same model that wrote the code, asked "does this look correct?" with no execution, said **"correct"** — on an implementation that a real, executed test proved was wrong. That gap between "looks right" and "survives execution" is the entire thesis this project is built to demonstrate, and this eval reproduces it across half the problem set, not just as a one-off anecdote.

## Known limitations, stated plainly

- **2 of 6 baseline checks came back "unknown"** rather than a clear verdict, even after fixing an earlier `max_tokens=20` truncation bug (raised to 300). The model's reasoning field can still occasionally consume the full budget on especially tricky specs (`two_sum_indices`, `valid_palindrome`) without producing a final CORRECT/INCORRECT word. This weakens those two data points to "no signal" rather than a false positive for the baseline.
- **The round budget (2) was capped below the system default (5)** purely to fit OpenRouter's free-tier daily quota, not because 2 rounds is architecturally correct. 5/6 problems staying "unresolved" at this cap says more about the quota constraint than about the retry loop's real convergence ability — see the V3 testing in this project's development history, where several specs did converge within 3-4 rounds once given the room to.
- **N=6 is still small.** Read this as "the mechanism works and the effect is real," not as a statistically reliable bug-catch rate for any general claim about LLM-generated code.
