#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.11.2", "numpy==2.5.4"]
# ///
"""Export scientific plots from explicitly selected, verified public grids.

Run from the benchmark repository, or provide --verifier-root REPOSITORY.
See VISUALIZATION.md in the repository. This program makes no inference calls.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors
from matplotlib.patches import Rectangle
import numpy as np

PUBLIC_SCHEMAS = {"random-number-public-grid-v1", "random-number-public-grid-v2"}
NUMBERS = tuple(str(i) for i in range(1, 11))
PROMPTS = ("baseline", "meta")
STYLES = {"baseline": ("#0072B2", "o", -0.17), "meta": ("#D55E00", "s", 0.17)}
REFERENCE_DRAWS = 200_000
REFERENCE_SEED = 20261101


@dataclass
class Row:
    run: str
    run_token: str
    source: Path
    manifest: dict
    cell: dict
    summary: dict
    context: dict

    @property
    def selector(self):
        return f"{self.run_token}::{self.cell['id']}"


@dataclass
class Page:
    rows: list[Row]
    context_index: int
    part: int
    parts: int


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def slug(value, limit=60):
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "grid"
    return clean[:limit] + "-" + hashlib.sha256(value.encode()).hexdigest()[:8]


def serving_context(manifest, cell):
    service = manifest["service"]
    common = {
        "runner": service["runner"],
        "runner_version": service.get("runner_version"),
        "provider": service["provider"],
        "evidence_level": service["evidence_level"],
    }
    profile = cell.get("request_profile")
    if profile is None:
        return {
            **common,
            "sampling_regime": "unspecified in v1",
            "routing": service.get("openrouter_routing"),
            "precision": None,
            "stratum": None,
        }
    return {
        **common,
        "sampling_regime": "fixed decoding",
        "routing": profile["provider"],
        "expected_upstream": profile["expected_provider"],
        "precision": profile["quantization"],
        "stratum": profile["stratum"],
        # Temperatures/modes remain distinct rows. Other decoding differences
        # cause separate pages so they cannot disappear in a group heading.
        "decoding": {
            key: profile[key]
            for key in ("top_p", "top_k", "frequency_penalty", "max_tokens")
        },
    }


def context_label(context):
    route = context.get("routing") or {}
    upstream = ", ".join(route.get("only", [])) or "upstream not recorded"
    fields = [
        f"runner {context['runner']} {context.get('runner_version') or '(version unknown)'}",
        f"provider {context['provider']}",
        f"route {upstream}",
        f"precision {context.get('precision') or 'not recorded'}",
        context["sampling_regime"],
    ]
    if context.get("stratum"):
        fields.append(f"stratum {context['stratum']}")
    if route:
        fields.append(f"fallbacks={route.get('allow_fallbacks', 'unknown')}")
    if context.get("decoding"):
        fields.extend(f"{key}={value}" for key, value in context["decoding"].items())
    return " | ".join(fields)


def settings_label(row):
    profile = row.cell.get("request_profile")
    settings = f"requested effort={row.cell['thinking']}"
    if profile is None:
        settings += "; temperature not recorded"
    else:
        settings += f"; mode={profile['mode']}; T={profile['temperature']:g}"
    return settings


def row_label(row):
    model_lines = textwrap.wrap(row.cell["model"], width=39) or [row.cell["model"]]
    return "\n".join(model_lines + textwrap.wrap(settings_label(row), width=45))


def load_rows(paths, verify_public):
    """The verifier is the sole export reader; use its verified return values."""
    rows = []
    seen_paths, seen_runs = set(), set()
    for source in paths:
        source = Path(source).expanduser().resolve()
        if source in seen_paths:
            raise ValueError(f"Duplicate input directory: {source}")
        seen_paths.add(source)
        manifest, summary = verify_public(source)
        if manifest["schema"] not in PUBLIC_SCHEMAS:
            raise ValueError(f"Unsupported grid schema: {manifest['schema']}")
        if manifest["service"]["state"] != "complete":
            raise ValueError(
                f"Only complete grids are allowed: {source.name} is {manifest['service']['state']}"
            )
        identity = manifest["grid"]["batch_manifest_sha256"]
        if identity in seen_runs:
            raise ValueError(
                f"The same immutable run was supplied more than once: {source}"
            )
        seen_runs.add(identity)
        run_token = f"{source.name}@{identity[:8]}"
        # Never iterate summary dictionaries or sort by a score. The frozen
        # manifest determines model/effort/temperature order.
        for cell in manifest["grid"]["cells"]:
            rows.append(
                Row(
                    source.name,
                    run_token,
                    source,
                    manifest,
                    cell,
                    summary["cells"][cell["id"]],
                    serving_context(manifest, cell),
                )
            )
    if not rows:
        raise ValueError("No cells were found in the selected grids")
    return rows


def make_pages(rows, rows_per_page):
    if rows_per_page < 1:
        raise ValueError("--rows-per-page must be a positive integer")
    groups = {}
    for row in rows:
        # A run is never combined with another, even if its visible settings
        # match. Context order and cell order follow first manifest occurrence.
        key = (row.run_token, canonical(row.context))
        groups.setdefault(key, []).append(row)
    pages = []
    for context_index, group in enumerate(groups.values(), start=1):
        parts = (len(group) + rows_per_page - 1) // rows_per_page
        for part in range(parts):
            pages.append(
                Page(
                    group[part * rows_per_page : (part + 1) * rows_per_page],
                    context_index,
                    part + 1,
                    parts,
                )
            )
    return pages


def select_detail(rows, selector=None):
    if selector is None:
        return rows[0]
    matches = [
        r
        for r in rows
        if selector in (r.cell["id"], r.selector, f"{r.run}::{r.cell['id']}")
    ]
    if len(matches) != 1:
        choices = "\n".join(f"  {r.selector}" for r in (matches or rows))
        raise ValueError(
            f"Detail selector must identify exactly one cell: {selector!r}\nAvailable matches:\n{choices}"
        )
    return matches[0]


@lru_cache(maxsize=None)
def uniform_reference(n):
    if n == 0:
        return None
    rng = np.random.default_rng(REFERENCE_SEED + n)
    counts = rng.multinomial(n, [0.1] * 10, size=REFERENCE_DRAWS)
    tv = np.abs(counts / n - 0.1).sum(axis=1) / 2
    return tuple(float(v) for v in np.quantile(tv, [0.025, 0.975]))


def percent_label(p):
    if p == 0:
        return "0"
    if p * 100 < 0.1:
        return "<0.1"
    return f"{100 * p:.1f}".rstrip("0").rstrip(".")


def setup_style():
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def add_header(fig, height, title, row, extra=""):
    fig.text(
        0.035, (height - 0.40) / height, title, fontsize=20, weight="bold", va="top"
    )
    fig.text(
        0.035,
        (height - 0.88) / height,
        f"Run: {row.run_token} | protocol {row.manifest['grid']['protocol']['version']} {extra}",
        fontsize=10,
        va="top",
    )
    fig.text(
        0.035,
        (height - 1.19) / height,
        "\n".join(textwrap.wrap(context_label(row.context), width=172)),
        fontsize=9,
        color="#444444",
        va="top",
        linespacing=1.45,
    )


def save_figure(fig, stem):
    for suffix in (".png", ".svg"):
        fig.savefig(Path(f"{stem}{suffix}"), dpi=180, facecolor="white")
    plt.close(fig)


def render_overview(page, stem):
    rows, nrows = page.rows, len(page.rows)
    # Long model IDs remain readable instead of crossing a neighbor's row.
    label_lines = max(len(row_label(row).splitlines()) for row in rows)
    row_height = max(0.55, 0.15 * label_lines + 0.12)
    top, bottom = 2.20, 2.05
    height = top + bottom + row_height * nrows
    fig = plt.figure(figsize=(20, height), facecolor="white")
    add_header(
        fig,
        height,
        "Random-number distributions - completed grid",
        rows[0],
        f"| context {page.context_index}, page {page.part}/{page.parts}",
    )
    positions = [
        (0.035, 0.205),
        (0.25, 0.17),
        (0.44, 0.17),
        (0.63, 0.165),
        (0.83, 0.13),
    ]
    axes = [
        fig.add_axes([x, bottom / height, width, nrows * row_height / height])
        for x, width in positions
    ]
    labels, baseline, meta, denominators, tv_ax = axes
    for ax in axes:
        ax.set_ylim(nrows - 0.5, -0.5)
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    for ax in (labels, denominators):
        ax.set_xlim(0, 1)
        ax.set_axis_off()
    header_y = (height - top + 0.27) / height
    for x, title in [
        (0.035, "Model / recorded settings"),
        (0.25, "Baseline prompt"),
        (0.44, "Sampling meta-prompt"),
        (0.63, "Accepted / planned"),
        (0.83, "TV from uniform"),
    ]:
        fig.text(x, header_y, title, weight="bold", fontsize=11)
    for x, title in [(0.63, "Baseline"), (0.694, "Meta"), (0.763, "Excl. B/M")]:
        fig.text(x, (height - top + 0.08) / height, title, fontsize=8, color="#555555")
    cmap = matplotlib.colormaps["cividis"].with_extremes(bad="#F4F4F4")
    norm = colors.Normalize(0, 1)
    for prompt, ax in zip(PROMPTS, (baseline, meta)):
        matrix = np.full((nrows, 10), np.nan)
        for y, row in enumerate(rows):
            data = row.summary["conditions"][prompt]
            n = data["accepted_trials"]
            if n:
                matrix[y] = [data["counts"][number] / n for number in NUMBERS]
        ax.imshow(matrix, cmap=cmap, norm=norm, aspect="auto", interpolation="none")
        ax.set_xticks(range(10), NUMBERS)
        ax.tick_params(axis="x", length=0, pad=6)
        ax.set_xlabel("Answer", labelpad=5)
        for y, row in enumerate(rows):
            data = row.summary["conditions"][prompt]
            n = data["accepted_trials"]
            if not n:
                ax.add_patch(
                    Rectangle(
                        (-0.5, y - 0.5),
                        10,
                        1,
                        facecolor="none",
                        edgecolor="#BBBBBB",
                        hatch="///",
                        linewidth=0,
                    )
                )
                ax.text(
                    4.5,
                    y,
                    "n/a - no accepted answers",
                    ha="center",
                    va="center",
                    fontsize=9,
                )
            else:
                for x, number in enumerate(NUMBERS):
                    p = data["counts"][number] / n
                    ax.text(
                        x,
                        y,
                        percent_label(p),
                        ha="center",
                        va="center",
                        fontsize=9,
                        color="white" if p < 0.48 else "#111111",
                    )
    for y, row in enumerate(rows):
        labels.text(0, y, row_label(row), va="center", fontsize=9, linespacing=1.25)
        a, b = [row.summary["conditions"][prompt] for prompt in PROMPTS]
        for x, data in [(0, a), (0.39, b)]:
            denominators.text(
                x,
                y,
                f"{data['accepted_trials']} / {data['planned_trials']}",
                va="center",
                fontsize=9,
            )
        denominators.text(
            0.81,
            y,
            f"{a['excluded_attempts']} / {b['excluded_attempts']}",
            va="center",
            fontsize=9,
        )
        for prompt in PROMPTS:
            color, marker, offset = STYLES[prompt]
            data = row.summary["conditions"][prompt]
            ref = uniform_reference(data["accepted_trials"])
            if ref is None:
                tv_ax.text(
                    0.85,
                    y + offset,
                    "n/a",
                    ha="right",
                    va="center",
                    fontsize=8,
                    color=color,
                )
                continue
            tv_ax.plot(
                ref,
                [y + offset] * 2,
                color="#D4D4D4",
                linewidth=5,
                solid_capstyle="butt",
                zorder=1,
            )
            value = data["tv_to_uniform"]
            tv_ax.scatter(
                value,
                y + offset,
                color=color,
                marker=marker,
                s=25,
                clip_on=False,
                zorder=3,
            )
            tv_ax.annotate(
                f"{value:.3f}",
                (value, y + offset),
                xytext=(-7 if value > 0.7 else 7, 0),
                textcoords="offset points",
                ha="right" if value > 0.7 else "left",
                va="center",
                fontsize=8,
                color=color,
            )
    tv_ax.set_xlim(0, 0.9)
    tv_ax.set_xticks([0, 0.3, 0.6, 0.9], ["0", ".30", ".60", ".90"])
    tv_ax.tick_params(axis="x", length=3, color="#888888")
    tv_ax.set_xlabel("0 = exact uniform histogram", fontsize=9, labelpad=5)
    fig.text(0.25, 1.30 / height, "Share of accepted answers (%)", fontsize=10)
    cax = fig.add_axes([0.25, 1.04 / height, 0.36, 0.12 / height])
    cb = fig.colorbar(
        matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap),
        cax=cax,
        orientation="horizontal",
    )
    cb.set_ticks(
        [0, 0.1, 0.25, 0.5, 0.75, 1],
        labels=["0%", "10% uniform", "25%", "50%", "75%", "100%"],
    )
    cb.ax.tick_params(labelsize=9, length=3)
    cb.outline.set_visible(False)
    notes = [
        "TV: blue circle = baseline; orange square = meta. Point estimates only.",
        "Gray spans: central 95% of uniform-sample TV at each accepted n.",
        "These are null sampling references, not model-score confidence intervals.",
        "Excl. B/M = physical excluded attempts by prompt, separate from logical trials.",
    ]
    for i, note in enumerate(notes):
        fig.text(0.63, (1.30 - i * 0.21) / height, note, fontsize=8.5, color="#444444")
    fig.text(
        0.035,
        0.39 / height,
        "No pooling or score ranking. Counts and proportions concern accepted answers; see the separate Wilson detail and plot_manifest.json.",
        fontsize=9,
        color="#444444",
    )
    fig.text(
        0.035,
        0.18 / height,
        f"Source: {rows[0].run_token} | verified by grid_results.verify_public",
        fontsize=8,
        color="#555555",
    )
    save_figure(fig, stem)


def render_detail(row, stem):
    height = 8.2
    fig = plt.figure(figsize=(16, height), facecolor="white")
    add_header(fig, height, "Selected cell - answer probabilities and uncertainty", row)
    fig.text(
        0.035,
        0.74,
        "\n".join(
            textwrap.wrap(
                f"{row.cell['model']} | {settings_label(row)} | cell {row.cell['id']}",
                155,
            )
        ),
        fontsize=10,
        va="top",
    )
    for prompt, x0 in [("baseline", 0.075), ("meta", 0.555)]:
        ax = fig.add_axes([x0, 0.23, 0.405, 0.41])
        color, marker, _ = STYLES[prompt]
        data = row.summary["conditions"][prompt]
        n = data["accepted_trials"]
        if n:
            p = np.array([data["counts"][number] / n for number in NUMBERS])
            interval = np.array(
                [data["wilson_intervals"][number] for number in NUMBERS]
            )
            ax.errorbar(
                range(1, 11),
                100 * p,
                yerr=100 * np.vstack([p - interval[:, 0], interval[:, 1] - p]),
                fmt=marker,
                color=color,
                markersize=5,
                elinewidth=1.5,
                capsize=3,
                linewidth=0,
                clip_on=False,
            )
        else:
            ax.text(
                0.5,
                0.5,
                "n/a - no accepted answers",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=14,
            )
        ax.axhline(10, color="#555555", linestyle=(0, (4, 3)), linewidth=1)
        ax.set(
            xlim=(0.6, 10.4),
            ylim=(0, 100),
            xticks=range(1, 11),
            xlabel="Answer",
            ylabel="Share of accepted answers",
        )
        ax.set_yticks(
            [0, 10, 25, 50, 75, 100], ["0%", "10%", "25%", "50%", "75%", "100%"]
        )
        tv = "n/a" if data["tv_to_uniform"] is None else f"{data['tv_to_uniform']:.3f}"
        ax.set_title(
            f"{prompt.title()} | accepted {n}/{data['planned_trials']} | TV {tv}",
            loc="left",
            fontsize=11,
        )
        ax.grid(axis="y", color="#E9E9E9", linewidth=0.7)
        ax.set_axisbelow(True)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#BBBBBB")
        exclusion = (
            f"Physical exclusions: {data['excluded_attempts']} "
            f"(invalid {data['invalid_response_attempts']}; operational {data['operational_exclusions']})"
        )
        fig.text(x0, 0.145, exclusion, fontsize=9)
    fig.text(
        0.035,
        0.098,
        "Whiskers: pointwise 95% Wilson intervals among accepted answers. Dashed line: 10% uniform probability.",
        fontsize=10,
    )
    fig.text(
        0.035,
        0.069,
        "Assumes stable, independent trials. Intervals are not simultaneous and are not TV intervals. Zero counts retain uncertainty.",
        fontsize=9,
        color="#444444",
    )
    fig.text(
        0.035,
        0.035,
        f"Source: {row.run_token} | verified by grid_results.verify_public",
        fontsize=8,
        color="#555555",
    )
    save_figure(fig, stem)


def load_verifier(root):
    root = Path(root).expanduser().resolve()
    if not (root / "grid_results.py").is_file():
        raise ValueError(
            "Cannot find grid_results.py; run from the benchmark repository or set --verifier-root"
        )
    sys.path.insert(0, str(root))
    verifier = importlib.import_module("grid_results")
    if Path(verifier.__file__).resolve() != root / "grid_results.py":
        raise ValueError("A different grid_results module is already imported")
    return verifier


def output_plan(pages, selected, out_dir):
    overview = [
        (
            page,
            out_dir
            / f"overview-{page.context_index:02d}-{slug(page.rows[0].run_token)}-p{page.part:03d}",
        )
        for page in pages
    ]
    detail = (
        out_dir
        / f"detail-{slug(selected.run_token, 35)}-{slug(selected.cell['id'], 70)}"
    )
    return overview, detail


def export_manifest(rows, overview, detail_stem, selected, verifier):
    sources = {}
    for row in rows:
        sources.setdefault(
            row.run_token,
            {
                "directory": row.source.parent.name + "/" + row.source.name,
                "schema": row.manifest["schema"],
                "batch_manifest_sha256": row.manifest["grid"]["batch_manifest_sha256"],
                "summary_sha256": row.manifest["files_sha256"]["summary.json"],
                "protocol": row.manifest["grid"]["protocol"],
                "state": "complete",
            },
        )
    ns = sorted(
        {r.summary["conditions"][p]["accepted_trials"] for r in rows for p in PROMPTS}
    )
    return {
        "schema": "random-number-scientific-plot-export-v1",
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "versions": {
            "python": sys.version,
            "matplotlib": matplotlib.__version__,
            "numpy": np.__version__,
        },
        "verifier": {
            "path": Path(verifier.__file__).name,
            "sha256": hashlib.sha256(Path(verifier.__file__).read_bytes()).hexdigest(),
        },
        "axes": {
            "answer_order": list(NUMBERS),
            "probability_domain": [0, 1],
            "tv_domain": [0, 0.9],
        },
        "uniform_reference": {
            "method": "multinomial uniform, central 95% sampling range; not model CI",
            "draws_per_n": REFERENCE_DRAWS,
            "rng": "numpy.random.default_rng",
            "seed": "20261101 + accepted_n",
            "ranges_by_n": {str(n): uniform_reference(n) for n in ns},
        },
        "sources": sources,
        "overview_pages": [
            {
                "stem": stem.name,
                "cells": [r.selector for r in page.rows],
                "context": page.rows[0].context,
            }
            for page, stem in overview
        ],
        "detail": {"stem": detail_stem.name, "cell": selected.selector},
        "cells": [
            {
                "selector": r.selector,
                "requested_model": r.cell["model"],
                "requested_effort": r.cell["thinking"],
                "request_profile": r.cell.get("request_profile"),
                "context": r.context,
                "conditions": r.summary["conditions"],
            }
            for r in rows
        ],
    }


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "exports", nargs="+", type=Path, help="Explicit public grid export directories"
    )
    result.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Output directory; existing output files are not overwritten",
    )
    result.add_argument("--rows-per-page", type=int, default=16)
    result.add_argument(
        "--detail-cell",
        help="Cell ID or RUN_NAME[@HASH]::CELL_ID; defaults to the first manifest cell",
    )
    result.add_argument(
        "--verifier-root",
        type=Path,
        default=Path.cwd(),
        help="Repository containing grid_results.py (default: current directory)",
    )
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not __debug__:
        raise ValueError("Do not run with Python -O: verification requires assertions")
    if args.rows_per_page < 1:
        raise ValueError("--rows-per-page must be a positive integer")
    out_dir = args.out_dir.expanduser().resolve()
    if any(
        out_dir.is_relative_to(path.expanduser().resolve()) for path in args.exports
    ):
        raise ValueError("Output must be outside every immutable input export")
    verifier = load_verifier(args.verifier_root)
    rows = load_rows(args.exports, verifier.verify_public)
    pages, selected = (
        make_pages(rows, args.rows_per_page),
        select_detail(rows, args.detail_cell),
    )
    overview, detail_stem = output_plan(pages, selected, out_dir)
    targets = [
        Path(f"{stem}{suffix}")
        for stem in [*[s for _, s in overview], detail_stem]
        for suffix in (".png", ".svg")
    ] + [out_dir / "plot_manifest.json"]
    if any(path.exists() for path in targets):
        raise ValueError("An output file already exists; choose a new --out-dir")
    # All input verification, completion checks, selection and collision checks
    # precede output creation. No model or runner commands are ever executed.
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_style()
    for page, stem in overview:
        render_overview(page, stem)
    render_detail(selected, detail_stem)
    (out_dir / "plot_manifest.json").write_text(
        json.dumps(
            export_manifest(rows, overview, detail_stem, selected, verifier), indent=2
        )
        + "\n"
    )
    print(
        f"Verified {len(args.exports)} completed grids; wrote {len(overview)} overview pages, 1 detail, and provenance to {out_dir}"
    )
    print(f"Detail: {selected.selector}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
