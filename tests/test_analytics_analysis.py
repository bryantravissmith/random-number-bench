"""Coverage math and grading contract, without inference."""

import importlib.util
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "analytics_analysis", ROOT / "studies/analytics-coverage/analyze.py"
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class AnalysisTest(unittest.TestCase):
    def test_observed_coverage(self):
        self.assertEqual(analysis.coverage([10], 10, 5), 1)
        self.assertEqual(analysis.coverage([5, 5], 10, 10), 2)
        self.assertAlmostEqual(
            analysis.coverage([5, 5], 10, 5), 2 - 2 / math.comb(10, 5)
        )
        self.assertEqual(analysis.coverage([1] * 10, 10, 5), 5)

    def test_failed_trials_stay_in_denominator(self):
        self.assertAlmostEqual(analysis.coverage([2], 10, 1), 0.2)
        self.assertEqual(analysis.coverage([], 10, 5), 0)
        self.assertEqual(analysis.coverage([2], 10, 10), 1)

    def test_no_coverage_extrapolation(self):
        with self.assertRaises(ValueError):
            analysis.coverage([5], 10, 11)

    def test_partial_run_distinguishes_invalid_pending_and_unstarted(self):
        study = {"cases": [{"id": "case"}], "efforts": ["low", "max"]}
        schedule = [
            {"trial": i, "cell": "case", "thinking": "low" if i <= 3 else "max"}
            for i in range(1, 7)
        ]
        attempts = [
            {
                "trial": 1,
                "attempt": 1,
                "case": "case",
                "effort": "low",
                "outcome": "timeout",
            },
            {
                "trial": 1,
                "attempt": 2,
                "case": "case",
                "effort": "low",
                "outcome": "valid",
            },
            {
                "trial": 2,
                "attempt": 1,
                "case": "case",
                "effort": "low",
                "outcome": "invalid_response",
            },
            {
                "trial": 4,
                "attempt": 1,
                "case": "case",
                "effort": "max",
                "outcome": "timeout",
            },
        ]
        progress = analysis.completion_by_cell(study, schedule, attempts, 3)
        self.assertEqual(
            progress["case", "low"],
            {
                "planned": 3,
                "attempted": 2,
                "terminal": 2,
                "pending_attempted": 0,
                "not_attempted": 1,
            },
        )
        self.assertEqual(
            progress["case", "max"],
            {
                "planned": 3,
                "attempted": 1,
                "terminal": 0,
                "pending_attempted": 1,
                "not_attempted": 2,
            },
        )
        # The observed invalid answer is a no-hit. The unstarted trial is not.
        self.assertEqual(
            analysis.coverage([1], progress["case", "low"]["terminal"], 1), 0.5
        )
        with self.assertRaises(ValueError):
            analysis.completion_by_cell(study, schedule, attempts + [attempts[0]], 3)

    def test_invalid_grades_rejected(self):
        study = {"cases": [{"id": "case", "mechanisms": {"a": "A"}}]}
        blind = [{"id": "x", "case": "case"}]
        grade = {
            "id": "x",
            "mechanism": "a",
            "other_label": "",
            "reason": "Concrete control.",
            "single_mechanism": True,
            "consistent": True,
            "diagnostic_score": 2,
        }
        analysis.validate_grades([grade], blind, study)
        for update in [{"diagnostic_score": True}, {"mechanism": "b"}, {"id": "y"}]:
            with self.assertRaises(ValueError):
                analysis.validate_grades([{**grade, **update}], blind, study)


if __name__ == "__main__":
    unittest.main(verbosity=2)
