"""Installed Pi with a local OpenRouter-shaped stream. Never calls a provider."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    seen = []
    upstream = "OpenAI"

    def log_message(self, *_):
        pass

    def do_POST(self):
        Handler.seen.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        if len(Handler.seen) == 1:
            body = json.dumps(
                {"error": {"message": "Rate limited", "code": 429}}
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "0.05")
        else:
            base = {
                "id": "local-response",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "openai/gpt-4o",
                "provider": Handler.upstream,
                "system_fingerprint": "local-fingerprint",
            }
            chunks = [
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "7"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 43,
                        "completion_tokens": 1,
                        "total_tokens": 44,
                        "completion_tokens_details": {"reasoning_tokens": 0},
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


class OpenRouterTest(unittest.TestCase):
    def test_transport_routing_prompt_and_zero_thinking_are_verified(self):
        Handler.seen = []
        Handler.upstream = "OpenAI"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config = root / "config"
                config.mkdir()
                (config / "models.json").write_text(
                    json.dumps(
                        {
                            "providers": {
                                "openrouter": {
                                    "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                                    "apiKey": "local-test-placeholder",
                                }
                            }
                        }
                    )
                )
                env = {**os.environ, "PI_CODING_AGENT_DIR": str(config)}
                target = root / "batch"

                def cli(*args):
                    return subprocess.run(
                        [sys.executable, str(ROOT / "batch.py"), *map(str, args)],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )

                planned = cli(
                    "plan",
                    "--out",
                    target,
                    "--provider",
                    "openrouter",
                    "--model",
                    "openai/gpt-4o",
                    "--thinking",
                    "off",
                    "--count",
                    "1",
                    "--concurrency",
                    "1",
                    "--cwd",
                    "/",
                    "--base-backoff",
                    ".05",
                )
                self.assertEqual(planned.returncode, 0, planned.stderr)
                run = cli("run", target)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                rows = [
                    json.loads(p.read_text())
                    for p in sorted((target / "attempts").glob("*/*/result.json"))
                ]
                self.assertEqual([r["outcome"] for r in rows], ["rate_limit", "valid"])
                self.assertEqual(len(Handler.seen), 2)
                for payload in Handler.seen:
                    self.assertEqual(
                        payload["provider"],
                        {"only": ["openai"], "allow_fallbacks": False},
                    )
                    self.assertNotIn("reasoning", payload)
                    self.assertNotIn("reasoning_effort", payload)
                    self.assertNotIn("temperature", payload)
                checked = subprocess.run(
                    [sys.executable, str(ROOT / "verify.py"), str(target)],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(checked.returncode, 0, checked.stderr)
                # Parse a provider mismatch from otherwise successful evidence.
                sys.path.insert(0, str(ROOT))
                import batch

                folder = target / "attempts/00001/02"
                events = batch.events(folder / "audit.jsonl")
                for event in events:
                    if event["type"] == "provider_metadata":
                        event["provider"] = "Azure"
                (folder / "audit.jsonl").write_text(
                    "".join(json.dumps(e) + "\n" for e in events)
                )
                parsed = batch.parse_attempt(
                    folder, batch.read(target / "manifest.json")["config"], 0
                )
                self.assertEqual(parsed["outcome"], "protocol_error")
                self.assertIsNone(parsed["number"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
