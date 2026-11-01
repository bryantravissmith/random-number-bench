# Independent design review

Grok 4.7, subscription CLI, before inference. Final review text follows.

I'll review only the study definition and look for confounds, unfair rubric assumptions, and measurement errors that should be fixed before a 40-call freeze.**Revise the grading contract and the run protocol, then run the same 40 calls.** Keep two cases, two effort levels, and ten independent repetitions. Do not add cases, arms, or repetitions.

The vignettes are usable for an exploratory effort comparison. The current primary endpoint cannot answer the coverage question cleanly, and a few effort-linked scoring rules can manufacture a coverage drop.

## Fix before any calls

1. **The primary endpoint mixes four different things.** "Distinct classes among useful responses," with invalid or unuseful trials treated as empty slots, moves when any of these move: parse or instruction compliance, factual consistency, the diagnostic floor, or true hypothesis diversity. Max effort is the arm most likely to add caveats, contrasts, or a longer trace, and those are exactly the behaviors that can zero a trial. Freeze three separate readouts per case and effort:
   - **Yield:** parse rate and useful rate.
   - **Diagnostic quality:** mean diagnostic score and the share scoring 2, computed on every schema-valid response. A mean taken only over useful responses truncates the score at 1 and hides the quality difference.
   - **Coverage:** distinct mechanism classes among useful responses, plus the expected-coverage curve. Also report concentration among the useful trials only (class shares or entropy). Read a drop in the empty-slot curve as a drop in diversity only when the yield and the conditional distribution say the same thing.

2. **The score-2 rule conflicts with the one-mechanism rule.** Score 2 wants a contrast that separates the proposed mechanism from alternatives. `single_mechanism` fails a list of distinct mechanisms. A careful answer will name that contrast in `test` or `expected_if_true`. Freeze this: exactly one nominated cause is required; discriminating contrast language in the test fields is allowed and is not a second hypothesis. A causal chain with one primary driver is one mechanism. If several causes are actually proposed, mark `single_mechanism` false and do not salvage one of them.

3. **Sampling and prompt assembly are unspecified.** Cross-sample coverage is undefined if decoding is deterministic, and it is confounded if effort also changes temperature, tools, or output headroom. Freeze, identically for both arms: fresh single-turn calls, the same non-zero temperature, tools off, and a max output large enough for hidden reasoning plus a short JSON object. The user message is the instruction plus that case's `observations` only. `mechanisms` and `planted_cause` stay grader-only. Record truncation and non-JSON reasoning by script. Strip them before grading so length and reasoning do not unblind effort.

## Fix in the rubric before grading

4. **This design does not measure truth-finding.** In `checkout`, device mix alone can produce the whole drop (stable within-device rates of about 14% desktop and 4% mobile), and paid-mix alone can do the same (about 13% other and 3% paid). The planted mobile payment-submission failure is consistent with the facts and is not identified by them. In `support`, the planted selection story matches the strongest surface clue, the response rate falling from 20% to 10% after the new survey email. An author-match is therefore a different event in each case. Keep any planted-cause tally per case, lock mechanism labels before anyone sees `planted_cause`, and define the match as codebook-class membership. Do not call it accuracy, and do not pool it across cases.

5. **Class boundaries will move the coverage count.** Freeze a few anchors. A tag-induced real payment failure is `checkout_regression`, not `measurement_artifact`. Pure undercounting is inconsistent with the processor confirmation. Campaign or audience mix is `acquisition_composition`; a demand shift that is not mix is `demand_change`. On `support`, "the email annoyed people who still answered" is `other`; who answers is `response_selection`. Stable resolved volume does not make `premature_closure` inconsistent. Canonicalize `other` labels blindly, and treat two labels as the same class when they name the same intervention and the same outcome path.

6. **Pre-register a parser and the comparison rule.** Count a trial as unparsed only after a deterministic extract of the first JSON object, with fences stripped. The claim stays descriptive and within case: ten generations are draws within a task, not ten tasks. Do not treat a coverage change as a change in diagnostic quality or in truth-finding.

## Leave as they are

The equal 40-call split, the forced single hypothesis, the 100-word cap, the hidden author story, and the two synthetic vignettes all fit this probe. The cap applies to both arms. Word-cap violations stay secondary and do not by themselves nullify a response.

## Coordinator decisions before freeze

Accepted separate yield/quality/coverage, contrast-language allowance, taxonomy anchors, blind author-story treatment, and explicit prompt assembly. Kept strict JSON parsing and report parse failures separately rather than silently repairing outputs. CLI defaults cannot be fixed to an exposed nonzero temperature or token budget; record that limitation rather than claim experimental control. Frozen study.json defines the actual protocol. The reviewer did not inspect execution code.
