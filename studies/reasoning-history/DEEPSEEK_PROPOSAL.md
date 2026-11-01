# DeepSeek release-history grid proposal

Proposed 2026-11-01. **Completed 2026-11-01 at 22:03 UTC.** The measured
[16-concurrency-ceiling run](../../results/2026-11-01-deepseek-history-c16-001)
finished all 4,400 logical trials in 91.7 minutes: 3,993 valid integers,
407 invalid final responses and 27 timeout attempts. Reported API cost was
$0.956; timeout charges without usage are unknown. The separate smoke and
superseded four-concurrency pilot remain unpooled.

All 400 V3.1 thinking trials returned empty final answers under the pinned
SiliconFlow FP8 profile. Those cells are n=0, not uniform or concentrated
distributions. The seven other invalid outputs were code, prose or a fenced
number. Preserve them; a repaired serving configuration requires a separate
validation and fresh round. See [results with intervals](../../RESULTS.md) and
[figures](../../figures/2026-11-01-deepseek-history).

The original proposal follows for provenance; its future-tense preflight
instructions describe the plan at proposal time.

## Question and prediction

Does reasoning-oriented post-training make a model's requested random numbers
more concentrated? Track historical releases and compare thinking off/on
within hybrid checkpoints. Higher TV from uniform, lower answer entropy and
higher modal-answer share would support the concentration prediction.

This is an exploratory test of association and inference-mode behavior. It
cannot isolate the causal effect of RL training without matched training-stage
checkpoints. Non-thinking models may already have received RL. DeepSeek V3's
[training report](https://github.com/deepseek-ai/DeepSeek-V3) explicitly includes
SFT and RL, so the earliest row is not an untreated pre-RL control.

## Full model grid

Each model/mode combination gets both fixed prompts, temperatures 0.7 and 1.0,
and 100 logical trials for each prompt/temperature combination.

| Release period | Exact OpenRouter ID | Modes | Planned trials | Role |
| --- | --- | --- | ---: | --- |
| 2024-12 | `deepseek/deepseek-chat` | Non-thinking | 400 | Early V3 historical anchor; separate serving stratum |
| 2025-03 | `deepseek/deepseek-chat-v3-0324` | Non-thinking | 400 | Versioned V3 instruction baseline |
| 2025-05 | `deepseek/deepseek-r1-0528` | Thinking required | 400 | R1 reasoning-trained lineage |
| 2025-08 | `deepseek/deepseek-chat-v3.1` | Off, on | 800 | First hybrid within-checkpoint comparison |
| 2025-12 | `deepseek/deepseek-v3.2` | Off, on | 800 | Later hybrid comparison |
| 2026-08 | `deepseek/deepseek-v4-pro-0813` | Off, on at high effort | 800 | V4 large-model reference |
| 2026-10 | `deepseek/deepseek-v4.1-flash-1017` | Off, on at high effort | 800 | Recent dated V4.1 reference |
| **Total** | **7 releases / 11 model-mode combinations** | | **4,400** | **44 analysis cells, n=100 planned per cell** |

The first row's catalog canonical slug is `deepseek/deepseek-chat-v3`, with
weights identified as `deepseek-ai/DeepSeek-V3`. Its public request ID is an
alias, so record the returned model identity and the endpoint's advertised
checkpoint. Do not claim that the alias independently proves immutable weights.
Original January 2025 R1 and earlier V2 releases were not in the checked catalog;
record those historical gaps rather than silently substituting R1-0528.

## What stays constant

- Runner: Pi through OpenRouter, fresh session per attempt, no tools or external
  random value. Use the same neutral working directory and system preamble.
- Prompts: the existing baseline and sampling meta-prompt, verbatim from
  `protocol.json`. No mode-specific text is added to the user prompt.
- Sampling: explicitly set temperature to 0.7 or 1.0 and top-p to 1.0. Disable
  top-k with the provider's documented value after a smoke check, and set the
  supported frequency penalty to zero. Record unsupported or hidden controls
  as unknown. No fixed generation seed is reused across calls.
- Output budget: 4,096 total generated tokens per attempt, including reasoning.
  Report truncation separately. A truncated final answer is excluded.
- Thinking: send an explicit off setting for hybrid off cells and an explicit
  enabled setting for on cells. Use high effort for the two V4 releases. R1 is
  thinking-only; the two V3 non-thinking releases receive no reasoning option.
  The V3 hybrid controls do not establish the same compute budget as V4 high.
- Routing: pin `siliconflow/fp8` for the six releases from V3-0324 onward, with
  fallback disabled and parameter support required. Match the provider and
  advertised precision across every off/on pair.
- Historical anchor: December 2024 V3 currently has a listed
  `deepinfra/fp4/flex` route. Keep its provider/precision difference conspicuous
  in tables and plots; exclude it from the primary matched-serving trend.

Two temperatures test whether the pattern survives a sampling change. Analyze
them separately. This fixed-decoding study is separate from the already-running
native CLI grids, whose backend sampling defaults are opaque.

## Preflight, scheduling and errors

First extend the Pi audit to support these upstreams, per-cell temperatures and
thinking controls. The current audited OpenRouter profile is GPT-4o only. Test
outgoing payloads, routing, model identity, final-answer parsing, truncations,
hidden retries and usage against a local HTTP fixture before live calls.

Then run a separate **44-trial smoke grid**, one trial per analysis cell. Use it
only for engineering validation, latency and cost estimates, not for choosing
models based on their answers. If an endpoint or required control is unavailable,
record the gap and revise the frozen plan before the measured run. A missing
December V3 anchor yields 4,000 measured trials; do not replace it with a newer
checkpoint under the historical label.

Freeze all 44 cells, prompts, settings, source hashes and the full schedule
before measured inference. Each of 100 complete replication blocks contains
every model/mode/temperature combination, with an adjacent baseline/meta pair
in randomized order. Use one shared controller, up to four calls in flight,
with adaptive backoff. Run no other OpenRouter grid concurrently.

Each logical trial permits at most three fresh-session physical attempts for
transport failures. Completed invalid responses are terminal and are never
replaced. Retain all attempts; no error response contributes a number. Suggested
limits: 180 seconds per attempt, 24 hours per grid and 20 USD reported usage.
Budgets include failures and resume without resetting. Quota/authentication or
unknown protocol failures stop the grid and preserve the partial result.

## Cost and duration

The checked SiliconFlow output prices range from 0.80 to 3.96 USD per million
tokens across the six primary releases; the historical anchor's listed output
price is 0.712 USD per million. Prices are recorded in the endpoint snapshot.

For all 4,400 trials, a scenario with 100 input tokens per call, one final-answer
token, 500-2,000 reasoning tokens per thinking call, and no retries costs roughly
**2-8 USD**. This is a scenario calculation, not a measured prediction. Reasoning
length, retries and price changes can raise it. The proposed 20 USD reported-cost
ceiling can stop the experiment early, and in-flight calls can slightly exceed
it. At the full output cap on every call, even without retries, cost can exceed
that ceiling. Publish any resulting shortfall.

| Average reasoning tokens per thinking call | 4,400-trial estimate | With separate 44-trial smoke |
| ---: | ---: | ---: |
| 500 | $2.05 | $2.07 |
| 1,000 | $3.88 | $3.91 |
| 2,000 | $7.53 | $7.61 |
| 4,095, reaching the output cap with a one-token answer | $15.19 | $15.34 |

These scenarios assume that non-thinking calls produce one output token and
there are no retries. The last row is not an upper bound on the bill: invalid
verbose non-thinking responses and failed attempts may also consume tokens.

Estimate duration from the 44-call smoke's observed latency and exclusions.
Do not transfer the much shorter GPT-4o runtime to these reasoning models.

## Results and interpretation

The generated `RESULTS.md` will retain model, release period, mode, temperature,
prompt, upstream and precision. Numbers 1 through 10 stay fixed as columns.
Each row shows counts, accepted/planned n and pointwise 95% Wilson intervals.
Keep independent runs separate and include every excluded outcome category.

Primary measure: TV distance from uniform. Secondary descriptive measures:
entropy `H = -sum(p * log2(p))`, with uniform entropy `log2(10)`, and modal-answer
share `max(p)`. Report the selected modal number. At n=100, empirical TV and
modal share have upward sampling bias and empirical entropy has downward bias;
compare like-sized cells and show a simulated uniform reference if plotting
these summaries. Do not attach a degenerate bootstrap CI to all-identical data.

Produce three views:

1. Release history within the matched SiliconFlow FP8 subset, with separate
   prompt/temperature/mode series and December V3 shown as a distinct anchor.
2. Thinking-on versus thinking-off for each of the four hybrid releases, at the
   same temperature and prompt. Display changes in TV and the full histograms.
3. The ten-bin distribution table with Wilson intervals, response validity and
   operational exclusions. Intervals concern accepted answers and assume stable,
   independent trials; they are not simultaneous bands across all 44 cells.

An increase across newer releases supports a historical association. An on/off
difference supports an inference-mode effect. Neither establishes that RL alone
caused the change. A pattern confined to one temperature or provider, or one
that reverses across releases, weakens a simple general claim. The current Grok
grid already supplies a reason to preserve contrary results.

If this pilot is useful, repeat the same full grid independently before selecting
a few dramatic examples for a larger study. The stronger causal follow-up uses
matched base/SFT/pre-RL/post-RL checkpoints on one inference stack, separately
from this release-history experiment.

## Evidence for the proposal

Availability, tags, pricing and precision were checked against OpenRouter's
public model endpoint API on 2026-11-01. The saved endpoint snapshot beside this
proposal records the proposed routes and advertised parameters. A catalog
listing does not establish live account access or parameter behavior.

- [V3-0324 endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-chat-v3-0324/endpoints)
- [R1-0528 endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-r1-0528/endpoints)
- [V3.1 hybrid modes](https://openrouter.ai/deepseek/deepseek-chat-v3.1)
- [V3.2 endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-v3.2/endpoints)
- [V4 Pro 0813 endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-pro-0813/endpoints)
- [V4.1 Flash 1017](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017)
- [Original V3 endpoints](https://openrouter.ai/api/v1/models/deepseek/deepseek-chat/endpoints)
- [Provider routing and fallbacks](https://openrouter.ai/docs/guides/routing/provider-selection)

At proposal time, no DeepSeek inference had been launched. The execution record below documents the subsequently authorized smoke and measured runs.

## Execution record

The authorized 44-trial smoke completed on 2026-11-01 in 177.9 seconds. All
requests passed the frozen routing/decoding/mode checks. It produced 40 valid
answers and four empty final responses, all from V3.1 thinking-on cells. Those
are terminal invalid observations, retained separately; they are not replaced
or used as numbers. Reported OpenRouter usage cost was $0.01233.
[Smoke evidence](../../probes/2026-11-01-deepseek-history-smoke-001/manifest.json).

The initial 4,400-trial measured run was then frozen and launched as
`2026-11-01-deepseek-history-001`, with four concurrent calls, a 180-second
attempt timeout, 24-hour active-run ceiling and $20 reported-cost ceiling.
The smoke is not pooled into the measured result. The full grid retains
V3.1 thinking-on to measure its validity as well as any accepted distribution.
See [the fixed-decoding reproduction steps](../../GRIDS.md#deepseek-history-with-fixed-decoding).


### Concurrency revision requested during execution

On 2026-11-01, Bryan requested higher parallelism for the paid API. The original
four-concurrency run was stopped and preserved as a separate execution pilot:
[archived evidence](../../probes/2026-11-01-deepseek-history-c4-pilot/manifest.json).
It contains 222 terminal logical trials (202 valid, 20 invalid), plus two timeout
attempts and four requests interrupted during shutdown. All 228 physical
attempts remain available. Its frozen status is `paused`; it is superseded and
must not be resumed alongside the replacement. Its reported cost was $0.06998,
with missing costs on six attempts.

A new independent full grid, `2026-11-01-deepseek-history-c16-001`, was frozen
with **16 concurrent calls** and the same seven models, 22 model/mode/temperature
cells, two prompts, 100 trials per prompt/cell, pinned providers, 180-second
attempt timeout and bounded adaptive retries. The earlier pilot is not pooled
into the new result, and its invalid answers are not replaced within that pilot.
The operational change was requested to improve runtime, not selected using
number frequencies.

The new reported-cost ceiling is $19.90, reserving $0.10 for the approximately
$0.0823 already reported across the smoke and superseded run. Missing usage and
in-flight overshoot remain limitations of reported-cost enforcement. All model
settings and evidence snapshots remain immutable. Only the new grid is scheduled
for the measured `RESULTS.md` export; the pilot stays in `PROBES.md`.

```sh
uv run grid.py plan --spec grids/deepseek-2026-11-01-full.json --count 100 \
  --out runs/2026-11-01-deepseek-history-c16-001 --concurrency 16 \
  --timeout 180 --max-seconds 86400 --max-cost 19.9
uv run grid.py run runs/2026-11-01-deepseek-history-c16-001
```

## Post-run scope change

Bryan requested that V3.1 be dropped after reviewing its empty-output failures.
Future `grids/deepseek.json` runs exclude both V3.1 modes: six releases,
18 model/mode/temperature cells and 3,600 trials at 100 per prompt. The original
profile remains `grids/deepseek-2026-11-01-full.json`. Published evidence, counts
and figures for the completed run remain unchanged; V3.1 is excluded from the
substantive model comparison and will not be retried.

## Observed model and mode pattern

Excluding V3.1, thinking-on had higher empirical TV from uniform than thinking-off
in all 12 matched comparisons: three hybrid models, two prompts and two
temperatures. These are point estimates from one round, with 100 planned trials
per cell, not twelve independent studies or a significance test.

| Model | Thinking off: TV range | Thinking on: TV range |
| --- | ---: | ---: |
| V3.2 | 0.267-0.320 | 0.400-0.490 |
| V4 Pro | 0.600-0.690 | 0.890 |
| V4.1 Flash | 0.630-0.730 | 0.860-0.900 |

Ranges span the two prompts and two temperatures; cells are not pooled. TV=0
is an exactly uniform histogram and TV=0.90 is all answers in one bin. V4 Pro
thinking-on returned 7 in 99/100 trials in each of its four cells. V4.1 Flash
thinking-on returned 7 in 96-100/100 trials. The higher temperature and sampling
meta-prompt did not remove that concentration in these settings.

The historical pattern is not monotonic. R1-0528, which requires thinking, had
TV 0.320-0.578, compared with 0.590-0.690 for the non-thinking March V3 release
on the same named upstream/precision. This is a cross-release comparison with
other changes, but it contradicts a universal claim that reasoning models must
give more concentrated number answers. Original V3 was already highly
concentrated and uses a different serving stratum, so it is a weaker history
comparison.

The evidence supports an association between the requested thinking mode and
number concentration within the tested hybrid models. It does not isolate RL
training, establish a general release trend, or measure semantic diversity of
analytics hypotheses. The frozen [summary](../../results/2026-11-01-deepseek-history-c16-001/summary.json)
and [per-bin intervals](../../RESULTS.md) remain the quantitative source.
