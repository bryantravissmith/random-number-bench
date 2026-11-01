# Protocol v1

The canonical prompts and range are in [protocol.json](protocol.json).

One standard round contains 1,000 logical baseline trials and 1,000 logical sampling-meta trials. Smaller rounds and model/effort grids record their planned counts explicitly. Freeze the complete trial schedule, exact prompts, model/provider/reasoning, runner name/version and runner/observer/verifier source before the first request. The system preamble is `You are a helpful assistant.` New rounds use the neutral directory `/`, so Pi appends exactly `\n\n<cwd>\n/\n</cwd>` to that preamble. Historical runs used one shared private directory, recorded as a hash. The directory is identical for both conditions in each round.

Future rounds randomize order inside 1,000 adjacent baseline/meta pairs. A scheduling seed is recorded. It is not sent to the model. Four requests can overlap across conditions; one controller handles all backoff. Output order is descriptive and is not treated as a serial independence test.

Model/effort grids use complete replication blocks. Each block contains every
cell once in shuffled order, with a randomly ordered adjacent baseline/meta
pair for each cell. All cells in a service share one concurrency and backoff
controller. Grid results stay separate by model, requested effort, prompt and
run. The canonical prompt text is unchanged. See [GRIDS.md](GRIDS.md) for the
checked-in profiles, frozen schedule, command sequence and interval method.

For Pi, no tools, MCP, user extensions, skills, prompt templates, project context files or themes are available. Only the explicit audit observer runs. Provider retries, cache warming, compaction and telemetry are disabled in isolated settings. Native CLIs disable or deny tools and suppress custom context where supported; their built-in prompts and internal HTTP retries are opaque. See [the per-runner controls](CLI_RUNNERS.md). Sampling overrides are absent. Existing credentials are used locally and are not copied into evidence.

For Pi, an accepted answer must come from one successful audited request with HTTP 200, one native session, the exact expected prompt and model configuration, no tool calls and a successful final assistant message. For native CLIs, acceptance requires a successful terminal transcript, a zero process exit, the exact frozen launcher request, no observed tools or errors, and the requested model wherever the CLI exposes it. These results carry `cli_transcript` evidence; no HTTP success is inferred from a successful process. After trimming surrounding whitespace, text must match a bare ASCII integer from 1 through 10. Prose, multiple numbers, refusals and out-of-range answers are invalid. Partial digits from errored requests are excluded. A completed native CLI turn whose final result text disagrees with its assistant text is a terminal invalid response; neither candidate contributes a number.

Transport failures can be retried at most twice. The logical trial and prompt stay fixed; each attempt has a fresh session. Completed invalid responses are terminal. Record every attempt, including failures and interruptions. If a stop or exhaustion leaves fewer than 1,000 accepted numbers, report the shortfall; never fill it with additional logical trials.

For each condition publish all ten bin counts, accepted/planned count, physical attempts, excluded attempts, outcome categories, valid-response rate and TV from uniform. Valid-response rate excludes transport failures. Costs include reported attempt usage and are API-equivalent estimates. Unreported costs are marked unknown; no API key or billed fallback is used for native CLI runs. Keep rounds, models, settings and protocol versions separate.

The initial historical pair predates interleaving. It includes the previously completed baseline and a later meta batch in the same working directory. Label this design explicitly. Small exploratory prompt checks selected the meta-prompt, so these are descriptive results, not a blinded confirmatory hypothesis test. Do not infer causal prompt effects from separate time blocks alone.

Transport and timeout exclusions can select for latency, which may correlate with the answer or reasoning. Retain and report those failures even though they do not enter the histogram. Interruptions consume the physical-attempt budget because the provider may already have processed them. Hard time/cost limits do not renew on resume; preserve and publish stopped partial rounds with their shortfall and state.

The benchmark studies observed model behavior under specific prompts. It is not a random-number service, and a small TV score is not proof of independence or suitability for cryptography.
