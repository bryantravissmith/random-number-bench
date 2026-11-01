# Did reasoning training concentrate random-number answers?

Study proposed on 2026-11-01. This remains an exploratory hypothesis and
candidate registry. The first DeepSeek grid completed on the same date;
native CLI grids retain their original frozen protocols and provider defaults.

Selected first family: **DeepSeek**. See the [full seven-release grid
proposal](DEEPSEEK_PROPOSAL.md), covering 4,400 planned trials with two prompts
and two temperatures. The [verified export](../../results/2026-11-01-deepseek-history-c16-001)
contains 3,993 accepted answers, 407 invalid final responses and 27 timeout
attempts. All 4,400 logical trials finished. Four hundred invalid responses
were empty V3.1 thinking outputs; no distribution is estimated for those cells.
The historical proposal below its status note is retained as provenance.

## Hypothesis

Reasoning-oriented post-training may increase concentration of responses to a
request for a random integer. The directional prediction is higher distance
from uniform, higher probability of the most common answer, and lower answer
entropy after reasoning-oriented training. The preferred number need not be 7.

The hypothesis arose after seeing the initial benchmark results. Label this
study exploratory; do not describe it as preregistered before all observations.
Freeze its candidate list, settings and analysis before collecting new samples.

There is relevant prior evidence for the proposed mechanism:
[The Entropy Mechanism of Reinforcement Learning for Reasoning Language Models](https://arxiv.org/abs/2505.22617)
reports policy-entropy collapse during reasoning RL without entropy
interventions. This motivates the hypothesis; token/policy entropy during
training is not the same quantity as entropy of ten final answer categories.
Earlier work, [Deterministic or probabilistic? The psychology of LLMs as random
number generators](https://arxiv.org/abs/2502.19965), already reports random-number
bias across models, ranges, temperatures and languages. Our contribution should
be a reproducible version/mode comparison, rather than claiming discovery that
LLMs can be biased number generators.

## Three distinct comparisons

| Comparison | What it can establish | Main limitation |
| --- | --- | --- |
| Older to newer releases within a family | Whether concentration changes with release history | Training data, size, architecture, alignment and serving all change |
| Thinking off/on on the same checkpoint | Effect of requesting explicit reasoning at inference | Both modes retain the same post-trained weights; it does not undo RL |
| Base, SFT, reasoning-RL checkpoints from one lineage | A closer test of the training-stage hypothesis | Exact checkpoints and matched serving are required; intermediate checkpoints may be unavailable |

Do not label older instruction models as "pre-RL" without training evidence.
[DeepSeek V3](https://github.com/deepseek-ai/DeepSeek-V3) already used SFT and RL.
[Qwen3's training description](https://qwenlm.github.io/blog/qwen3/) includes
reasoning RL, thinking-mode fusion and general RL. Turning off its explicit
thinking leaves those training stages in place.

The strongest available training lineage to investigate is DeepSeek-V3-Base
to R1-Zero, with R1 as an additional post-training comparison.
[R1-Zero used RL without a preceding SFT stage; R1 added cold-start data and
further stages](https://github.com/deepseek-ai/DeepSeek-R1). These checkpoints
are research targets, not assumed to be served by OpenRouter. Distilled R1
models must be labeled distillation rather than direct RL experiments.

## Candidate history as of 2026-11-01

All IDs below were present in OpenRouter's public catalog when checked.
Presence is not a successful inference check. Each row still requires a pinned
upstream endpoint and audited smoke calls. The dated catalog snapshot is
retained beside this plan; catalog creation dates are not original release
dates. Follow primary release announcements for the historical x-axis.

| Family / stage | Candidate OpenRouter ID | Comparison role |
| --- | --- | --- |
| Qwen2.5 | `qwen/qwen-2.5-7b-instruct`, `qwen/qwen-2.5-72b-instruct` | Earlier non-thinking instruction models; size differs from later candidates |
| Qwen3 | `qwen/qwen3-32b` | Same-checkpoint thinking off/on |
| Qwen3 July 2025 | `qwen/qwen3-235b-a22b-2507`, `qwen/qwen3-235b-a22b-thinking-2507` | Separately trained Instruct and Thinking releases; not a mode toggle |
| Qwen3.5 | `qwen/qwen3.5-27b` | Intermediate dense generation, off/on where endpoint supports it |
| Qwen3.6 | `qwen/qwen3.6-27b` | Intermediate dense generation, off/on where supported |
| Qwen3.8 | `qwen/qwen3.8-27b` | Current dense generation, off/on |
| Qwen3.8 large | `qwen/qwen3.8-2.4t-a95b` | Current open-weight flagship reference; thinking required |
| DeepSeek V3 | `deepseek/deepseek-chat`, `deepseek/deepseek-chat-v3-0324` | Earlier non-thinking chat releases; verify what the undated alias serves |
| DeepSeek R1 May 2025 | `deepseek/deepseek-r1-0528` | Reasoning-trained lineage; thinking required |
| DeepSeek V3.1 | `deepseek/deepseek-chat-v3.1` | Hybrid off/on comparison |
| DeepSeek V3.2 | `deepseek/deepseek-v3.2` | Later hybrid off/on comparison |
| DeepSeek V4 Pro | `deepseek/deepseek-v4-pro-0813` | Recent large-model reference, off/on where supported |
| DeepSeek V4.1 Flash | `deepseek/deepseek-v4.1-flash-1017` | Recent dated reference, off/on where supported |

Qwen2.5 was announced on [2024-09-19](https://qwenlm.github.io/blog/qwen2.5/),
and Qwen3 on [2025-04-29](https://qwenlm.github.io/blog/qwen3/). The
[Qwen3.8 27B model card](https://huggingface.co/Qwen/Qwen3.8-27B) documents both
modes. The [2.4T model card](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B)
requires thinking and carries its own model license. "Open-weight" does not
mean every model shares the same open-source license.

The [DeepSeek V4.1 Flash 1017 listing](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017)
identifies an October 17, 2026 snapshot. Here "current flagship" means a recent
family reference, not a claim that one model wins every state-of-the-art metric.

Seek missing matched-size older models and original R1 checkpoints explicitly.
Do not replace a missing historical model with a newer alias and retain the
old date. Record unavailable models as gaps in the timeline.

## Execution controls

Start with 100 logical trials per prompt and setting, using the same two
canonical prompts and bare-integer acceptance rule. Before inference, select
the manageable first subset and record the resulting total calls and budget.
Use the existing complete randomized blocks and shared adaptive controller.
Keep invalid responses and operational failures visible; never replace them
to reach 100 accepted answers.

For the historical study, add a separately versioned fixed-decoding profile.
Audit temperature, top-p, other sampling filters and penalties, output budget,
thinking request and upstream routing. Compare off/on using identical sampling
values wherever supported. A vendor-recommended profile can be a separate
sensitivity analysis because vendors may recommend different sampling values
for the two modes. For example, Qwen3.8's model card recommends different
temperatures and penalties. Uncontrolled defaults could imitate a mode effect.

Pin one upstream per matched comparison, disable provider fallback and require
support for the requested parameters. Use one runner, identical system/user
text and fresh sessions. Do not add `/think` text to only one prompt. A hidden
reasoning response is not proof that thinking was disabled: audit the request
and record exposed reasoning usage. Record unobservable backend settings as
unknown. The current Pi profile only audits non-reasoning OpenAI upstreams;
extend and validate it before using these candidates.

Use a fresh scheduling seed per independent round. Do not reuse one fixed
generation seed for every trial. Repeat rounds on separate occasions and keep
them visible to assess serving drift. Never pool native CLI results into the
OpenRouter historical analysis as if only the model had changed.

## Analysis and model metadata

For each model/mode/prompt retain the ten-bin histogram, accepted/planned n,
exclusions, pointwise Wilson intervals and TV from uniform. Add entropy and
modal-answer share as descriptive secondary measures, specifying formulas and
finite-sample limitations before collection. Plot release date within family,
with separate thinking-mode series and explicit gaps. Also plot the within-model
off/on change. Keep model size, architecture and training category visible.

Treat higher TV in newer models as a trend, not proof that RL caused it. Stronger
support would require a consistent change across matched training checkpoints,
stable under decoding controls and repeated rounds. A mode effect without a
training-checkpoint effect supports an inference mechanism instead. No increase,
or an effect that disappears under matched decoding, weakens the hypothesis.

For each candidate record: exact requested ID, observed response ID, original
release date and source, catalog capture date, weights/checkpoint link, family,
total/active parameter counts, architecture, training-stage evidence, mode,
provider, quantization when disclosed, decoding settings and availability.
Unknown values remain null. Store interpretations separately from source facts.

## Reproduce the availability snapshot

This command performs a public metadata request, with no credentials or model
inference, and refuses to overwrite an existing snapshot:

```sh
uv run catalog.py --family qwen --family deepseek \
  --out studies/reasoning-history/catalog-YYYY-MM-DD.json
```

The snapshot includes model IDs, weight identifiers, advertised reasoning
controls, supported parameters, default settings and pricing. It is a discovery
artifact, not an executable grid or proof of live account access. See the
[OpenRouter catalog reference](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties).
