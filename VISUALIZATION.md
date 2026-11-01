# A consistent view of random-number distributions

Use two aligned probability heatmaps, one per prompt, with answers 1-10 fixed
as columns and model/settings as rows. Put a TV dot beside each row and show
the selected setting's ten probabilities with pointwise 95% Wilson intervals.
This format scales to many models without changing what color or position means.

## Read the same layout everywhere

| Element | Fixed meaning |
| --- | --- |
| Columns 1-10 | The answer categories, including observed zero counts |
| Heatmap | Share among accepted answers; linear cividis scale from 0 to 100% |
| Baseline / meta facets | The same two protocol prompts, always separate |
| Row | One immutable run, model, mode, requested effort and temperature |
| TV position | Distance from uniform on a fixed 0-0.90 axis; point estimate |
| Detail dots and whiskers | Fixed-bin probabilities and pointwise 95% Wilson intervals |
| Denominators | Accepted / planned logical trials; exclusions shown separately |

Group by immutable run and serving context first. Within each context, order
by developer, named model class, version, requested effort and temperature.
The display registry [visualization-models.json](visualization-models.json)
records named classes such as Opus, Sonnet, Sol and Terra. These are display
groups from requested model names, not measured parameter counts or a claim
about training. Unknown models remain unclassified until the registry is
reviewed. The frozen result package remains the authority for experiment settings.

Filter to one class to compare versions across effort levels. Keep the same
prompt, temperature, upstream and precision when interpreting a trend. Do not
average effort settings or developers, sort into a global leaderboard, or treat
"high" as equal compute across developers. Keep independent repeated rounds
separate. Default decoding is unknown, not temperature zero or one.

The original DeepSeek V3 FP4/DeepInfra anchor gets a different serving block
from the six SiliconFlow FP8 releases. A history plot must preserve that break.
Thinking disabled on an RL-trained model does not undo its training, so mode
contrasts and historical trends do not isolate the causal effect of RL.

## Generate an interactive explorer

```sh
uv run visualize.py \
  results/2026-11-01-grid-pi-001 \
  results/2026-11-01-grid-grok-001 \
  --out .lavish/distribution-explorer.html
```

Open the HTML directly or use `lavish-axi .lavish/distribution-explorer.html`
for collaborative review. It has developer, class, version, effort and
temperature filters, linked detail plots, exact interval tables, frozen
parameters and filtered CSV download. It works offline with no external
JavaScript. Optional local project fonts are embedded when available; otherwise
portable font fallbacks are used. No model calls are made.

The generator verifies each public evidence package before loading it and
accepts only completed v1/v2 grids. Supply result directories explicitly.
Smoke checks, exploratory probes and historical separate-batch rounds should
have separate views. A completed smoke is still a validation experiment,
not a measured grid. Never add live partial results to the scientific comparison.

## Generate scientific figures

```sh
uv run plot_results.py \
  results/2026-11-01-grid-pi-001 \
  results/2026-11-01-grid-grok-001 \
  --out-dir figures/my-comparison --rows-per-page 16 \
  --detail-cell openai--gpt-4o--off
```

Use a new output directory for each export; existing figures are not overwritten.
[Example figures](figures/2026-11-01-completed-grids) include their reproduction
metadata and source hashes in `plot_manifest.json`.

`plot_results.py` writes paginated PNG and SVG figures from verified grids.
Each page retains run and serving context. It also writes a selected-cell
interval figure and a provenance file. See its `--help` for explicit paths,
page size and detail selection. The static figure and interactive explorer
share answer order, fixed scales, prompt identities and denominator rules.

## Uncertainty and missingness

At 0/100, a 95% Wilson interval is approximately [0%, 3.70%]; at 100/100 it is
[96.30%, 100%]. Equal-looking histograms at n=100 and n=1,000 have different
precision. Show intervals visibly in the selected-row panel, not only on hover.

These intervals are conditional on accepted answers and assume stable,
independent trials. They are pointwise for fixed answer categories, not
simultaneous bands for every bin or row. Overlap is not an equivalence test.
A fixed-bin Wilson interval is not automatically valid for a data-selected
modal share. TV currently has no confidence interval. Do not derive one by
combining the bin intervals or use a degenerate empirical bootstrap for an
all-identical sample.

Observed zero and no data are different: zero counts get the ordinary zero
color and a nonzero upper interval; n=0 gets hatched n/a cells and no TV point.
Unsupported, not run, running and stopped states need distinct status labels.
Generate such placeholders only from documented design/support information,
not an imagined Cartesian product of every possible model and setting.

Physical operational exclusions can be retried and later produce accepted
logical trials. Never stack them against accepted/planned as mutually exclusive
outcomes. Keep invalid completed responses separate from transport failures
and show truncation counts from v2 summaries when reviewing validity.

## Finite uniform samples also have nonzero TV

For ten categories and accepted sample size n, the expected empirical TV under
uniform sampling is

`5 * sum_k abs(k/n - 0.1) * BinomialPMF(k; n, 0.1)`.

It is approximately 0.11868 at n=100 and 0.03781 at n=1,000. A 200,000-draw
multinomial simulation gives central 95% reference ranges of approximately
0.06-0.18 and 0.021-0.058 respectively. Static figures show a matched-n reference
span, with seed and simulation settings recorded in plot provenance. This is
a uniform-sampling reference, not a confidence interval for a model score, a
familywise test or a pass/fail threshold. Do not subtract it and call TV
"bias corrected."

## Why this representation

Precise comparisons use aligned position, while the heatmap gives a dense
pattern overview. This design choice follows the evidence on graphical
perception; the particular combined layout is our recommendation for this
benchmark, not a layout validated by that study.
[Heer and Bostock, CHI 2010](https://hci.stanford.edu/publications/2010/crowd-perception/heer-chi2010.pdf).

Cividis is a sequential colormap designed for perceptual uniformity and
red-green color-vision deficiencies. Numeric labels and marker shapes ensure
color is not the only signal. Keep a single normalization across all rows.
[Nuñez, Anderton and Renslow, 2018](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199239),
[Matplotlib colormap guidance](https://matplotlib.org/stable/users/explain/colors/colormaps.html),
[W3C use of color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).

Wilson score intervals remain meaningful at the boundaries, unlike naive
symmetric normal intervals. The repository already exports the endpoints;
visualizations consume those verified values.
[NIST proportion intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

Avoid pie charts, radar charts, ten-color stacked answer bars, per-row rescaling
and lines joining the answer bins. For a focused release or effort comparison,
use small multiples with the same probability axes and explicit serving breaks.
