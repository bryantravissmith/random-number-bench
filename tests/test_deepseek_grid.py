"""Real Pi, frozen fixed-decoding grid and public export against loopback HTTP."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import batch
import grid
import grid_results


class Handler(BaseHTTPRequestHandler):
    seen = []
    aliases = {}
    request_lock = threading.Lock()

    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with Handler.request_lock:
            Handler.seen.append(payload)
            request_index = len(Handler.seen)
        if request_index == 1:
            body = b'{"error":{"code":429,"message":"Rate limited"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "0.01")
        else:
            thinking = payload.get("reasoning", {}).get("enabled", False)
            provider = (
                "DeepInfra"
                if payload["model"] == "deepseek/deepseek-chat"
                else "SiliconFlow"
            )
            base = {
                "id": "fixture-" + uuid.uuid4().hex,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": Handler.aliases[payload["model"]],
                "provider": provider,
            }
            delta = {"role": "assistant", "content": "7"}
            if thinking:
                delta["reasoning"] = "Fixture reasoning."
            chunks = [
                {
                    **base,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "length"
                            if request_index == 2
                            else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 5 if thinking else 1,
                        "total_tokens": 55 if thinking else 51,
                        "cost": 0.001,
                        "completion_tokens_details": {
                            "reasoning_tokens": 4 if thinking else 0
                        },
                    },
                },
            ]
            body = (
                "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DeepSeekGridTest(unittest.TestCase):
    def test_all_profile_cells_and_export_with_real_pi(self):
        profile = batch.read(ROOT / "grids/deepseek.json")
        Handler.seen = []
        Handler.aliases = {
            m["model"]: m["accepted_models"][-1] for m in profile["models"]
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                config = root / "config"
                config.mkdir()
                batch.save(
                    config / "models.json",
                    {
                        "providers": {
                            "openrouter": {
                                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                                "apiKey": "local-fixture-placeholder",
                            }
                        }
                    },
                )
                target = root / "grid"
                args = SimpleNamespace(
                    out=target,
                    runner="pi",
                    cells=grid.profile_cells(profile),
                    count=1,
                    concurrency=4,
                    timeout=20,
                    max_seconds=120,
                    max_cost=10,
                    order_seed=123,
                    pi_command=None,
                )
                original_plan = batch.plan

                def fast_plan(options):
                    options.base_backoff = 0.01
                    options.max_backoff = 0.02
                    original_plan(options)

                with (
                    patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(config)}),
                    patch.object(batch, "plan", fast_plan),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    grid.plan(args)
                env = {**os.environ, "PI_CODING_AGENT_DIR": str(config)}
                done = subprocess.run(
                    [sys.executable, str(ROOT / "grid.py"), "run", str(target)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                rows = batch.records(target / "batch")
                self.assertEqual(len(rows), 37)
                self.assertEqual(sum(r["outcome"] == "valid" for r in rows), 35)
                self.assertEqual(
                    sum(r.get("invalid_reason") == "truncated" for r in rows), 1
                )
                self.assertEqual(sum(r["outcome"] == "rate_limit" for r in rows), 1)
                self.assertEqual(len(Handler.seen), 37)
                for request in Handler.seen:
                    self.assertIn(request["temperature"], (0.7, 1.0))
                    self.assertEqual(request["top_p"], 1)
                    self.assertEqual(request["top_k"], 0)
                    self.assertEqual(request["max_tokens"], 4096)
                    self.assertFalse(request["provider"]["allow_fallbacks"])
                    self.assertTrue(request["provider"]["require_parameters"])
                for row in rows:
                    if row["outcome"] == "valid":
                        self.assertEqual(row["usage"]["cost"]["total"], 0.001)
                destination = root / "public"
                grid_results.export(target, destination)
                manifest, summary = grid_results.verify_public(destination)
                self.assertEqual(manifest["schema"], grid_results.PROFILE_SCHEMA)
                self.assertEqual(summary["totals"]["accepted_trials"], 35)
                self.assertEqual(
                    sum(
                        c["conditions"][p]["truncated_response_attempts"]
                        for c in summary["cells"].values()
                        for p in ("baseline", "meta")
                    ),
                    1,
                )
                self.assertEqual(len(summary["cells"]), 18)
                report = "\n".join(grid_results.report_lines(destination))
                self.assertIn("Temperature", report)
                self.assertIn("historical_anchor", report)
                for cell in summary["cells"].values():
                    self.assertIn(cell["request_profile"]["temperature"], (0.7, 1.0))
                # A truncated answer with a changed protocol is still a fatal
                # protocol violation, rather than an ordinary invalid response.
                truncated = next(
                    r for r in rows if r.get("invalid_reason") == "truncated"
                )
                folder = (
                    target
                    / "batch"
                    / "attempts"
                    / f"{truncated['trial']:05d}"
                    / f"{truncated['attempt']:02d}"
                )
                manifest = batch.read(target / "batch" / "manifest.json")
                cfg = batch.trial_config(manifest, truncated["trial"])
                cfg["prompt"] = "Changed prompt"
                reparsed = batch.parse_attempt(folder, cfg, truncated["returncode"])
                self.assertEqual(reparsed["outcome"], "protocol_error")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
