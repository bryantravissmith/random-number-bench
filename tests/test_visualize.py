"""Offline checks for the comparison metric and reproducible public artifact."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import visualize  # noqa: E402


def condition(counts):
    return {"accepted_trials": sum(counts), "counts": dict(enumerate(counts, 1))}


class PresentationTests(unittest.TestCase):
    def test_effective_count_boundaries_and_balanced_support(self):
        for counts, expected in (
            ([0, 0, 0, 0, 0, 0, 100, 0, 0, 0], 1),
            ([10] * 10, 10),
            ([25] * 4 + [0] * 6, 4),
        ):
            self.assertAlmostEqual(
                visualize.diversity(condition(counts))["effective_answers"], expected
            )

    def test_skew_is_not_unique_bin_count(self):
        value = visualize.diversity(condition([99, 1] + [0] * 8))
        self.assertAlmostEqual(value["effective_answers"], 1.0575993064679887)

    def test_empty_completed_condition_is_missing_not_concentrated(self):
        self.assertEqual(
            visualize.diversity(condition([0] * 10)),
            {"entropy_bits": None, "effective_answers": None},
        )

    def test_repeated_immutable_run_cannot_be_silently_pooled(self):
        path = ROOT / "results/2026-11-01-grid-pi-001"
        with self.assertRaisesRegex(ValueError, "Duplicate immutable run"):
            visualize.build_rows([path, path])

    def test_generate_check_and_detect_modified_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "comparison.html"
            spec = Path(directory) / "scope.json"
            spec.write_text(json.dumps({"exports": ["results/2026-11-01-grid-pi-001"]}))
            command = [
                sys.executable,
                str(ROOT / "visualize.py"),
                "--spec",
                str(spec),
                "--out",
                str(target),
            ]
            subprocess.run(command, check=True, capture_output=True)
            subprocess.run(command + ["--check"], check=True, capture_output=True)
            html = target.read_text()
            self.assertIn('id="matrix"', html)
            self.assertNotIn("/* BENCH_DATA */", html)
            self.assertNotIn("/* BENCH_SCRIPT */", html)
            self.assertNotIn("@font-face", html)
            metadata = json.loads(
                target.with_name("comparison-manifest.json").read_text()
            )
            self.assertEqual(metadata["settings"], 1)
            self.assertIn("external-scope.json", metadata["sources_sha256"])
            target.write_text(html + "<!-- modified -->")
            result = subprocess.run(
                command + ["--check"], capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Presentation is stale", result.stderr)


if __name__ == "__main__":
    unittest.main()
