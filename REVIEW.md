# Independent implementation review

2026-11-01. Claude completed a read-only review through the External Model
Orchestrator, covering the runner, verifier, repeat-round wrapper and public
export. No provider subagents were spawned. The review made no repository edits
or inference benchmark calls. Its findings led to:

- Recovery of orphan attempts now retains available usage, HTTP status and hashes
  while excluding the unknown answer.
- Both native and public verifiers reject attempt gaps, retries after terminal
  answers, and retries beyond the frozen physical-attempt bound.
- Native verification requires complete evidence hash coverage and refuses
  Python's optimized mode, which disables assertions.
- New rounds use `/` as a neutral, public working directory. Historical runs keep
  their original inputs and disclose the directory hash and context difference.
- Result tables show complete, paused or stopped state, alongside accepted and
  planned trial counts. Partial results remain reportable.
- At that revision, the public Pi runner restricted the provider to the audited
  OpenAI Responses adapter. Subsequent OpenRouter validations are documented in
  [GRIDS.md](GRIDS.md); new adapters still need transport validation.
- Exports lock source batches, check all relevant condition settings and publish
  a verified directory atomically. Free-form errors and local paths stay local.

Two suggested changes were intentionally declined. Aggregate time/cost limits
remain hard stops rather than replenishable budgets. Operator interruptions still
consume physical attempts because an unknown request may have reached the
provider. These rules preserve the precommitted bounds; CLI flags expose limits
before a round is frozen, and shortfalls stay visible.

Regression coverage includes interruption/recovery, prompt preservation across
retries, rejection of replaced invalid responses and incomplete evidence hashes,
public redaction, owned-batch export refusal and tampering. The actual installed
Pi was also tested against a loopback 429/200 server without any model calls.

## Native runner implementation and validation

Claude supplied a bounded draft implementation and fixture tests through the
External Model Orchestrator workflow. It edited only a dedicated linked worktree. Its shell and
reference-file permissions prevented it from validating three CLI interfaces or
running tests. The coordinator checked installed help, replaced the assumed
Grok/AGY flags and AGY project schema, corrected OAuth/OIDC handling, and ran the
fixtures and real subscription smoke checks locally. No API-key fallback was used.

Two early measured Claude rounds stopped on transcript cases missing from the
initial fixtures. Their records and source snapshots remain separate immutable
exports; no partial answer was silently promoted into the distribution.

- Round 001 stopped at 174 attempted trials. One assistant message arrived in
  separate thinking and text events. The parser now groups by message ID while
  rejecting multiple message identities. One unrelated invalid text response
  also remains excluded in that round.
- Round 002 stopped at 126 attempted trials. A successful CLI terminal event had
  an empty final result but nonempty intermediate text. This is now a terminal
  invalid response, with no number and no retry. Identity, model, tools and
  malformed-structure errors still stop the controller.
- Round 003 completed 1,000 trials per prompt with those fixes. Baseline accepted
  all 1,000; meta accepted 999 and excluded one prose response without retry.
  The two stopped rounds are not pooled into it.

The native CLI tests cover credential rejection, exact prompt/command provenance,
errors containing digits, observed tool use, model/session mismatch, split
thinking/text events, conflicting final text, cooldown hints such as `3m17s`, and
unknown cost metadata. A fake Codex process exercises the whole plan/run/export
path in CI without inference. The Pi process, retry, resume and loopback HTTP
tests continue to pass.

Native recovery was also tested with a deliberately orphaned live child process.
Resume now refuses to start duplicate work until that child exits, and recovered
attempts retain the correct runner/evidence label. This recovery fix was made
after round 003 was frozen; round 003 ran continuously from its recorded source
snapshot and did not exercise recovery. Later rounds freeze the updated code.

The export-time verifier also compares reparsed session/model identifiers and
usage metadata against the saved attempt. Tampering tests cover those fields,
so changing a derived session ID cannot conceal reuse. This strengthens the
independent exporter without changing any model call or recorded result.


## 2026-11-01 presentation and publication cleanup

Grok 4.7 performed a read-only methodology review through the External Model
Orchestrator before the final presentation revision. It identified five risks:
V3.1 default inclusion, mixing On profiles with native effort flags, treating
named families as ancestry, undefined effective-answer-count estimates, and
mixing operational retries with invalid logical responses. The coordinator
checked these findings against the frozen exports.

The final presentation is one model-by-effort matrix, requested by Bryan,
with paired prompt values. It hides V3.1 by default, reveals it in an archive
block, keeps DeepSeek On separate from native High, preserves temperature and
serving strata, defines `2^H` per cell/prompt, and shows no invented score CI.
Cell details retain fixed-bin Wilson intervals and separate validity, operational
and truncation counts. Display families carry no ancestry claim. The reviewer
examined the earlier presentation, not the final rewritten matrix; browser QA
and generated-artifact checks cover that revision.

The four completed grid exports remain unchanged. Codex is still running and
excluded from this snapshot. The analytics pilot's frozen verifier passed for
27 physical attempts, but the run stopped after 23/40 terminal trials. It is
incomplete and ungraded; documentation no longer implies a completed diversity
finding. No new benchmark inference was performed for this cleanup.

## Repository consistency audit, 2026-11-01

Reverified all eight result packages and six probe packages, reconciled the
three static figure collections with their source cell counts, checked every
tracked Markdown file link and heading anchor, and checked the matrix/image
source hashes. Rebuilding RESULTS.md and PROBES.md produced no data-table changes.
All six grid profiles expand to their documented setting and trial counts.

The installed Pi integration checks (`test_pi_http.py`, `test_openrouter.py`,
`test_deepseek_grid.py`) passed against local HTTP fixtures without provider
inference. Local run folders, credentials and preview assets remain untracked.

Corrected stale pre-DeepSeek support descriptions, separated default round/grid
limits, added DeepSeek to the profile index, and made the matrix shortfall label
refer to the planned sample size. The latter was reproduced and rechecked with
a 200-trial presentation fixture. Historical evidence and original study
proposals were preserved. Codex remains active; the analytics pilot remains
stopped, incomplete and ungraded.
