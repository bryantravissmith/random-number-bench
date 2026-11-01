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
