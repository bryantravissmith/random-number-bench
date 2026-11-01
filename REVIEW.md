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
