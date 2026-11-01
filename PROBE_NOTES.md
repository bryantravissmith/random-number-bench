# Reasoning-effort probe, 2026-11-01

On Claude Opus 5.5 through the subscription-backed Claude CLI, `max` changed the
observed answers sharply. Medium and xhigh were almost entirely `7`; max
returned only `3`, `4` and `6`. The max distributions remain far from uniform.
These are exploratory results from 50 planned trials per prompt and setting.

The [generated report](PROBES.md) contains all ten answer counts, denominators,
excluded attempts and links to immutable evidence. These rounds are separate
from the [1,000-trial benchmark](RESULTS.md).

## Design

- Model: `claude-opus-5-5`, Claude Code 2.1.296, existing first-party subscription OAuth.
- Three requested effort levels: medium, xhigh and max. Each level has 50 baseline and 50 sampling meta-prompt trials using the unchanged [canonical prompts](protocol.json).
- Fresh process and conversation for every attempt; neutral working directory `/`; system preamble `You are a helpful assistant.`; tools disabled; no temperature or model-sampling seed override.
- Baseline/meta pairs were randomized within each effort block, under one shared concurrency controller capped at four. Effort levels ran in separate time blocks in the order medium, xhigh, max. They were not randomized against one another.
- Each round froze its source, prompts, schedule and limits before inference: 180-second timeout, three physical attempts per trial, two-hour active-run ceiling and 10 USD metadata-cost ceiling. Invalid answers were never replaced.

## Observations

| Effort | Baseline accepted | Meta accepted | Baseline TV to uniform | Meta TV to uniform | Calls reporting thinking / all attempts | Reported thinking tokens, all attempts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| medium | 50/50 | 49/50 | 0.900 | 0.900 | 0/100 | 0 |
| xhigh | 50/50 | 50/50 | 0.900 | 0.880 | 1/100 | 30 |
| max, round 002 | 49/50 | 50/50 | 0.718 | 0.760 | 100/100 | 9,380 |

Max baseline counts were ten threes, 35 fours and four sixes. Max meta counts
were twelve threes, 36 fours and two sixes. Neither prompt produced an accepted
seven at max. The empirical TV distance between medium and max is 1.0 for
each prompt: their observed answer categories do not overlap.

Reported thinking tokens come from terminal CLI usage, including excluded
attempts. Progress estimates are not token accounting. The CLI launch option
and reported usage are observed; the full backend request and immutable model
snapshot are not independently established.

This is a strong descriptive shift in this probe, not proof of a general
effect across runs or models. The sample is small, effort blocks were sequential,
and the max runner snapshot includes a parser correction described below.
A replication should randomize effort levels as well as prompts and use more
trials. Greater spread in the histogram does not establish independent or
uniform random sampling.

## Exclusions and parser correction

Medium had one invalid meta response, retained and excluded. Xhigh had no
excluded attempts.

The first max round stopped after four attempts because Claude emitted
`system/thinking_tokens` progress events that the parser had not encountered.
All four remain excluded in the immutable `max-001-stopped` export. A regression
test reproduced the failure. The corrected parser validates these numeric
progress events without treating them as answers or final usage, while still
rejecting unknown events. A new round was frozen and run; the stopped round
was not edited or pooled into it.

Max round 002 attempted all 100 planned trials. One baseline attempt exposed a
CLI-internal `api_retry` event and was excluded even though its eventual text
was an integer. Its final controller state is `stopped` because that event was
classified as a protocol error; all planned trials were already launched and
finished. This is 49 accepted baseline observations, not 50. No replacement
trial was added. Native retries are not always visible, but observed retries
cannot contribute a number.

The max correction also includes the new thinking-off profile and guards
against four inherited thinking-control environment variables. Those variables
were confirmed absent before the original plans; the prompt, model, CLI version
and launch options for the Opus trials otherwise stayed the same apart from
the requested effort.

## A thinking-off control is available

The subscription CLI successfully ran `claude-haiku-4-5-20251001` with thinking
explicitly disabled. Two separate engineering smoke calls, one per canonical
prompt, both returned `7` with zero reported thinking tokens. They passed
evidence verification and remain local, outside these probe statistics.
Haiku is still a thinking-capable model; this profile provides a thinking-off
comparison. See [CLI setup and model limitations](CLI_RUNNERS.md#non-reasoning-comparisons).

## Repeat a probe

Choose a new ID and set `--thinking medium`, `xhigh` or `max` before planning:

```sh
uv run bench.py plan --runner claude --model claude-opus-5-5 \
  --thinking max --count 50 --concurrency 4 --timeout 180 \
  --out runs/opus-max-probe-next
uv run bench.py run runs/opus-max-probe-next
uv run bench.py export runs/opus-max-probe-next --out probes/opus-max-probe-next
uv run bench.py report --probes
```

The report generator and CI verify both `results/` and `probes/`. Published
evidence and generated reports must be rebuilt through the exporter, never
edited by hand.
