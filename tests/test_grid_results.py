"""Offline grid export, public verification, interval and report tests."""

import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import batch
import cli_runners
import grid
import grid_results
from statistics_helpers import tv_to_uniform, wilson_interval

EM_DASH = chr(0x2014)
CELLS = [
    {"id": "gpt-fake-a--low", "model": "gpt-fake-a", "thinking": "low"},
    {"id": "gpt-fake-a--high", "model": "gpt-fake-a", "thinking": "high"},
    {"id": "gpt-fake-b--medium", "model": "gpt-fake-b", "thinking": "medium"},
]
# (baseline, meta) answer per cell. "seven" is always an invalid response.
ANSWERS = {
    "gpt-fake-a--low": ("7", "4"),
    "gpt-fake-a--high": ("3", "3"),
    "gpt-fake-b--medium": ("seven", "10"),
}
FAKE_CODEX = """
import json, os, sys, uuid
from pathlib import Path
args = sys.argv[1:]
if '--version' in args:
    print('fake-codex-grid-1')
elif args == ['login', 'status']:
    print('Logged in using ChatGPT')
else:
    model = args[args.index('--model') + 1]
    effort = next(a.split('=', 1)[1].strip('"') for a in args
                  if a.startswith('model_reasoning_effort='))
    out = Path(args[args.index('--output-last-message') + 1])
    trial, attempt = out.parent.parent.name, out.parent.name
    print(json.dumps({'type': 'thread.started', 'thread_id': str(uuid.uuid4())}))
    if os.environ.get('FAKE_FAILURES') and (
            (trial, attempt) == ('00001', '01') or trial == '00002'):
        print(json.dumps({'type': 'turn.failed',
                          'error': {'message': 'rate limit exceeded private-canary'}}))
        sys.exit(1)
    answers = ANSWERS[model + '--' + effort]
    number = answers[1 if 'random sample' in args[-1] else 0]
    print(json.dumps({'type': 'turn.started'}))
    print(json.dumps({'type': 'item.completed', 'item': {
        'id': 'answer-' + str(uuid.uuid4()), 'type': 'agent_message', 'text': number}}))
    print(json.dumps({'type': 'turn.completed', 'usage': {
        'input_tokens': 10, 'output_tokens': 1}}))
    out.write_text(number)
"""


def make_grid(folder, *, count=1, cells=CELLS[:1], failures=False):
    """Plan in-process with a fast backoff, then run the frozen grid snapshot."""
    tools = folder / "bin"
    tools.mkdir(parents=True)
    fake = tools / "codex"
    fake.write_text(
        f"#!{sys.executable}\nANSWERS = {json.dumps(ANSWERS)}\n{FAKE_CODEX}"
    )
    fake.chmod(0o700)
    # The fake must not inherit real credential or routing variables.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in cli_runners.REJECTED_ENV["codex"] and k != "FAKE_FAILURES"
    }
    env["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]
    if failures:
        env["FAKE_FAILURES"] = "1"
    original = batch.plan

    def fast(options):
        options.base_backoff, options.max_backoff = 0.01, 0.05
        return original(options)

    out = folder / "grid"
    args = SimpleNamespace(
        out=out,
        runner="codex",
        cells=cells,
        count=count,
        concurrency=2,
        timeout=30,
        max_seconds=600,
        max_cost=1,
        order_seed=20261101,
        pi_command=None,
    )
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(grid.batch, "plan", fast),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        grid.plan(args)
    done = subprocess.run(
        [sys.executable, str(ROOT / "grid.py"), "run", str(out)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if done.returncode != 0:
        raise AssertionError(done.stdout + done.stderr)
    return out


def rows_of(public):
    return [
        json.loads(line)
        for line in (public / "attempts.jsonl").read_text().splitlines()
    ]


def rewrite(public, rows=None, summary=None, manifest=None):
    """Tamper with a public export and refresh its file hashes."""
    if rows is not None:
        (public / "attempts.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )
    if summary is not None:
        grid_results.save(public / "summary.json", summary)
    manifest = manifest or grid_results.read(public / "manifest.json")
    manifest["files_sha256"] = {
        name: batch.digest(public / name) for name in manifest["files_sha256"]
    }
    grid_results.save(public / "manifest.json", manifest)


class IntervalTest(unittest.TestCase):
    def test_point_masses_mid_proportion_and_empty(self):
        low, high = wilson_interval(0, 100)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.036994, places=5)
        low, high = wilson_interval(100, 100)
        self.assertAlmostEqual(low, 0.963006, places=5)
        self.assertEqual(high, 1.0)
        low, high = wilson_interval(50, 100)
        self.assertAlmostEqual(low, 0.403832, places=5)
        self.assertAlmostEqual(high, 0.596168, places=5)
        low, high = wilson_interval(1, 10)
        self.assertAlmostEqual(low, 0.01788, places=4)
        self.assertAlmostEqual(high, 0.40415, places=4)
        self.assertIsNone(wilson_interval(0, 0))
        with self.assertRaises(ValueError):
            wilson_interval(3, 2)
        with self.assertRaises(TypeError):
            wilson_interval(True, 1)

    def test_tv(self):
        self.assertEqual(tv_to_uniform([5] * 10), 0)
        self.assertEqual(tv_to_uniform([10] + [0] * 9), 0.9)
        self.assertIsNone(tv_to_uniform([0] * 10))


class GridExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="number-grid-")
        cls.folder = Path(cls.temp.name)
        cls.raw = make_grid(cls.folder, count=3, cells=CELLS, failures=True)
        cls.public = cls.folder / "public"
        done = subprocess.run(
            [
                sys.executable,
                str(ROOT / "grid.py"),
                "export",
                str(cls.raw),
                "--out",
                str(cls.public),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if done.returncode != 0:
            raise AssertionError(done.stdout + done.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="number-grid-copy-")
        self.copy = Path(self.scratch.name) / "copy"
        shutil.copytree(self.public, self.copy)

    def tearDown(self):
        self.scratch.cleanup()

    def test_recount_mapping_and_exclusions(self):
        manifest, summary = grid_results.verify_public(self.public)
        schedule = manifest["schedule"]
        self.assertEqual(len(schedule), 18)
        self.assertEqual(manifest["schema"], "random-number-public-grid-v1")
        self.assertEqual(
            set(manifest["service"]["source_sha256"]),
            {"batch.py", "verify.py", "pi-audit.ts", "grid.py", "cli_runners.py"},
        )
        self.assertEqual(
            manifest["service"]["raw_manifest_sha256"],
            batch.digest(self.raw / "batch/manifest.json"),
        )
        for cell in CELLS:
            for index, condition in enumerate(("baseline", "meta")):
                answer = ANSWERS[cell["id"]][index]
                trials = [
                    e["trial"]
                    for e in schedule
                    if (e["cell"], e["condition"]) == (cell["id"], condition)
                ]
                answered = [t for t in trials if t != 2]
                row = summary["cells"][cell["id"]]["conditions"][condition]
                accepted = len(answered) if answer.isdigit() else 0
                self.assertEqual(row["accepted_trials"], accepted)
                self.assertEqual(row["planned_trials"], 3)
                self.assertEqual(
                    row["invalid_response_attempts"],
                    0 if answer.isdigit() else len(answered),
                )
                self.assertEqual(
                    row["operational_exclusions"], (2 in trials) * 3 + (1 in trials)
                )
                if answer.isdigit():
                    self.assertEqual(row["counts"][answer], accepted)
                    self.assertEqual(row["wilson_intervals"][answer][1], 1.0)
                else:
                    self.assertEqual(sum(row["counts"].values()), 0)
                    self.assertTrue(
                        all(v is None for v in row["wilson_intervals"].values())
                    )
                    self.assertIsNone(row["tv_to_uniform"])
                self.assertGreater(row["missing_cost_attempts"], 0)
        rows = rows_of(self.public)
        self.assertEqual(
            [(r["attempt"], r["outcome"]) for r in rows if r["global_trial"] == 1],
            [(1, "rate_limit"), (2, "valid")],
        )
        self.assertEqual(
            [r["outcome"] for r in rows if r["global_trial"] == 2], ["rate_limit"] * 3
        )
        for row in rows:
            entry = schedule[row["global_trial"] - 1]
            self.assertEqual(
                (row["cell"], row["condition"], row["trial"]),
                (entry["cell"], entry["condition"], entry["condition_trial"]),
            )
            self.assertEqual(row["requested_effort"], entry["thinking"])
            if row["outcome"] != "valid":
                self.assertIsNone(row["number"])
                self.assertIsNone(row["response_text"])
            if row["outcome"] == "invalid_response":
                # An invalid answer is terminal and never replaced by a retry.
                self.assertEqual(
                    [
                        r["attempt"]
                        for r in rows
                        if r["global_trial"] == row["global_trial"]
                    ],
                    [1],
                )

    def test_public_export_has_no_private_values(self):
        text = "".join(p.read_text() for p in self.public.rglob("*") if p.is_file())
        self.assertNotIn(str(self.folder), text)
        self.assertNotIn("private-canary", text)
        # Exported records exclude argv and free-form errors; sources may name flags.
        records = "".join(
            (self.public / name).read_text()
            for name in ("attempts.jsonl", "summary.json", "manifest.json")
        )
        for secret in ("--output-last-message", "rate limit exceeded", EM_DASH):
            self.assertNotIn(secret, records)

    def test_export_is_immutable(self):
        with self.assertRaises(FileExistsError):
            grid_results.export(self.raw, self.public)

    def test_report_lines(self):
        repo = Path(self.scratch.name) / "repo"
        target = repo / "results" / "grid-fixture"
        shutil.copytree(self.public, target)
        lines = grid_results.report_lines(target, base=repo)
        text = "\n".join(lines)
        self.assertEqual(lines, grid_results.report_lines(target, base=repo))
        self.assertNotIn(EM_DASH, text)
        self.assertNotIn(
            grid_results.read(target / "manifest.json")["exported_at"], text
        )
        self.assertIn("(results/grid-fixture/manifest.json)", text)
        self.assertIn("| n | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |", text)
        self.assertIn("| gpt-fake-b | medium | baseline | 0 | 0 [n/a] |", text)
        self.assertIn(", 1.000]", text)
        self.assertIn("0 [0.000, 0.", text)
        self.assertIn("| gpt-fake-b | medium | baseline | 0 / 3 |", text)
        self.assertIn("no bootstrap", text)
        self.assertIn("not simultaneous", text)
        self.assertIn("State: complete.", text)
        self.assertTrue(all(line.isascii() for line in lines))

    def assertRejected(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            grid_results.verify_public(self.copy)

    def test_row_mapping_tampering(self):
        rows = rows_of(self.copy)
        other = next(c["id"] for c in CELLS if c["id"] != rows[0]["cell"])
        rows[0]["cell"] = other
        rewrite(self.copy, rows)
        self.assertRejected("frozen schedule")

    def test_schedule_tampering(self):
        manifest = grid_results.read(self.copy / "manifest.json")
        schedule = manifest["schedule"]
        for key in ("cell", "model", "thinking"):
            for i in (0, 1):
                schedule[i][key], schedule[i + 2][key] = (
                    schedule[i + 2][key],
                    schedule[i][key],
                )
        rewrite(self.copy, manifest=manifest)
        self.assertRejected("frozen order seed|frozen schedule")
        manifest["schedule"] = manifest["schedule"][:-2]
        rewrite(self.copy, manifest=manifest)
        self.assertRejected("count mismatch")

    def test_source_tampering(self):
        path = self.copy / "source/grid.py"
        path.write_text(path.read_text() + "\n# changed\n")
        self.assertRejected("hash mismatch")
        rewrite(self.copy)
        self.assertRejected("provenance")

    def test_duplicate_session_and_accepted_trial(self):
        rows = rows_of(self.copy)
        valid = [r for r in rows if r["outcome"] == "valid"]
        valid[1]["session_id_sha256"] = valid[0]["session_id_sha256"]
        rewrite(self.copy, rows)
        self.assertRejected("Reused")
        rows = rows_of(self.public)
        twin = dict(
            next(r for r in rows if r["outcome"] == "valid" and r["attempt"] == 1)
        )
        twin["attempt"] = 2
        twin["session_id_sha256"] = "f" * 64
        twin["raw_result_sha256"] = "f" * 64
        rewrite(
            self.copy,
            rows + [dict(twin, raw_result_sha256=rows[0]["raw_result_sha256"])],
        )
        self.assertRejected("Duplicate physical attempt")
        rewrite(self.copy, rows + [twin])
        self.assertRejected("Duplicate accepted")

    def test_invalid_response_cannot_be_replaced(self):
        rows = rows_of(self.copy)
        invalid = next(r for r in rows if r["outcome"] == "invalid_response")
        replacement = {
            **invalid,
            "attempt": 2,
            "outcome": "valid",
            "number": 5,
            "response_text": "5",
            "session_id_sha256": "e" * 64,
            "raw_result_sha256": "e" * 64,
        }
        rewrite(self.copy, rows + [replacement])
        self.assertRejected("terminal response was retried")

    def test_excluded_attempt_cannot_contribute(self):
        rows = rows_of(self.copy)
        failed = next(r for r in rows if r["outcome"] == "rate_limit")
        failed["number"] = 7
        rewrite(self.copy, rows)
        self.assertRejected("contributed a number")

    def test_bad_usage_and_unknown_cost(self):
        for key, value in (
            ("pi_reported_cost", -1),
            ("pi_reported_cost", "0.1"),
            ("input_tokens", math.nan),
            ("output_tokens", math.inf),
        ):
            rows = rows_of(self.public)
            rows[0][key] = value
            rewrite(self.copy, rows)
            self.assertRejected("finite nonnegative")
        rows = rows_of(self.public)
        self.assertFalse(rows[0]["reported_cost_known"])
        rows[0]["pi_reported_cost"] = 0.5
        rewrite(self.copy, rows)
        self.assertRejected("finite nonnegative")

    def test_summary_and_interval_tampering(self):
        summary = grid_results.read(self.copy / "summary.json")
        row = summary["cells"]["gpt-fake-a--low"]["conditions"]["baseline"]
        row["wilson_intervals"]["7"] = [0.5, 1.0]
        rewrite(self.copy, summary=summary)
        self.assertRejected("tally")

    def test_extra_or_free_form_fields(self):
        rows = rows_of(self.copy)
        rows[0]["error"] = "free form"
        rewrite(self.copy, rows)
        self.assertRejected("fields")
        rows = rows_of(self.public)
        rows[0]["observed_model"] = "model\nwith newline"
        rewrite(self.copy, rows)
        self.assertRejected("free-form")


class RawGridTamperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="number-grid-raw-")
        self.folder = Path(self.temp.name)
        self.public = self.folder / "public"

    def tearDown(self):
        self.temp.cleanup()

    def assertExportFails(self, raw, error=ValueError):
        with self.assertRaises(error):
            grid_results.export(raw, self.public)
        self.assertFalse(self.public.exists())

    def test_source_hash_checked_before_frozen_code_runs(self):
        raw = make_grid(self.folder)
        canary = self.folder / "executed"
        path = raw / "batch/snapshot/verify.py"
        path.write_text(
            f"from pathlib import Path\nPath({str(canary)!r}).write_text('ran')\n"
            + path.read_text()
        )
        self.assertExportFails(raw)
        self.assertFalse(canary.exists())

    def test_raw_schedule_tampering(self):
        raw = make_grid(self.folder, cells=CELLS[:2])
        path = raw / "batch/manifest.json"
        manifest = batch.read(path)
        first, second = manifest["schedule"][0], manifest["schedule"][1]
        first["condition"], second["condition"] = (
            second["condition"],
            first["condition"],
        )
        first["prompt"], second["prompt"] = second["prompt"], first["prompt"]
        batch.save(path, manifest)
        self.assertExportFails(raw)
        # Even with the grid binding refreshed, the seed and order must agree.
        spec = batch.read(raw / "grid.json")
        spec["batch_manifest_sha256"] = batch.digest(path)
        batch.save(raw / "grid.json", spec)
        self.assertExportFails(raw)

    def test_raw_outcome_tampering_fails_frozen_verifier(self):
        raw = make_grid(self.folder, cells=CELLS[2:])
        paths = sorted((raw / "batch/attempts").glob("*/*/result.json"))
        invalid = next(
            p for p in paths if batch.read(p)["outcome"] == "invalid_response"
        )
        record = batch.read(invalid)
        record.update(outcome="valid", number=7)
        batch.save(invalid, record)
        self.assertExportFails(raw)

    def test_unfinished_attempt_and_owned_batch(self):
        raw = make_grid(self.folder)
        with batch.locked(raw / "batch"):
            self.assertExportFails(raw, RuntimeError)
        (raw / "batch/attempts/00001/02").mkdir()
        self.assertExportFails(raw)

    def test_small_grid_exports(self):
        raw = make_grid(self.folder, cells=CELLS[2:])
        grid_results.export(raw, self.public)
        _, summary = grid_results.verify_public(self.public)
        row = summary["cells"]["gpt-fake-b--medium"]["conditions"]
        self.assertEqual(row["baseline"]["accepted_trials"], 0)
        self.assertEqual(row["baseline"]["invalid_response_attempts"], 1)
        self.assertEqual(row["meta"]["counts"]["10"], 1)
        self.assertEqual(row["meta"]["wilson_intervals"]["10"][1], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
