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
- The public runner explicitly restricts the provider to the audited OpenAI
  Responses adapter. Other adapters need their own transport validation.
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
- Round 003 is a new precommitted 1,000-trial-per-prompt round with those fixes.
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
after round 003 was frozen; round 003 runs continuously from its recorded source
snapshot and does not exercise recovery. Later rounds freeze the updated code.
