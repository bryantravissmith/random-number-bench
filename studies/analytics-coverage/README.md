# Does requested reasoning effort change useful hypothesis coverage?

A small follow-up to the random-number benchmark: two ambiguous analytics cases,
ten independent responses per case at requested low and max effort, using Claude
Opus 5.5 through the subscription CLI. Forty planned calls. This is a pilot of
repeated top-hypothesis generation, not a test of every alternative a model can
represent internally.

**Partial result, 2026-11-01:** the run stopped at its frozen 30-minute limit
with 23/40 terminal trials: 22 schema-valid responses, one invalid response and
four timeout attempts (27 physical attempts total). At Bryan's request, all
22 valid responses were independently graded and audited while blind to effort.
See the [graded report and evidence](results/2026-11-01-001-partial/REPORT.md).

Both efforts produced **one useful mechanism per case**. Checkout low and max
each returned five mobile checkout-regression hypotheses. Support low returned
six issue-composition hypotheses; support max returned six response-selection
hypotheses. Grok and the coordinator agreed on every scoring dimension. All
22 valid answers scored 2/2, so this rubric cannot demonstrate a quality benefit
from max. It is a ceiling result, not proof that answer quality is identical.

Max used much more reported reasoning in the accepted responses: means of
14,304 tokens for checkout and 15,731 for support, versus 23 and zero at low.
That confirms a difference in exposed usage, not better reasoning. The observed
pattern is a changed preferred support hypothesis with equally concentrated
repetition, not evidence of an effort/diversity tradeoff in these two cases.

This is a post-hoc partial analysis. Sixteen trials never started and one remained
unfinished after a timeout. Missing trials are not scored as failures. Coverage
uses equal five-call subsets of completed trials and, separately, useful answers.
The cutoff may select for latency or answer content, and there are only five or
six valid responses per cell. No further inference or replacement was performed.

The native evidence remains preserved under `runs/analytics-coverage-2026-11-01-001`.
Its original snapshot and execution limits were not modified. The publication
includes the original frozen inference code, a separate partial exporter, sealed
grades, coordinator audit, treatment key and frozen analysis code.

The frozen [study definition](study.json) contains the exact prompts and grading
contract. Models see only case observations and the response instruction. The
mechanism codebook, author stories and grading rubric are never supplied during
inference. Each call returns one hypothesis, a diagnostic test, and its predicted
result. The two author stories are not identifiable from the observations, so
other plausible mechanisms are not wrong answers.

## What the pilot measures

- Yield: schema-valid and useful responses out of ten planned sessions.
- Diagnostic quality: mean score (0-2) and score-2 share among all schema-valid
  responses, including responses that fail another grading dimension.
- Coverage: distinct useful causal mechanism classes and their frequencies.
  Coverage at five calls averages over every possible subset of five from the
  ten observed trials. Invalid or unuseful trials contribute no class. This is
  descriptive rarefaction, not an estimate of unseen alternatives.

Useful means one nominated mechanism consistent with the observations and a
diagnostic score of at least 1. A discriminating contrast in the test is allowed.
Different wording for the same mechanism does not earn another class. A
100-word instruction applies to both arms; violations are reported, not excluded.
Complete strict JSON is required, without fences or surrounding prose. Invalid
responses are retained and never replaced.

The CLI's temperature, seed and output budget are opaque defaults. Only the
requested effort flag differs; the actual backend reasoning allocation is not
independently verified. Higher requested effort counts as a quality improvement
only if the diagnostic grades improve. Two tasks cannot establish a general
effect of reasoning training, RL, creativity, or analytics performance. Repeated
answers are draws within a task, not independent tasks for a pooled p-value.

## Reproduce a fresh round

Use Python 3.12+, `uv`, and an authenticated Claude subscription CLI supporting
the requested model and effort levels. See [CLI setup](../../CLI_RUNNERS.md).
Use a new run ID each time. Planning freezes the source, study, prompts, CLI
version, control limits and randomized schedule before any inference.

```sh
uv run --python 3.12 studies/analytics-coverage/probe.py plan runs/analytics-pilot-002
uv run --python 3.12 studies/analytics-coverage/probe.py run runs/analytics-pilot-002
uv run --python 3.12 studies/analytics-coverage/probe.py verify runs/analytics-pilot-002
uv run --python 3.12 studies/analytics-coverage/probe.py export runs/analytics-pilot-002 \
  --out studies/analytics-coverage/results/pilot-002
```

Each replication block randomizes case order and then low/max order within each
case. Fresh sessions use neutral working directory `/`, one supplied prompt, no
tools, and no resume or fallback. The existing benchmark controller provides up
to four concurrent calls, bounded retries for operational errors, backoff,
180-second call timeouts, and a 30-minute run limit. A reported metadata cost
ceiling of $20 is not a subscription charge. Invalid final responses are terminal.
The run command resumes a paused run using frozen sources.

Native CLI logs remain in ignored `runs/`. Public exports contain explicit fields,
source snapshots and evidence hashes. The number benchmark's generic verifier
does not apply to this JSON response contract; use the frozen `probe.py verify`.
This study's results remain outside the top-level integer `RESULTS.md`.

## Grade before revealing effort

Export creates shuffled `blind-responses.json` and a separate `blind-key.json` in
the private run directory. Do not read the key or inspect per-effort outputs
until grades and any audit changes are frozen.

```sh
uv run --python 3.12 studies/analytics-coverage/analyze.py packet \
  runs/analytics-pilot-002 runs/analytics-pilot-002/grading-prompt.txt
```

Give that packet alone to an independent grader. It excludes model settings,
order, timing, token usage and author stories. Save its JSON array as
`independent-grades.json`. Audit the response labels while still blinded; save
final `grades.json` and an `audit.json` explaining each change, or explicitly
recording no changes. Then freeze before unblinding:

```sh
uv run --python 3.12 studies/analytics-coverage/analyze.py freeze \
  runs/analytics-pilot-002 studies/analytics-coverage/results/pilot-002 \
  runs/analytics-pilot-002/grades.json \
  runs/analytics-pilot-002/independent-grades.json \
  runs/analytics-pilot-002/audit.json \
  runs/analytics-pilot-002/grading-prompt.txt
uv run --python 3.12 studies/analytics-coverage/results/pilot-002/grading/analyze.py report \
  studies/analytics-coverage/results/pilot-002
```

The report command verifies public inference hashes, sealed grades and the exact
answer-to-key mapping before joining effort. It generates `summary.json` and
`REPORT.md`; do not edit them manually. Keep independent grades, audit decisions
and the grading prompt with the public round. Model grading is not human
validation, and prose style may still suggest effort despite metadata blinding.

For the initial pilot, Grok 4.7 reviewed the design before calls and independently
graded the 22 available valid responses. The coordinator recorded initial grades
before reading Grok's grades, audited while still blind, and sealed the unchanged
grades before joining effort. See [design review](design-review.md) and the
[grading audit](results/2026-11-01-001-partial/grading/audit.json).

## Grade a stopped partial run

This is an explicit post-hoc analysis, separate from completing the original
study. The partial exporter refuses active runs and refuses to replace an
existing export or blind assignment. It imports the run's frozen verifier,
retains all attempts and preserves `state: stopped`.

```sh
uv run studies/analytics-coverage/export_partial.py runs/stopped-pilot \
  --out studies/analytics-coverage/results/stopped-pilot-partial
```

Then use the same packet, blind grading, audit and freeze sequence above.
Record a `partial-analysis-plan.json` in the private run directory before reading
the treatment key; freeze includes it and any grader-provenance/initial-audit
files in the grading seal. The report shows completed, valid and useful counts
separately. Its completed-trial rarefaction denominator never includes unfinished
calls, and it reports n/a rather than extrapolating beyond observed samples.

To reproduce the published partial report without model calls or private logs:

```sh
uv run --python 3.12 studies/analytics-coverage/results/2026-11-01-001-partial/grading/analyze.py report \
  studies/analytics-coverage/results/2026-11-01-001-partial
```

The frozen analyzer verifies public inference hashes, the grading seal and
answer-to-key mapping before computing the report. Keep this study out of the
integer comparison matrix and top-level RESULTS.md.

## Offline checks

```sh
uv run --python 3.12 tests/test_analytics_coverage.py
uv run --python 3.12 tests/test_analytics_analysis.py
```

These exercise the frozen runner end to end against a fake CLI, native error
exclusion, evidence tampering, grade validation and exact coverage calculations.
