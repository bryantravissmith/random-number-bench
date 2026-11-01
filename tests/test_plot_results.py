"""Independent presentation fixtures; no runner, inference, or evidence edits."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plot_results as plots


def condition(n=100, *, excluded=0):
    # Exact endpoint Wilson limits at 0/n and n/n, for this synthetic fixture.
    z2 = 1.959963984540054**2
    upper = z2 / (n + z2) if n else None
    return {
        "accepted_trials": n,
        "planned_trials": 100,
        "excluded_attempts": excluded,
        "invalid_response_attempts": excluded,
        "operational_exclusions": 0,
        "counts": {number: n if number == "7" else 0 for number in plots.NUMBERS},
        "wilson_intervals": {
            number: None if not n else ([1 - upper, 1] if number == "7" else [0, upper])
            for number in plots.NUMBERS
        },
        "tv_to_uniform": 0.9 if n else None,
    }


def fixture(version=2, *, identity="a" * 64, state="complete"):
    cells = []
    for index, (model, temp, route, precision) in enumerate(
        [
            ("example/z-model-4.5", 0.7, "provider-one/fp8", "fp8"),
            ("example/a-model-4.5", 1.0, "provider-one/fp8", "fp8"),
            ("example/anchor-model-4.5", 0.7, "provider-two/fp4", "fp4"),
        ]
    ):
        cell = {"id": f"cell-{index}", "model": model, "thinking": "off"}
        if version == 2:
            cell["request_profile"] = {
                "temperature": temp,
                "mode": "non_reasoning",
                "quantization": precision,
                "provider": {
                    "only": [route],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                },
                "expected_provider": route.split("/")[0],
                "stratum": "anchor" if precision == "fp4" else "matched",
                "top_p": 1.0,
                "top_k": 0,
                "frequency_penalty": 0,
                "max_tokens": 4096,
            }
        cells.append(cell)
    manifest = {
        "schema": f"random-number-public-grid-v{version}",
        "grid": {
            "cells": cells,
            "batch_manifest_sha256": identity,
            "protocol": {"version": "synthetic-fixture-not-a-result"},
        },
        "service": {
            "state": state,
            "runner": "pi",
            "runner_version": "fixture-1",
            "provider": "openrouter",
            "evidence_level": "fixture",
            "openrouter_routing": {
                "only": ["fixture-default"],
                "allow_fallbacks": False,
            },
        },
        "files_sha256": {"summary.json": "b" * 64},
    }
    summary = {
        "cells": {
            cell["id"]: {
                "conditions": {
                    "baseline": condition(),
                    "meta": condition(0, excluded=100) if index == 0 else condition(),
                }
            }
            for index, cell in enumerate(cells)
        }
    }
    return manifest, summary


class PlotExportTests(unittest.TestCase):
    def rows(self, version=2):
        return plots.load_rows(
            [Path("synthetic-fixture-not-a-result")],
            Mock(return_value=fixture(version)),
        )

    def test_verification_is_required_before_using_result(self):
        verifier = Mock(side_effect=ValueError("Public evidence hash mismatch"))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            plots.load_rows([Path("untrusted-export")], verifier)
        verifier.assert_called_once()

    def test_incomplete_grid_rejected_even_after_verification(self):
        for state in ("running", "stopped", "paused", "planned"):
            with (
                self.subTest(state=state),
                self.assertRaisesRegex(ValueError, "Only complete"),
            ):
                plots.load_rows(
                    [Path("fixture")], Mock(return_value=fixture(state=state))
                )

    def test_v1_does_not_infer_temperature_mode_or_precision(self):
        row = self.rows(1)[0]
        self.assertIsNone(row.context["precision"])
        self.assertIn("temperature not recorded", plots.settings_label(row))
        self.assertNotIn("mode=", plots.settings_label(row))

    def test_v2_routing_precision_and_stratum_separate_pages(self):
        pages = plots.make_pages(self.rows(), 16)
        self.assertEqual(
            [[r.cell["id"] for r in page.rows] for page in pages],
            [["cell-0", "cell-1"], ["cell-2"]],
        )
        self.assertEqual(pages[0].rows[0].context["precision"], "fp8")
        self.assertEqual(pages[1].rows[0].context["precision"], "fp4")
        self.assertIn("T=0.7", plots.settings_label(pages[0].rows[0]))
        self.assertIn("mode=non_reasoning", plots.settings_label(pages[0].rows[0]))

    def test_pagination_keeps_manifest_order_and_never_pools_runs(self):
        first = plots.load_rows([Path("first")], Mock(return_value=fixture()))
        second = plots.load_rows(
            [Path("second")], Mock(return_value=fixture(identity="c" * 64))
        )
        pages = plots.make_pages(first + second, 1)
        self.assertEqual(
            [page.rows[0].cell["model"] for page in pages],
            [row.cell["model"] for row in first + second],
        )
        self.assertEqual(
            len({(page.rows[0].run_token, page.context_index) for page in pages}), 4
        )

    def test_selector_requires_unique_run_and_cell(self):
        a = self.rows()
        b = plots.load_rows(
            [Path("other")], Mock(return_value=fixture(identity="c" * 64))
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            plots.select_detail(a + b, "cell-0")
        self.assertIs(plots.select_detail(a + b, b[0].selector), b[0])

    def test_duplicate_immutable_run_rejected(self):
        with self.assertRaisesRegex(ValueError, "same immutable run"):
            plots.load_rows([Path("one"), Path("two")], Mock(return_value=fixture()))

    def test_finite_reference_and_nonzero_small_proportions(self):
        self.assertIsNone(plots.uniform_reference(0))
        self.assertAlmostEqual(plots.uniform_reference(100)[0], 0.06, places=6)
        self.assertAlmostEqual(plots.uniform_reference(100)[1], 0.18, places=6)
        self.assertEqual(plots.percent_label(0.001), "0.1")
        self.assertEqual(plots.percent_label(0.0001), "<0.1")
        self.assertEqual(plots.percent_label(0), "0")

    def test_render_zero_accepted_and_long_labels_with_decimal_filename(self):
        rows = self.rows()
        rows[0].cell["model"] = (
            "example/a-long-model-release-name-that-must-wrap-cleanly-over-multiple-lines-4.5"
        )
        plots.setup_style()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plots.render_overview(plots.make_pages(rows, 16)[0], root / "overview-4.5")
            plots.render_detail(rows[0], root / "detail-4.5")
            for name in ("overview-4.5", "detail-4.5"):
                for extension in ("png", "svg"):
                    self.assertGreater(
                        (root / f"{name}.{extension}").stat().st_size, 1000
                    )
            svg = (root / "detail-4.5.svg").read_text()
            self.assertIn("n/a - no accepted answers", svg)
            self.assertIn("mode=non_reasoning", svg)
            self.assertIn("T=0.7", svg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
