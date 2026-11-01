"""End-to-end round, retry, public export and tamper checks without model calls."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import public_results
import batch


class RoundTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="number-round-")
        self.folder = Path(self.temp.name)
        self.round = self.folder / "round"
        self.result = self.folder / "result"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "bench.py"), *map(str, args)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def prepare(self, scenario="ok", count=6, expected_exit=0):
        fake = json.dumps(
            [sys.executable, str(ROOT / "tests/fake_pi.py"), "--scenario", scenario]
        )
        planned = self.cli(
            "plan",
            "--out",
            self.round,
            "--count",
            count,
            "--order-seed",
            1234,
            "--pi-command",
            fake,
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        spec = public_results.read(self.round / "round.json")
        for i in range(0, 2 * count, 2):
            self.assertEqual(
                {r["condition"] for r in spec["schedule"][i : i + 2]},
                {"baseline", "meta"},
            )
        run = self.cli("run", self.round)
        self.assertEqual(run.returncode, expected_exit, run.stderr)

    def test_stopped_batch_errors_export_without_free_text(self):
        self.prepare(scenario="auth", count=2, expected_exit=2)
        paths = list((self.round / "batch/attempts").glob("*/*/result.json"))
        for path in paths:
            data = public_results.read(path)
            data["error"] = "private-export-canary"
            public_results.save(path, data)
        exported = self.cli("export", self.round, "--out", self.result)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        _, summary = public_results.verify_public(self.result)
        self.assertEqual(sum(r["accepted_trials"] for r in summary.values()), 0)
        self.assertEqual(
            sum(r["excluded_attempts"] for r in summary.values()), len(paths)
        )
        self.assertNotIn(
            "private-export-canary",
            "".join(p.read_text() for p in self.result.rglob("*") if p.is_file()),
        )

    def test_export_refuses_owned_batch(self):
        self.prepare(count=1)
        with batch.locked(self.round / "batch"):
            result = self.cli("export", self.round, "--out", self.result)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.result.exists())

    def test_export_recounts_and_preserves_originals(self):
        self.prepare()
        exported = self.cli("export", self.round, "--out", self.result)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        manifest, summary = public_results.verify_public(self.result)
        self.assertEqual(summary["baseline"]["accepted_trials"], 6)
        self.assertEqual(summary["meta"]["accepted_trials"], 6)
        public_text = "".join(
            p.read_text() for p in self.result.rglob("*") if p.is_file()
        )
        self.assertNotIn(str(ROOT), public_text)
        self.assertNotIn(str(self.folder), public_text)
        self.assertEqual(manifest["design"], "interleaved_pairs")
        self.assertNotEqual(
            self.cli("export", self.round, "--out", self.result).returncode, 0
        )
        old = public_results.digest(self.result / "attempts.jsonl")
        self.assertEqual(self.cli("run", self.round).returncode, 0)
        self.assertEqual(public_results.digest(self.result / "attempts.jsonl"), old)

    def test_public_tampering_is_rejected(self):
        self.prepare(count=2)
        self.assertEqual(
            self.cli("export", self.round, "--out", self.result).returncode, 0
        )
        path = self.result / "attempts.jsonl"
        path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
        with self.assertRaises(ValueError):
            public_results.verify_public(self.result)

    def test_changed_manifest_or_condition_rejected(self):
        self.prepare(count=2)
        path = self.round / "round.json"
        spec = public_results.read(path)
        spec["schedule"][0]["condition"] = (
            "meta" if spec["schedule"][0]["condition"] == "baseline" else "baseline"
        )
        public_results.save(path, spec)
        self.assertNotEqual(
            self.cli("export", self.round, "--out", self.result).returncode, 0
        )
        self.assertFalse(self.result.exists())

    def test_native_cli_round_and_unknown_cost_export(self):
        binary = self.folder / "codex"
        binary.write_text(
            "#!"
            + sys.executable
            + "\n"
            + """
import json, sys, uuid
args = sys.argv[1:]
if '--version' in args:
    print('fake-codex-1')
elif args == ['login', 'status']:
    print('Logged in using ChatGPT')
else:
    number = '4' if 'random sample' in args[-1] else '7'
    print(json.dumps({'type': 'thread.started', 'thread_id': str(uuid.uuid4())}))
    print(json.dumps({'type': 'turn.started'}))
    print(json.dumps({'type': 'item.completed', 'item': {
        'id': 'answer', 'type': 'agent_message', 'text': number}}))
    print(json.dumps({'type': 'turn.completed', 'usage': {
        'input_tokens': 10, 'output_tokens': 1}}))
    from pathlib import Path
    Path(args[args.index('--output-last-message') + 1]).write_text(number)
"""
        )
        binary.chmod(0o700)
        env = {**os.environ, "PATH": str(self.folder) + os.pathsep + os.environ["PATH"]}

        def invoke(*args):
            return subprocess.run(
                [sys.executable, str(ROOT / "bench.py"), *map(str, args)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

        result = invoke("plan", "--runner", "codex", "--out", self.round, "--count", 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = invoke("run", self.round)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = invoke("export", self.round, "--out", self.result)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest, summary = public_results.verify_public(self.result)
        self.assertEqual(manifest["sources"]["shared"]["runner"], "codex")
        self.assertEqual(summary["baseline"]["counts"]["7"], 2)
        self.assertEqual(summary["meta"]["counts"]["4"], 2)
        self.assertEqual(summary["meta"]["missing_cost_attempts"], 2)
        rows = [
            json.loads(line)
            for line in (self.result / "attempts.jsonl").read_text().splitlines()
        ]
        self.assertTrue(all(r["http_statuses"] == [] for r in rows))
        report = self.folder / "report.md"
        # Ignore the raw round by putting only the public export under this root.
        results = self.folder / "results"
        results.mkdir()
        self.result.rename(results / "codex-fixture")
        public_results.report(results, report)
        self.assertIn("n/a", report.read_text())
        self.assertIn("| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |", report.read_text())

    def test_native_orphan_cannot_duplicate_a_live_process(self):
        folder = self.round / "attempts/00001/01"
        folder.mkdir(parents=True)
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        process = subprocess.Popen(command, start_new_session=True)
        manifest = {
            "config": {"runner": "codex", "prompt": "test", "model": "fixture"},
            "schedule": [{"trial": 1}],
        }
        public_results.save(
            folder / "started.json",
            {
                "trial": 1,
                "attempt": 1,
                "pid": process.pid,
                "command": command,
                "folder": "attempts/00001/01",
            },
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "still alive"):
                batch.recover_unfinished(self.round, manifest)
            self.assertFalse((folder / "result.json").exists())
        finally:
            process.terminate()
            process.wait(timeout=5)
        batch.recover_unfinished(self.round, manifest)
        recovered = public_results.read(folder / "result.json")
        self.assertEqual(recovered["runner"], "codex")
        self.assertEqual(recovered["outcome"], "interrupted")
        self.assertIsNone(recovered["number"])

    def test_public_error_cannot_contribute_number(self):
        rows = [
            {
                "condition": "baseline",
                "trial": 1,
                "attempt": 1,
                "outcome": "provider_error",
                "number": None,
                "pi_reported_cost": 0,
            },
            {
                "condition": "baseline",
                "trial": 1,
                "attempt": 2,
                "outcome": "valid",
                "number": 7,
                "pi_reported_cost": 0.01,
            },
        ]
        result = public_results.tally(rows, {"baseline": 1, "meta": 1})
        self.assertEqual(result["baseline"]["counts"]["7"], 1)
        self.assertEqual(result["baseline"]["physical_attempts"], 2)
        self.assertEqual(result["baseline"]["excluded_attempts"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
