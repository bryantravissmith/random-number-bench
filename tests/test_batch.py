# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Exercise the actual CLI and process scheduler without inference calls."""

import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import batch


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="random-bench-tests-")
        self.path = Path(self.temp.name) / "batch"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "batch.py"), *map(str, args)],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def plan(self, scenario="ok", count=30, **overrides):
        args = [
            "plan",
            "--out",
            self.path,
            "--count",
            str(count),
            "--concurrency",
            "4",
            "--base-backoff",
            ".04",
            "--max-backoff",
            ".1",
            "--timeout",
            "2",
            "--recovery-successes",
            "4",
            "--circuit-errors",
            "20",
            "--pi-command",
            json.dumps(
                [sys.executable, str(ROOT / "tests/fake_pi.py"), "--scenario", scenario]
            ),
        ]
        for key, value in overrides.items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_parallel_success_and_idempotent_resume(self):
        self.plan()
        result = self.cli("run", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = batch.records(self.path)
        self.assertEqual(len(rows), 30)
        self.assertEqual(batch.read(self.path / "summary.json")["accepted_trials"], 30)
        times = [(r["started_epoch"], 1) for r in rows]
        times += [(r["started_epoch"] + r["elapsed_seconds"], -1) for r in rows]
        active, maximum = 0, 0
        for _, delta in sorted(times):
            active += delta
            maximum = max(maximum, active)
        self.assertGreaterEqual(maximum, 2)
        self.assertLessEqual(maximum, 4)
        old = {
            p: batch.digest(p) for p in (self.path / "attempts").rglob("result.json")
        }
        self.assertEqual(self.cli("run", self.path).returncode, 0)
        self.assertEqual(old, {p: batch.digest(p) for p in old})
        self.assertEqual(len(batch.records(self.path)), 30)

    def test_custom_prompt_is_frozen_and_used(self):
        prompt = "Choose uniformly from 1 to 10.\nReply with only an integer."
        self.plan(count=4, prompt=prompt)
        manifest = batch.read(self.path / "manifest.json")
        self.assertEqual(manifest["config"]["prompt"], prompt)
        result = self.cli("run", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        verified = subprocess.run(
            [sys.executable, str(ROOT / "verify.py"), str(self.path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["accepted_unique_trials"], 4)

    def test_per_trial_prompts_survive_retries(self):
        prompts = [
            f"The last number generated was {i}. Pick an integer from 1 to 10."
            for i in range(1, 7)
        ]
        source = Path(self.temp.name) / "prompts.json"
        source.write_text(json.dumps(prompts))
        self.plan("mixed", count=6, trial_prompts=source)
        source.unlink()  # Execution must rely on the frozen manifest.
        result = self.cli("run", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = batch.records(self.path)
        self.assertEqual(len(rows), 9)
        for row in rows:
            self.assertEqual(row["command"][-1], prompts[row["trial"] - 1])
            audit = batch.events(self.path / row["folder"] / "audit.jsonl")
            request = next(e for e in audit if e["type"] == "request")
            self.assertEqual(
                request["payload"]["input"][1]["content"][0]["text"],
                prompts[row["trial"] - 1],
            )
        verified = subprocess.run(
            [sys.executable, str(ROOT / "verify.py"), str(self.path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["accepted_unique_trials"], 5)

    def test_bad_trial_prompts_rejected_before_planning(self):
        source = Path(self.temp.name) / "prompts.json"
        for prompts in (["one"], ["one", " "], ["one", 2], {"one": "two"}):
            with self.subTest(prompts=prompts):
                source.write_text(json.dumps(prompts))
                result = self.cli(
                    "plan", "--out", self.path, "--count", 2, "--trial-prompts", source
                )
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.path.exists())

    def test_transient_errors_excluded_and_backoff_honored(self):
        self.plan("mixed")
        result = self.cli("run", self.path)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = batch.read(self.path / "summary.json")
        self.assertEqual(report["accepted_trials"], 29)
        self.assertEqual(report["physical_attempts"], 33)
        self.assertEqual(report["excluded_attempts"], 4)
        self.assertEqual(
            report["attempt_outcomes"],
            {
                "rate_limit": 1,
                "valid": 29,
                "provider_error": 1,
                "invalid_response": 1,
                "timeout": 1,
            },
        )
        accepted = batch.read(self.path / "accepted.json")
        self.assertEqual(len({r["trial"] for r in accepted}), 29)
        self.assertFalse(any(r["trial"] == 3 for r in accepted))
        self.assertEqual([r["attempt"] for r in accepted if r["trial"] == 1], [2])
        expected = Counter(str(i % 10 + 1) for i in range(1, 31) if i != 3)
        self.assertEqual(
            report["counts"], {str(i): expected[str(i)] for i in range(1, 11)}
        )
        logs = batch.events(self.path / "controller.jsonl")
        failures = [
            e
            for e in logs
            if e["event"] == "completed" and e["outcome"] in batch.TRANSIENT
        ]
        launches = [e for e in logs if e["event"] == "launch"]
        for failed in failures:
            deadline = failed["controller"]["cooldown_until"]
            stamp = datetime.fromisoformat(failed["timestamp"]).timestamp()
            for launched in launches:
                start = datetime.fromisoformat(launched["timestamp"]).timestamp()
                self.assertFalse(stamp < start < deadline)
        self.assertTrue(any(e["limit"] == 1 for e in launches))
        self.assertEqual(launches[-1]["limit"], 4)

    def test_permanent_errors_stop_without_retry(self):
        for scenario in ("auth", "quota", "wrong_model", "hidden_retry", "early"):
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as folder,
            ):
                self.path = Path(folder) / "batch"
                self.plan(scenario)
                result = self.cli("run", self.path)
                self.assertEqual(result.returncode, 2, result.stderr)
                report = batch.read(self.path / "summary.json")
                self.assertEqual(report["accepted_trials"], 0)
                self.assertLessEqual(report["physical_attempts"], 4)
                self.assertEqual(
                    report["excluded_attempts"], report["physical_attempts"]
                )
                self.assertTrue(
                    all(r["attempt"] == 1 for r in batch.records(self.path))
                )

    def test_retry_exhaustion_and_circuit(self):
        self.plan("always_error", count=2, concurrency=1)
        self.assertEqual(self.cli("run", self.path).returncode, 0)
        report = batch.read(self.path / "summary.json")
        self.assertEqual(
            (
                report["physical_attempts"],
                report["accepted_trials"],
                report["terminal_trials"],
            ),
            (6, 0, 2),
        )
        with tempfile.TemporaryDirectory() as folder:
            self.path = Path(folder) / "batch"
            self.plan("always_error", circuit_errors=3, concurrency=1)
            self.assertEqual(self.cli("run", self.path).returncode, 2)
            self.assertEqual(
                batch.read(self.path / "summary.json")["physical_attempts"], 3
            )

    def test_interrupt_terminates_children_and_resume_does_not_duplicate(self):
        self.plan("stubborn", count=8, timeout=20)
        with (self.path / "test-output.log").open("w") as output:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "batch.py"), "run", str(self.path)],
                stdout=output,
                stderr=output,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    started = list((self.path / "attempts").glob("*/*/started.json"))
                    if len(started) == 4 and all(
                        "pid" in batch.read(p) and (p.parent / "audit.jsonl").exists()
                        for p in started
                    ):
                        break
                    time.sleep(0.02)
                self.assertEqual(len(started), 4)
                duplicate = self.cli("run", self.path)
                self.assertNotEqual(duplicate.returncode, 0)
                process.send_signal(signal.SIGINT)
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        rows = batch.records(self.path)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["outcome"] == "interrupted" for r in rows))
        for row in rows:
            with self.assertRaises(ProcessLookupError):
                os.kill(row["pid"], 0)
        self.assertEqual(batch.read(self.path / "status.json")["state"], "paused")
        control = batch.read(self.path / "controller.json")
        self.assertEqual((control["limit"], control["failures"]), (4, 0))
        before = {
            p: batch.digest(p) for p in (self.path / "attempts").rglob("result.json")
        }
        (self.path / "test-release").touch()
        result = self.cli("run", self.path)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = batch.read(self.path / "summary.json")
        self.assertEqual(
            (report["accepted_trials"], report["physical_attempts"]), (8, 12)
        )
        self.assertEqual(before, {p: batch.digest(p) for p in before})

    def test_retry_after_forms(self):
        self.assertEqual(batch.retry_after({"retry-after": "30"}, 100), 30)
        stamp = format_datetime(datetime.fromtimestamp(160, UTC), usegmt=True)
        self.assertEqual(batch.retry_after({"retry-after": stamp}, 100), 60)
        self.assertEqual(batch.retry_after({"retry-after-ms": "2500"}, 100), 2.5)
        self.assertEqual(batch.retry_after({"retry-after": "inf"}), 0)
        self.assertEqual(batch.retry_after({"retry-after": "garbage"}), 0)

    def test_crash_before_controller_checkpoint_cannot_bypass_fatal_stop(self):
        self.plan("auth")
        self.assertEqual(self.cli("run", self.path).returncode, 2)
        before = batch.records(self.path)
        (self.path / "controller.json").unlink()
        result = self.cli("run", self.path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fatal_error", result.stderr)
        self.assertEqual(before, batch.records(self.path))

    def test_one_trial_auth_error_is_not_reported_as_complete(self):
        self.plan("auth", count=1)
        self.assertEqual(self.cli("run", self.path).returncode, 2)
        self.assertEqual(batch.read(self.path / "status.json")["state"], "stopped")

    def test_recover_empty_attempt_directory(self):
        self.plan(count=1)
        (self.path / "attempts/00001/01").mkdir(parents=True)
        result = self.cli("run", self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = batch.read(self.path / "summary.json")
        self.assertEqual(
            (report["accepted_trials"], report["excluded_attempts"]), (1, 1)
        )
        self.assertEqual(batch.read(self.path / "accepted.json")[0]["attempt"], 2)

    def test_error_message_status_without_http_hook(self):
        self.assertEqual(
            batch.error_kind("OpenAI API error (401): Incorrect API key provided", []),
            "fatal_error",
        )
        self.assertEqual(
            batch.error_kind("OpenAI API error (429): Please wait", []), "rate_limit"
        )

    def test_recovered_finished_child_keeps_usage_and_hashes(self):
        self.plan(count=1)
        self.assertEqual(self.cli("run", self.path).returncode, 0)
        path = self.path / "attempts/00001/01/result.json"
        before = batch.read(path)
        path.unlink()  # Simulate death after child completion but before result durability.
        resumed = self.cli("run", self.path)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        recovered = batch.read(path)
        self.assertEqual(recovered["outcome"], "interrupted")
        self.assertIsNone(recovered["number"])
        self.assertEqual(recovered["usage"], before["usage"])
        self.assertEqual(recovered["evidence_sha256"], before["evidence_sha256"])
        self.assertEqual(recovered["http_statuses"], [200])

    def test_verifier_rejects_retry_after_invalid_response(self):
        self.plan(count=1)
        self.assertEqual(self.cli("run", self.path).returncode, 0)
        first = self.path / "attempts/00001/01"
        second = self.path / "attempts/00001/02"
        shutil.copytree(first, second)
        later = batch.read(second / "result.json")
        later["attempt"] = 2
        later["folder"] = "attempts/00001/02"
        batch.save(second / "result.json", later)
        initial = batch.read(first / "result.json")
        initial.update(outcome="invalid_response", number=None)
        batch.save(first / "result.json", initial)
        batch.summary(self.path, batch.read(self.path / "manifest.json"))
        verified = subprocess.run(
            [sys.executable, str(ROOT / "verify.py"), str(self.path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn("terminal response was retried", verified.stderr)

    def test_verifier_rejects_missing_hashes(self):
        self.plan(count=1)
        self.assertEqual(self.cli("run", self.path).returncode, 0)
        path = self.path / "attempts/00001/01/result.json"
        result = batch.read(path)
        result["evidence_sha256"] = {}
        batch.save(path, result)
        verified = subprocess.run(
            [sys.executable, str(ROOT / "verify.py"), str(self.path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn("Incomplete evidence hash coverage", verified.stderr)

    def test_verifier_refuses_optimized_python(self):
        verified = subprocess.run(
            [sys.executable, "-O", str(ROOT / "verify.py"), str(self.path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(verified.returncode, 0)
        self.assertIn("do not use Python -O", verified.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
