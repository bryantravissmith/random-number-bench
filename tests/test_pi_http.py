# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Actual installed Pi against a local fake HTTP endpoint; no real model calls."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    requests_seen: ClassVar[list[float]] = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", "0")))
        Handler.requests_seen.append(time.time())
        if len(Handler.requests_seen) == 1:
            data = json.dumps(
                {
                    "error": {
                        "message": "Please wait",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                }
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "1")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        item = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "7", "annotations": []}],
        }
        response = {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "model": "gpt-6.1-sol",
            "output": [item],
            "usage": {
                "input_tokens": 5,
                "output_tokens": 1,
                "total_tokens": 6,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        events = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**item, "content": [], "status": "in_progress"},
            },
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_test",
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_test",
                "delta": "7",
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        data = "".join(
            f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RealPiTransportTest(unittest.TestCase):
    def test_http_429_header_survives_real_pi_and_retry_counts_once(self):
        Handler.requests_seen = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="random-bench-http-") as temp:
                root = Path(temp)
                config = root / "config"
                config.mkdir()
                (config / "models.json").write_text(
                    json.dumps(
                        {
                            "providers": {
                                "openai": {
                                    "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                                    "apiKey": "local-test-placeholder",
                                }
                            }
                        }
                    )
                )
                env = {**os.environ, "PI_CODING_AGENT_DIR": str(config)}
                target = root / "batch"
                planned = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "batch.py"),
                        "plan",
                        "--out",
                        str(target),
                        "--count",
                        "1",
                        "--cwd",
                        "/",
                        "--concurrency",
                        "1",
                        "--base-backoff",
                        ".05",
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=20,
                )
                self.assertEqual(planned.returncode, 0, planned.stderr)
                run = subprocess.run(
                    [sys.executable, str(ROOT / "batch.py"), "run", str(target)],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
                results = [
                    json.loads(p.read_text())
                    for p in sorted((target / "attempts").glob("*/*/result.json"))
                ]
                self.assertEqual(
                    results[0]["retry_after_seconds"], 1, run.stdout + run.stderr
                )
                audits = [
                    json.loads(line)
                    for line in (target / "attempts/00001/02/audit.jsonl")
                    .read_text()
                    .splitlines()
                ]
                self.assertEqual(
                    run.returncode, 0, json.dumps(audits) + run.stdout + run.stderr
                )
                self.assertEqual(
                    [r["outcome"] for r in results], ["rate_limit", "valid"]
                )
                self.assertEqual(
                    len(Handler.requests_seen), 2, "Hidden retries or extra requests"
                )
                self.assertGreaterEqual(
                    Handler.requests_seen[1] - Handler.requests_seen[0], 1
                )
                report = json.loads((target / "summary.json").read_text())
                self.assertEqual(
                    (report["accepted_trials"], report["excluded_attempts"]), (1, 1)
                )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
