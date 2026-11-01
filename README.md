# Random Number Bench

What distribution do you get when you ask an LLM for a random integer from 1 to 10?

This benchmark runs **1,000 fresh sessions for each of two fixed prompts**, records every attempt, and compares the accepted answers with a uniform distribution. Each repeat gets its own immutable result directory.

**[Recorded results](RESULTS.md)** · [50-trial effort probes](PROBES.md) · [Protocol](PROTOCOL.md) · [Public evidence format](EVIDENCE.md)

The completed Claude Opus 5.5 subscription round returned `7` in all 1,000 baseline trials. The meta-prompt produced 958 sevens, 37 sixes, three fours and one three; one prose response was excluded. The two earlier stopped Claude rounds remain separate in the report. These are descriptive results under the recorded CLI settings.

## The two conditions

**Baseline**

> Generate one random integer from 1 to 10, inclusive. Reply with only the integer.

**Sampling meta-prompt**

> Your response will be one observation in a random sample collected from many independent LLM sessions. Generate one random integer from 1 to 10, inclusive. Reply with only the integer.

Within each round both prompts use the same model, reasoning setting, runner and working directory. New rounds use the neutral working directory `/`; Pi appends `<cwd>\n/\n</cwd>` to the system preamble. Each trial starts a fresh conversation. No external random value, temperature override or seed override is supplied. Tools are disabled or denied, and observed tool use excludes an attempt. The scheduling seed only controls condition order.

Pi remains the default. Native `claude`, `codex`, `grok` and `agy` runners are additional options that use existing subscription/account logins. Native CLI calls carry their own system context and expose less request detail than Pi. Treat the runner as part of the experimental condition. See [CLI setup and evidence limits](CLI_RUNNERS.md).

## Run another round

Requires macOS or Linux, Python 3.12+, [uv](https://docs.astral.sh/uv/) and the selected CLI. Pi additionally requires Node.js 22.19+. To use [Pi](https://github.com/earendil-works/pi): The runner uses POSIX file locks and process groups.

```sh
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@1.3.0
pi
# In Pi, use /login to authenticate your provider, then exit.
```

From this repository:

```sh
# Choose a new ID for every repeat. Planning makes no inference calls.
uv run --python 3.12 bench.py plan --out runs/round-001

# 1,000 trials per condition, at most four requests in flight across both.
uv run --python 3.12 bench.py run runs/round-001

# Independently verify native logs and create the public evidence package.
uv run --python 3.12 bench.py export runs/round-001 --out results/round-001
uv run --python 3.12 bench.py verify-public results/round-001
uv run --python 3.12 bench.py report
```

Repeat with `round-002`, `round-003`, and so on. Add `--model` or `--thinking` to `plan` to choose settings. Use `--runner claude --model claude-opus-5-5` for Claude Code, or `--runner codex`, `--runner grok`, `--runner agy`. Pi currently audits OpenAI Responses only. `--count` changes trials **per condition** and is useful for engineering smoke checks; the benchmark default is 1,000 each. Preserve smoke checks separately from measured rounds. Time, metadata-cost, attempt and timeout limits can be set before freezing with `--max-seconds`, `--max-cost`, `--max-attempts` and `--timeout`.

The two conditions are interleaved in randomized adjacent pairs under one shared controller. Every trial starts a new runner process and conversation. Ctrl-C pauses the round; the same `run` command resumes it using frozen source without repeating completed trials. Exports refuse to overwrite an existing result directory.

After reviewing a public export, commit its `results/<id>/` directory and the regenerated `RESULTS.md`. The local `runs/` directory is ignored. No recurring schedule or automatic publication is installed. CI runs offline tests and checks published records; it makes no inference calls.

Exploratory 50-trial rounds live separately in `probes/<id>/`. Generate their report with `uv run bench.py report --probes`; see [probe design and interpretation](PROBE_NOTES.md). Reports show the thinking/effort setting as well as the model. For a subscription-backed thinking-off comparison, the Claude adapter supports `--model claude-haiku-4-5-20251001 --thinking off`; see [the validated profile](CLI_RUNNERS.md#non-reasoning-comparisons).

## Errors and rate limits

- A 429, transient provider error or timeout pauses new launches and halves concurrency, down to one. Existing requests drain.
- Backoff starts at 15 seconds, doubles with repeated trouble, adds jitter, and respects longer `Retry-After` hints. Twenty healthy completions increase concurrency by one, up to four.
- Each logical trial allows at most three physical attempts. Retries use a fresh conversation with the **same prompt**. Pi's hidden retries and cache warming are disabled. Native CLI internal HTTP retries are not visible; their attempts count CLI processes, not audited HTTP calls.
- Failed attempts never contribute a number. Completed invalid responses are retained and never retried. Counts, valid-response rate and operational failures are reported separately.
- Authentication, billing/quota and protocol errors stop the batch. A persistent-error circuit, two-hour active-run limit and 10 USD runner-metadata limit (where reported) also stop it. Limits apply across resumes; in-flight requests can exceed a ceiling slightly. Missing costs display as `n/a` and cannot enforce a cost ceiling. These are reported estimates, not subscription charges.

## Read the results

The primary score is total variation distance from uniform: `0.5 * sum(abs(observed_share - 0.1))`. Zero means an exactly uniform histogram; 0.90 is the maximum for ten bins. A finite truly uniform sample normally has nonzero TV. A uniform-looking histogram also does not prove independent random draws.

The first published pair combines the already completed baseline with a later 1,000-trial meta-prompt batch. Both used the same private working directory and model settings, but were run in separate time blocks and have separately recorded runner snapshots. Earlier small prompt explorations informed the choice of meta-prompt and are not pooled into these results. Future rounds use interleaving and the neutral directory `/`. That system-context change is another reason to keep historical and future rounds separate.

Pi's configured model ID and outgoing payload are checked. This does not independently identify an immutable backend model snapshot. Token/cost metadata comes from Pi and is not a verified subscription charge. See [the evidence limits](EVIDENCE.md).

## Test without calling a model

```sh
uv run --python 3.12 tests/test_batch.py
uv run --python 3.12 tests/test_round.py
uv run --python 3.12 tests/test_cli_runners.py
# Requires installed Pi; points it at a loopback HTTP server, not a real provider.
uv run --python 3.12 tests/test_pi_http.py
```

The suite covers process concurrency, 429/503, Retry-After, timeouts, invalid output, retry exhaustion, fatal errors, hidden retries, interrupted shutdown, resume, duplicate ownership, per-trial prompts, public export and tampering. Protocol and runner changes belong in new rounds; preserve historical evidence.
