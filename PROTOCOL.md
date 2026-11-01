# Protocol v1

The canonical prompts and range are in [protocol.json](protocol.json).

One round contains 1,000 logical baseline trials and 1,000 logical sampling-meta trials. Freeze the complete trial schedule, exact prompts, model/provider/reasoning, Pi version and runner/observer/verifier source before the first request. The system preamble is `You are a helpful assistant.` New rounds use the neutral directory `/`, so Pi appends exactly `\n\n<cwd>\n/\n</cwd>` to that preamble. Historical runs used one shared private directory, recorded as a hash. The directory is identical for both conditions in each round.

Future rounds randomize order inside 1,000 adjacent baseline/meta pairs. A scheduling seed is recorded. It is not sent to the model. Four requests can overlap across conditions; one controller handles all backoff. Output order is descriptive and is not treated as a serial independence test.

No tools, MCP, user extensions, skills, prompt templates, project context files or themes are available. Only the explicit audit observer runs. Sampling overrides are absent. Provider retries, cache warming, compaction and telemetry are disabled in isolated settings. Existing credentials are used locally and are not copied into evidence.

An accepted answer must come from one successful audited request with HTTP 200, one native session, the exact expected prompt and model configuration, no tool calls and a successful final assistant message. After trimming surrounding whitespace, text must match a bare ASCII integer from 1 through 10. Prose, multiple numbers, refusals and out-of-range answers are invalid. Partial digits from errored requests are excluded.

Transport failures can be retried at most twice. The logical trial and prompt stay fixed; each attempt has a fresh session. Completed invalid responses are terminal. Record every attempt, including failures and interruptions. If a stop or exhaustion leaves fewer than 1,000 accepted numbers, report the shortfall; never fill it with additional logical trials.

For each condition publish all ten bin counts, accepted/planned count, physical attempts, excluded attempts, outcome categories, valid-response rate and TV from uniform. Valid-response rate excludes transport failures. Costs include reported attempt usage and are API-equivalent estimates. Keep rounds, models, settings and protocol versions separate.

The initial historical pair predates interleaving. It includes the previously completed baseline and a later meta batch in the same working directory. Label this design explicitly. Small exploratory prompt checks selected the meta-prompt, so these are descriptive results, not a blinded confirmatory hypothesis test. Do not infer causal prompt effects from separate time blocks alone.

Transport and timeout exclusions can select for latency, which may correlate with the answer or reasoning. Retain and report those failures even though they do not enter the histogram. Interruptions consume the physical-attempt budget because the provider may already have processed them. Hard time/cost limits do not renew on resume; preserve and publish stopped partial rounds with their shortfall and state.

The benchmark studies observed model behavior under specific prompts. It is not a random-number service, and a small TV score is not proof of independence or suitability for cryptography.
