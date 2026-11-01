"""Offline end-to-end checks for heterogeneous, frozen service grids."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import batch
import grid
import verify


class GridTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        binary = self.root / "codex"
        binary.write_text(
            "#!"
            + sys.executable
            + "\n"
            + """
import json,sys,uuid
args=sys.argv[1:]
if '--version' in args:
    print('fake-codex-grid-1')
elif args == ['login','status']:
    print('Logged in using ChatGPT')
else:
    model=args[args.index('--model')+1]
    maximum='model_reasoning_effort="max"' in args
    number=('2' if maximum else '7') if model=='test-a' else ('3' if maximum else '8')
    print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}))
    print(json.dumps({'type':'item.completed','item':{'id':'x','type':'agent_message','text':number}}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':1}}))
    if '--output-last-message' in args:
        open(args[args.index('--output-last-message')+1],'w').write(number)
"""
        )
        binary.chmod(0o755)
        self.env = patch.dict(
            os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def planned(self, concurrency=3):
        target = self.root / "grid"
        cells = [
            {"id": model + "--" + effort, "model": model, "thinking": effort}
            for model in ("test-a", "test-b")
            for effort in ("low", "max")
        ]
        args = SimpleNamespace(
            out=target,
            runner="codex",
            count=3,
            concurrency=concurrency,
            timeout=20,
            max_seconds=120,
            max_cost=10,
            order_seed=123,
            cells=cells,
            pi_command=None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            grid.plan(args)
        return target

    def test_grid_executes_effective_cells_and_resumes_without_duplicates(self):
        target = self.planned()
        spec, manifest = grid.validate(target)
        command = [sys.executable, str(ROOT / "grid.py"), "run", str(target)]
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        rows = batch.records(target / "batch")
        self.assertEqual(len(rows), 24)
        for row in rows:
            config = batch.trial_config(manifest, row["trial"])
            expected = {
                ("test-a", "low"): 7,
                ("test-a", "max"): 2,
                ("test-b", "low"): 8,
                ("test-b", "max"): 3,
            }[config["model"], config["thinking"]]
            self.assertEqual(row["number"], expected)
        verify.verify(target / "batch")
        before = {
            p: batch.digest(p)
            for p in (target / "batch/attempts").glob("*/*/result.json")
        }
        again = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(before, {p: batch.digest(p) for p in before})
        for block in range(1, 4):
            self.assertEqual(
                len(
                    {
                        (r["cell"], r["condition"])
                        for r in manifest["schedule"]
                        if r["block"] == block
                    }
                ),
                8,
            )

    def test_changed_grid_spec_or_schedule_is_rejected(self):
        target = self.planned()
        spec, manifest = grid.validate(target)
        changed = json.loads(json.dumps(manifest))
        changed["schedule"][0]["thinking"] = "medium"
        with self.assertRaises(ValueError):
            grid.validate_schedule(spec, changed["schedule"], changed["config"])
        spec["order_seed"] = 0
        batch.save(target / "grid.json", spec)
        with self.assertRaises(ValueError):
            grid.validate(target)

    def test_binary_update_stops_cleanly_before_next_launch(self):
        binary = self.root / "codex"
        with binary.open("a") as stream:
            stream.write(
                "\nif '--model' in args:\n    with open(__file__, 'a') as f: f.write('\\n# updated\\n')\n"
            )
        target = self.planned(concurrency=1)
        done = subprocess.run(
            [sys.executable, str(ROOT / "grid.py"), "run", str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        status = batch.read(target / "batch/status.json")
        self.assertEqual(status["state"], "stopped")
        self.assertIn("executable changed", status["controller"]["stopped"])
        rows = batch.records(target / "batch")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "valid")
        self.assertEqual(len(list((target / "batch/attempts").glob("*/*"))), 1)
        verify.verify(target / "batch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
