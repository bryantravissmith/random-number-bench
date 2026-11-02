# Random Number Bench

- Preserve every attempted trial and failed attempt. Never replace invalid model responses.
- Freeze prompts, counts, schedule, model settings and source before inference.
- Keep protocol versions and independent rounds separate. Do not pool silently.
- Use the existing bounded concurrency controller for all conditions in a round.
- Native evidence and credentials stay outside Git; public exports use explicit fields.
- Never manually edit generated result JSON, JSONL or RESULTS.md. Rebuild with the exporter.
- Check offline tests before changing execution or evidence handling.
- Never use the em dash or add agent co-authors to commits.
- Publish only the harness, reproducible configurations, measured results and user documentation. Keep development artifacts local under ignored paths.
