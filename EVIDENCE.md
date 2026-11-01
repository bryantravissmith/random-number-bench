# Evidence and reproducibility

A local round contains its frozen protocol and schedule, original Pi sessions, provider-request/HTTP audit, process streams, immutable physical-attempt records, controller history, frozen source and verification report. These live under ignored `runs/` directories. The original historical batches are retained in their original local experiment package.

Before export, `verify.py` independently recounts successful answers from native session files, checks exact effective prompts and request settings, confirms one request/HTTP 200 per counted trial, verifies source/evidence hashes, checks distinct native sessions and rejects numbers from error attempts.

Each public result contains:

- `manifest.json`: prompts, design, model/runtime metadata, planned counts, source provenance and export file hashes.
- `attempts.jsonl`: one allowlisted row per physical attempt, including logical trial, condition, outcome, accepted answer text/number, timing, HTTP status, usage and original-file hashes.
- `summary.json`: per-condition counts, validity, exclusions, TV and Pi cost metadata.
- `source/`: the exact frozen runner, observer and verifier files used by each source batch.

Local paths and identifiers are replaced by SHA-256 hashes. Headers, credentials, environment values, full commands and free-form error text are omitted. A working-directory hash preserves comparability without publishing the local path embedded in the system prompt. Invalid-response free text stays in raw logs; its category and evidence hashes remain public.

Public rows are a selected-field export of verified native logs, not the full original provider transcript. Original hashes commit to those local files; they do not make private originals independently downloadable. The public verifier recounts the disclosed rows and checks their hashes, uniqueness and validity. These checks support auditability and reproducibility, but they are not independent provider attestations.

To recheck a public result, run `uv run --python 3.12 bench.py verify-public results/<id>`. To verify a local raw batch, run its frozen `snapshot/verify.py` against the batch directory. Do not use Python's `-O`, which disables assertions in the native verifier.

Exports are immutable. If evidence needs correction, preserve the old directory, document the reason, and create a newly identified export. Rebuild the index with `bench.py report`; never manually change generated JSON, JSONL or RESULTS.md.
