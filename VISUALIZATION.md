# One matrix for family, version and effort

The primary presentation is a single [comparison matrix](reports/comparison.html).
Download and open the HTML in a browser. It shows every included model together:
no filters or view switching are required to compare the three dimensions.

| Position | Meaning |
| --- | --- |
| Row groups | Developer and separately recorded serving context / immutable run |
| Left labels | Named family, requested model/version, temperature |
| Columns | Requested native effort, or explicitly separate Off / On profile modes |
| Paired values | Baseline first (B), sampling meta-prompt second (M) |
| Color and number | Observed effective answer count, on a fixed 1-10 scale |
| Click a value | Ten-bin probabilities, pointwise 95% Wilson intervals, TV and provenance |

Read across for an effort contrast within a model; read down for model/version
contrasts. Every comparison retains its actual setting and sample. The diagram
never pools runs, temperatures, models or prompts and never sorts by outcome.
DeepSeek's pinned On/required profile is separate from native CLI High. Effort
labels do not mean equal compute across providers, and disabling thinking does
not undo reasoning training. Family labels are display groups from requested
names, not claims of common ancestry, size or architecture. V4 Pro and Flash
remain explicitly named as different products.

The original DeepSeek V3 FP4/DeepInfra anchor has its own labeled serving block;
the later SiliconFlow FP8 cells form another. Default temperature is unknown.
Requested model IDs and accepted returned aliases are available in each cell's
frozen-parameter detail; neither establishes immutable backend weights.

## What the colors measure

For a cell and prompt with accepted count n, let `p_i = count_i / n` across the
ten fixed answer categories. Shannon entropy is `H = -sum(p_i * log2(p_i))`,
ignoring zero terms. **Effective answer count is `2^H`.** One means all accepted
answers were identical; ten means an exactly even empirical histogram. Four
means the same entropy as four equally frequent answers, even if more than four
bins were observed. It is not a count of unique values, semantic alternatives,
reasoning strategies or useful hypotheses.

These are descriptive plug-in point estimates with finite-sample downward bias.
Do not interpret distance from ten as bias-corrected, or derive an entropy CI
from separate Wilson endpoints. The original benchmark's primary statistic, TV,
remains in RESULTS.md and cell detail. Identical entropy can hide different
number distributions, so inspect the fixed-bin counts before interpreting a
particular model preference.

Asterisks mark acceptance shortfalls. Click for accepted/planned, invalid,
operational-attempt and (where recorded) truncation counts. Completed n=0 cells
show hatched n/a; absent completed cells show a dash without claiming unsupported
capability. V3.1 is excluded by default and can be revealed as a labeled archive
at the end. Its original evidence is unchanged. The completed Codex grid is
included alongside Claude, Grok, GPT-4o and DeepSeek.

## Reproduce the checked-in presentation

```sh
uv run visualize.py --spec reports/comparison.json --out reports/comparison.html
uv run visualize.py --spec reports/comparison.json --out reports/comparison.html --check
```

The scope file enumerates the exact public exports and exclusions. The generator
verifies each package before loading it and accepts only completed v1/v2 grids.
The HTML and `comparison-manifest.json` are generated artifacts; edit the
scope, registry, `visualize.py`, `visualization.html` or `visualization.js`, then
rebuild. The sidecar records input and source hashes. CI checks for staleness.
No model calls are made. The HTML includes its data and script and works offline.
It uses the existing Agents & Insights paper/forest-green style with portable
system and serif fonts; `--embed-fonts` optionally embeds local brand fonts.

For another completed round, add its export to a new scope or pass explicit
result directories. Repeated runs remain separate blocks even for identical
model settings. Separate historical batches remain in the recorded results.
Only completed measured grids belong in this matrix.

For local collaborative review, run `lavish-axi reports/comparison.html`.
The CSV download contains every displayed cell's exact counts, CIs, entropy,
TV, denominators, settings and exclusion label. It does not contain raw logs.

## Refresh the README and results image

Both documents embed [comparison.png](reports/comparison.png), a high-resolution
capture of this same matrix. Refresh the HTML first, then capture it headlessly:

```sh
# One-time browser installation; skip if using an installed Chrome below.
uv run --python 3.12 --with playwright==1.64.1 python -m playwright install chromium
uv run snapshot_matrix.py
# Alternatively: uv run snapshot_matrix.py --browser chrome
uv run --python 3.12 bench.py report
```

The capture uses a 1,600-pixel desktop viewport at 2x resolution, includes all
rows, and omits interactive controls. It makes no network requests or model
calls. `comparison-image.json` records source/image hashes and renderer versions.
CI runs `python snapshot_matrix.py --check` to reject a missing, modified or
stale image without installing a browser. Pixel rendering can differ with the
browser version and system fonts; this checks provenance, not pixel equality
across operating systems. Commit the PNG and its sidecar whenever the matrix
changes. RESULTS.md gets the image through `public_results.py`; never edit the
generated report manually.

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
page size and detail selection. The static heatmaps retain the same prompt identities, answer order and
denominator rules as the matrix detail. Their probability colors encode a
different quantity from the primary matrix's effective-answer-count colors.

## Uncertainty and missingness

At 0/100, a 95% Wilson interval is approximately [0%, 3.70%]; at 100/100 it is
[96.30%, 100%]. Equal-looking histograms at n=100 and n=1,000 have different
precision. Show intervals visibly in the selected-row panel, not only on hover.

These intervals are conditional on accepted answers and assume stable,
independent trials. They are pointwise for fixed answer categories, not
simultaneous bands for every bin or row. Overlap is not an equivalence test.
A fixed-bin Wilson interval is not automatically valid for a data-selected
modal share. TV and effective answer count currently have no confidence intervals. Do not derive one by
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
