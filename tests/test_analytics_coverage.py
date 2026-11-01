"""Native parser and frozen end-to-end fixtures; no provider calls."""

import importlib.util
import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_cli_runners as fixtures
import batch

spec = importlib.util.spec_from_file_location(
    "coverage_probe", ROOT / "studies/analytics-coverage/probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
analysis_spec = importlib.util.spec_from_file_location(
    "coverage_analysis", ROOT / "studies/analytics-coverage/analyze.py"
)
analysis = importlib.util.module_from_spec(analysis_spec)
analysis_spec.loader.exec_module(analysis)
ANSWER = {
    "hypothesis": "A checkout regression affects mobile users.",
    "test": "Compare mobile checkout errors before and after release against desktop.",
    "expected_if_true": "Mobile errors increase while desktop stays stable.",
}


class CoverageTest(unittest.TestCase):
    def parse(self, text, events=None):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "events.jsonl").write_text(
                "".join(
                    json.dumps(e) + "\n"
                    for e in (events or fixtures.claude_events(text))
                )
            )
            (p / "stderr.log").write_text("")
            return probe.parse(p, fixtures.config_for("claude", Path("/")), 0)

    def test_json_is_accepted_after_native_checks(self):
        r = self.parse(json.dumps(ANSWER))
        self.assertEqual(r["outcome"], "valid")
        self.assertIsNone(r["number"])
        self.assertEqual(r["answer"], ANSWER)

    def test_format_failures_stay_terminal_invalid(self):
        for text in (
            "7",
            '{"hypothesis":"x"}',
            "```json\n" + json.dumps(ANSWER) + "\n```",
        ):
            self.assertEqual(self.parse(text)["outcome"], "invalid_response")

    def test_native_retry_never_becomes_valid_json(self):
        text = json.dumps(ANSWER)
        events = fixtures.claude_events(text)
        events.insert(
            1,
            {
                "type": "system",
                "subtype": "api_retry",
                "error_status": 429,
                "error": "Rate limit",
            },
        )
        self.assertNotEqual(self.parse(text, events)["outcome"], "valid")

    def test_model_mismatch_fails_closed(self):
        text = json.dumps(ANSWER)
        events = fixtures.claude_events(text)
        events[1]["message"]["model"] = "another-model"
        self.assertEqual(self.parse(text, events)["outcome"], "protocol_error")

    def test_frozen_run_export_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            binary = tmp / "claude"
            script = (
                "#!"
                + sys.executable
                + "\n"
                + f"""
import sys,json,uuid
sys.path.insert(0,{str(ROOT / "tests")!r})
from test_cli_runners import claude_events
args=sys.argv[1:]
if '--version' in args: print('fixture-claude-1')
elif args[:2]==['auth','status']: print(json.dumps({{'loggedIn':True,'authMethod':'oauth_token','apiProvider':'firstParty'}}))
else:
 events=claude_events({json.dumps(ANSWER)!r})
 sid=str(uuid.uuid4())
 for e in events: e['session_id']=sid;print(json.dumps(e))
"""
            )
            binary.write_text(script)
            binary.chmod(0o755)
            env = {**os.environ, "PATH": str(tmp) + os.pathsep + os.environ["PATH"]}
            raw = tmp / "raw"
            out = tmp / "public"
            with patch.dict(os.environ, env):
                probe.plan(raw)
            for action, extra in [("run", []), ("export", ["--out", str(out)])]:
                if action == "export":
                    # Simulate a cutoff in this fake-CLI fixture, retaining the
                    # original paths required by frozen argv verification.
                    original_status = batch.read(raw / "status.json")
                    parked = tmp / "unreached-fixture-trials"
                    parked.mkdir()
                    for folder in (raw / "attempts").iterdir():
                        if int(folder.name) > 6:
                            shutil.move(folder, parked / folder.name)
                    batch.save(
                        raw / "status.json",
                        {
                            "state": "stopped",
                            "elapsed_seconds": 1800,
                            "controller": {"stopped": "Fixture time limit"},
                        },
                    )
                    partial = tmp / "partial"
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(probe.HERE / "export_partial.py"),
                            str(raw),
                            "--out",
                            str(partial),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertEqual(
                        batch.read(partial / "manifest.json")["state"], "stopped"
                    )
                    blind_partial = batch.read(raw / "blind-responses.json")
                    self.assertEqual(len(blind_partial), 6)
                    partial_grades = [
                        {
                            "id": r["id"],
                            "mechanism": "checkout_regression"
                            if r["case"] == "checkout"
                            else "routing_quality",
                            "other_label": "",
                            "single_mechanism": True,
                            "consistent": True,
                            "diagnostic_score": 2,
                            "reason": "Fixture grade.",
                        }
                        for r in blind_partial
                    ]
                    batch.save(raw / "grades.json", partial_grades)
                    batch.save(raw / "audit.json", {"changes": []})
                    analysis.packet(raw, raw / "grading-prompt.txt")
                    analysis.freeze(
                        raw,
                        partial,
                        raw / "grades.json",
                        raw / "grades.json",
                        raw / "audit.json",
                        raw / "grading-prompt.txt",
                    )
                    with contextlib.redirect_stdout(io.StringIO()):
                        analysis.report(partial)
                    partial_summary = batch.read(partial / "summary.json")
                    self.assertTrue(partial_summary["partial"])
                    self.assertEqual(partial_summary["terminal_trials"], 6)
                    self.assertEqual(partial_summary["missing_trials"], 34)
                    self.assertTrue(
                        all(
                            c["coverage"]["5"] is None for c in partial_summary["cells"]
                        )
                    )
                    # Restore the full fixture and then exercise the ordinary
                    # complete-run export below. Real evidence is never altered.
                    for folder in parked.iterdir():
                        shutil.move(folder, raw / "attempts" / folder.name)
                    batch.save(raw / "status.json", original_status)
                    (raw / "blind-key.json").unlink()
                    (raw / "blind-responses.json").unlink()
                done = subprocess.run(
                    [
                        sys.executable,
                        str(probe.HERE / "probe.py"),
                        action,
                        str(raw),
                        *extra,
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            rows = batch.read(out / "attempts.json")
            self.assertEqual(len(rows), 40)
            self.assertTrue(all(r["outcome"] == "valid" for r in rows))
            self.assertEqual(len(batch.read(raw / "blind-responses.json")), 40)
            blind = batch.read(raw / "blind-responses.json")
            grades = [
                {
                    "id": r["id"],
                    "mechanism": "checkout_regression"
                    if r["case"] == "checkout"
                    else "routing_quality",
                    "other_label": "",
                    "single_mechanism": True,
                    "consistent": True,
                    "diagnostic_score": 2,
                    "reason": "Fixture grade.",
                }
                for r in blind
            ]
            batch.save(raw / "grades.json", grades)
            batch.save(raw / "audit.json", {"changes": []})
            analysis.packet(raw, raw / "grading-prompt.txt")
            self.assertNotIn("planted_cause", (raw / "grading-prompt.txt").read_text())
            analysis.freeze(
                raw,
                out,
                raw / "grades.json",
                raw / "grades.json",
                raw / "audit.json",
                raw / "grading-prompt.txt",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                analysis.report(out)
            self.assertTrue(
                all(
                    c["distinct"] == 1
                    for c in batch.read(out / "summary.json")["cells"]
                )
            )
            grades[0]["reason"] = "Changed after seal"
            batch.save(out / "grading/grades.json", grades)
            with self.assertRaises(ValueError):
                analysis.report(out)
            m = batch.read(raw / "manifest.json")
            m["config"]["concurrency"] = 99
            batch.save(raw / "manifest.json", m)
            with self.assertRaises(ValueError):
                probe.check_plan(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
