# Evidence and reproducibility

A local round contains its frozen protocol and schedule, original Pi sessions and provider-request/HTTP audit or native CLI transcripts, process streams, immutable physical-attempt records, controller history, frozen source and verification report. These live under ignored `runs/` directories. The original historical batches are retained in their original local experiment package.

Before export, `verify.py` independently recounts successful answers and checks source/evidence hashes. For Pi it checks exact effective prompts and request settings, one request/HTTP 200 per counted trial, and distinct native sessions. For native CLIs it validates the frozen launcher request, reparses the original transcript, checks model identity where exposed, and checks unique sessions where exposed. Both reject numbers from error attempts. CLI internal retries and complete backend prompts are not observable.

Each public result contains:

- `manifest.json`: prompts, design, model/runtime metadata, planned counts, source provenance and export file hashes.
- `attempts.jsonl`: one allowlisted row per physical attempt, including logical trial, condition, outcome, accepted answer text/number, timing, HTTP status, usage and original-file hashes.
- `summary.json`: per-condition counts, validity, exclusions, TV and runner cost metadata.
- `source/`: the exact frozen runner, observer and verifier files used by each source batch.

Runner, runner version, evidence level and observed response model (when exposed) are preserved. The legacy field `pi_reported_cost` also stores native runner estimates; `reported_cost_known` distinguishes unknown values from reported zeroes.

Local paths and identifiers are replaced by SHA-256 hashes. Headers, credentials, environment values, full commands and free-form error text are omitted. A working-directory hash preserves comparability without publishing the local path embedded in the system prompt. Invalid-response free text stays in raw logs; its category and evidence hashes remain public.

Public rows are a selected-field export of verified native logs, not the full original provider transcript. Original hashes commit to those local files; they do not make private originals independently downloadable. The public verifier recounts the disclosed rows and checks their hashes, uniqueness and validity. These checks support auditability and reproducibility, but they are not independent provider attestations.

To recheck a public result, run `uv run --python 3.12 bench.py verify-public results/<id>`. To verify a local raw batch, run its frozen `snapshot/verify.py` against the batch directory. Do not use Python's `-O`, which disables assertions in the native verifier.

Exports are immutable. If evidence needs correction, preserve the old directory, document the reason, and create a newly identified export. Rebuild the index with `bench.py report`; never manually change generated JSON, JSONL or RESULTS.md.
