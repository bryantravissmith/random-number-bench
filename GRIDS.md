# Run a model and reasoning-effort grid

A grid fixes the models, requested effort levels, prompts, sample count and
execution limits before it makes any model calls. Each model/effort cell gets
the same two prompts. `--count 100` means **100 trials per prompt per cell**,
not 100 across the whole grid. Invalid answers are excluded and never replaced.

## Included profiles

| Profile | Models | Requested efforts | Cells | Trials at count 100 |
| --- | --- | --- | ---: | ---: |
| [Codex](grids/codex.json) | GPT-6.1 Sol/Terra, GPT-6 Astra/Sol/Luna, GPT-5.6 Sol/Terra/Luna | low, medium, high, xhigh, max | 40 | 8,000 |
| [Claude](grids/claude.json) | Opus/Sonnet/Haiku 5.5, Fable 5.1, Opus 4.8; Sonnet 4.6 | low through max; Sonnet 4.6 omits unsupported xhigh | 29 | 5,800 |
| [Grok](grids/grok.json) | Grok 4.5 and 4.7 | low, medium, high | 6 | 1,200 |
| [Pi/OpenRouter](grids/pi.json) | `openai/gpt-4o` | off, reasoning not applicable | 1 | 200 |
| [DeepSeek/Pi](grids/deepseek.json) | V3, V3-0324, R1-0528, V3.2, V4 Pro 0813, V4.1 Flash 1017 | supported off/on modes, temperatures 0.7 and 1.0 | 18 | 3,600 |

`ultra` is outside this experiment because it invokes agent delegation. Model
and effort availability depends on the installed CLI and account. Unsupported
models are never silently substituted. The Claude effort combinations follow
the [official support table](https://platform.claude.com/docs/en/build-with-claude/effort).

## Set up once

Install Python 3.12+, [uv](https://docs.astral.sh/uv/) and the CLI for the profile
you want. Sign in through that CLI. Native Claude, Codex and Grok use existing
subscription logins. For GPT-4o, run `pi`, choose `/login`, and connect
OpenRouter. This Pi profile uses paid OpenRouter credits. Credentials stay in
the CLI's private credential store and never enter Git.

```sh
uv run bench.py check --runner codex
uv run bench.py check --runner claude
uv run bench.py check --runner grok
```

These checks validate login and executable identity; a live smoke grid below
checks every requested model/effort combination. See [CLI details](CLI_RUNNERS.md).
The planner records CLI versions and hashes. A different installed version
requires a new plan. Keep old versions available if you need to resume later.

## Plan, run, inspect, export

Use a unique directory for every run. Planning does not call a model:

```sh
uv run grid.py plan --spec grids/codex.json --count 100 \
  --out runs/codex-grid-001
uv run grid.py run runs/codex-grid-001
uv run grid.py status runs/codex-grid-001
uv run grid.py export runs/codex-grid-001 --out results/codex-grid-001
uv run bench.py verify-public results/codex-grid-001
uv run bench.py report
```

Replace `codex` with `claude`, `grok` or `pi` to use another profile. For the
small GPT-4o experiment, add `--max-cost 1` to planning. This freezes a one-dollar
reported-usage ceiling; a few in-flight calls can finish after a ceiling is hit.

For a new CLI version or profile, first use `--count 1` and a separate
`runs/smoke-...` directory. That makes two calls per cell, for example 80 calls
for the full Codex smoke grid. Verify the smoke output before starting the
larger grid, and keep those checks out of the measured results.

To leave a long run in a terminal without streaming progress into the window:

```sh
uv run grid.py run runs/codex-grid-001 > runs/codex-grid-001.log 2>&1 &
uv run grid.py status runs/codex-grid-001
```

Different service grids can run concurrently. Run only one grid per service
at a time: each grid has its own controller, and launching two Codex grids
would double the account's concurrency. The default is four in-flight calls
per grid, not four per model or cell. Run the same command after Ctrl-C to
resume completed work without duplicating accepted trials. A batch lock rejects
two runners trying to own the same grid.

## What is frozen and how errors behave

Every replication block contains every model/effort cell once, in randomized
order. Each cell's baseline/meta pair is also randomly ordered. The same shared
controller schedules all cells in a service. `--order-seed` reproduces this
launch schedule only; it is never supplied as a model sampling seed. Completion
order can differ under concurrency, and the model outputs are not expected to
reproduce bit for bit.

`grid.json` is bound to the batch manifest. The manifest includes the full
per-trial schedule, canonical prompts, model/effort assignments, settings,
limits and hashes of frozen planner/runner/verifier source. Resuming executes
the frozen runner. Export verifies native evidence with the frozen verifier,
then creates an immutable public package that can be independently recounted.

The defaults are four concurrent calls, three physical attempts per logical
trial, a 180-second attempt timeout, a 24-hour active-run ceiling and a
100 USD runner-metadata ceiling. Change `--concurrency`, `--timeout`,
`--max-seconds` or `--max-cost` **when planning**, never by editing a frozen run.
Native CLI cost metadata is not a subscription charge; missing costs cannot
enforce a cost ceiling.

Rate limits, timeouts and transient provider failures drain active calls,
reduce concurrency and trigger backoff before bounded fresh-session retries.
Observed internal CLI retries exclude that entire CLI attempt. An authenticated
Codex connection sometimes returns an opaque WebSocket-edge 403; that exact
connection error is retried with the same bounded policy. Explicit authentication,
quota and other permission failures remain fatal.

Invalid completed answers are terminal and never replaced. Authentication,
quota and unknown protocol failures stop the service grid. Keep stopped rounds
and their accepted/attempted denominators visible. A fatal stop requires a new
plan after the cause is resolved; do not rewrite the old controller or pool a
replacement round silently.

## Change the models or efforts

Copy a profile to a new JSON file and edit its model list:

```json
{
  "runner": "codex",
  "models": [
    {"model": "gpt-6.1-sol", "efforts": ["low", "max"]},
    {"model": "gpt-6-luna", "efforts": ["low", "max"]}
  ]
}
```

Pass the new file with `--spec`. With four cells, `--count 100` runs 800 trials.
Use one service per profile, exact model identifiers, and supported effort
values. A grid combines its profile with the unchanged prompts in
[protocol.json](protocol.json). Prompt changes require a new protocol version
and separate results. The current Pi/OpenRouter profile is specifically audited
for non-reasoning OpenAI models, with OpenAI as the sole upstream and provider
fallback disabled. The separate [DeepSeek fixed-decoding profile](#deepseek-history-with-fixed-decoding)
also has validated routing and reasoning controls. Additional providers or
model/mode combinations need their own validated profile.

## Read confidence intervals

`RESULTS.md` aggregates rows into one report while retaining each grid, model,
requested effort and prompt separately. Its fixed columns 1 through 10 contain
answer counts and pointwise [95% Wilson confidence intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) for the proportion
of accepted answers in each bin. A zero count does not imply zero uncertainty.
With no accepted observations, the interval is n/a.

Intervals describe the conditional distribution among valid answers under an
independent, stable-trial assumption. They do not cover backend changes,
correlated generations, missing/invalid answers or CLI context differences.
They are not simultaneous confidence bands across ten bins or the entire grid.
Overlapping intervals are not a test that two settings are equivalent. TV to
uniform is a descriptive point estimate; finite uniform samples also have
nonzero TV. The report does not use a degenerate bootstrap interval for
all-identical responses.

## Publish and verify

After export, commit the new `results/<id>/` folder and regenerate `RESULTS.md`.
Never edit generated result JSON, JSONL or reports by hand. Full raw evidence
stays under ignored `runs/`; the public export contains allowlisted records,
hashes, schedules and frozen source. Keep the raw run locally for audit.

CI tests the runner without inference and verifies all public result hashes,
retry histories, grid mappings, tallies and intervals. Run the offline checks
locally before changing the harness:

```sh
uv run tests/test_batch.py
uv run tests/test_cli_runners.py
uv run tests/test_round.py
uv run tests/test_grid.py
uv run tests/test_grid_results.py
# These use installed Pi against a loopback fake server, with no provider calls.
uv run tests/test_pi_http.py
uv run tests/test_openrouter.py
```

## DeepSeek history with fixed decoding

[grids/deepseek.json](grids/deepseek.json) expands six releases into 18
model/mode/temperature cells. With both prompts and 100 trials per prompt this
is 3,600 logical trials. V3.1 was removed from future runs at Bryan's request
after its thinking condition returned no final answers. The original seven-release
profile is preserved as [deepseek-2026-11-01-full.json](grids/deepseek-2026-11-01-full.json);
use that file to reproduce the original 4,400-trial design. See the [study proposal](studies/reasoning-history/DEEPSEEK_PROPOSAL.md)
for the hypotheses, historical serving exception and cost scenarios.

```sh
# Separate 36-call validation run. Never pool this with measured results.
uv run grid.py plan --spec grids/deepseek.json --count 1 \
  --out runs/deepseek-smoke-001 --max-cost 1 --max-seconds 3600
uv run grid.py run runs/deepseek-smoke-001
uv run grid.py export runs/deepseek-smoke-001 --out probes/deepseek-smoke-001

# After verifying the smoke, freeze a separate measured grid.
uv run grid.py plan --spec grids/deepseek.json --count 100 \
  --out runs/deepseek-history-001 --concurrency 16 --max-cost 20
uv run grid.py run runs/deepseek-history-001
uv run grid.py export runs/deepseek-history-001 --out results/deepseek-history-001
uv run bench.py report
```

The measured command above uses a ceiling of 16 concurrent calls, with the
same adaptive backoff controller. Paid OpenRouter models have no platform-level
request cap, but pinned upstream capacity and account in-flight budgets still
apply. The controller can reduce concurrency on errors.
[OpenRouter limits](https://openrouter.ai/docs/api_reference/limits).

This profile explicitly sends temperature 0.7 or 1.0, top-p 1, top-k 0,
frequency penalty 0 and a 4,096 generated-token limit including reasoning.
It pins a route with fallbacks disabled and requires parameter support.
The original V3 anchor uses DeepInfra FP4 Flex; five subsequent releases use
SiliconFlow FP8. Keep these serving strata separate. Metadata does not
independently prove the actual backend weights or quantization.

`non_reasoning` omits the reasoning field; `off` explicitly disables it;
`required` and `on` enable it. V4 thinking calls also request high effort.
The Pi `high` setting is recorded for thinking calls, but the frozen payload
profile defines the effective OpenRouter control. Earlier enabled-only
profiles do not claim a high compute budget. A short answer can legitimately
report zero reasoning tokens even when reasoning was requested.

The new v2 grid schema freezes the complete request profile per schedule row.
`pi-profile.ts` applies these declared parameters before the separate
`pi-audit.ts` observer records the effective request. Export replays the frozen
verifier against every attempt, including exclusions, and checks the exact
profile, requested model, allowed catalog alias, upstream, finish reason,
and exposed reasoning usage. Unsupported controls or unexpected providers
stop the grid. They never trigger silent substitutions or relaxed controls.

Completed empty, malformed or truncated answers are terminal invalid trials
and are not retried. Truncations have their own count in each v2 cell summary
and an `invalid_reason` field in public attempts. Transport failures use the
same bounded backoff controller. Costs use OpenRouter's response `usage.cost`
when exposed, including failures that return usage. Missing costs are unknown
and cannot enforce the ceiling. In-flight calls can slightly exceed $20.

Both historical v1 exports and fixed-decoding v2 exports remain verifiable.
Do not apply the new settings to old GPT-4o results: that grid used API default
decoding. Preserve frozen source and create a new run after any source, model,
profile or routing change.

```sh
uv run tests/test_openrouter_profiles.py
# Requires installed Pi; calls only a loopback HTTP fixture.
uv run tests/test_deepseek_grid.py
```
