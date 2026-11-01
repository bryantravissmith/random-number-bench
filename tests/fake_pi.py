# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Offline Pi process double with real files, delays, and error responses."""

import json
import os
import signal
import sys
import time
from pathlib import Path


def main():
    if "--version" in sys.argv:
        print("fake-pi-test-1")
        return

    def arg(name):
        return sys.argv[sys.argv.index(name) + 1]

    scenario = arg("--scenario")
    session_dir = Path(arg("--session-dir"))
    session_dir.mkdir()
    attempt = int(session_dir.parent.name)
    trial = int(session_dir.parent.parent.name)
    code, error, answer, delay = 200, "", str(trial % 10 + 1), 0.03
    if scenario == "mixed":
        if trial == 1 and attempt == 1:
            code, error, answer = 429, "429 rate limit", "7"
        elif trial == 2 and attempt == 1:
            code, error = 503, "503 service unavailable"
        elif trial == 3:
            answer = "The number is 7."
        elif trial == 4 and attempt == 1:
            delay = 5
    elif scenario == "auth":
        code, error = 401, "Invalid API key"
    elif scenario == "quota":
        code, error = 429, "insufficient_quota"
    elif scenario == "always_error":
        code, error = 503, "Server unavailable"
    elif scenario in ("slow", "stubborn"):
        delay = 0.03 if (session_dir.parents[3] / "test-release").exists() else 10
        if scenario == "stubborn":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif scenario == "early":
        print("Unknown model", file=sys.stderr)
        return 1
    audit = Path(os.environ["RANDOM_BENCH_AUDIT"])
    requests = 2 if scenario == "hidden_retry" else 1
    with audit.open("w") as stream:
        for _ in range(requests):
            stream.write(
                json.dumps(
                    {
                        "type": "request",
                        "payload": {
                            "model": arg("--model"),
                            "tools": [],
                            "reasoning": {"effort": arg("--thinking")},
                            "input": [
                                {
                                    "role": "developer",
                                    "content": arg("--system-prompt")
                                    + f"\n\n<cwd>\n{os.getcwd()}\n</cwd>",
                                },
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "input_text", "text": sys.argv[-1]}
                                    ],
                                },
                            ],
                        },
                    }
                )
                + "\n"
            )
        stream.write(
            json.dumps(
                {
                    "type": "http_response",
                    "status": code,
                    "headers": {"retry-after": "0.06" if code == 429 else "0"},
                }
            )
            + "\n"
        )
    time.sleep(delay)
    messages = [
        {"type": "session", "id": f"fake-{trial}-{attempt}"},
        {
            "type": "message",
            "message": {
                "role": "system",
                "content": "",
                "sections": {
                    "preamble": arg("--system-prompt"),
                    "cwd": f"<cwd>\n{os.getcwd()}\n</cwd>",
                },
            },
        },
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": sys.argv[-1]}],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": answer}],
                "provider": arg("--provider"),
                "model": arg("--model"),
                "thinkingLevel": arg("--thinking"),
                "stopReason": "stop" if code == 200 else "error",
                "errorMessage": error,
                "usage": {"cost": {"total": 0.0001}},
            },
        },
    ]
    if scenario == "wrong_model":
        messages[-1]["message"]["model"] = "unrequested-model"
    (session_dir / "session.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in messages)
    )
    print(json.dumps({"type": "agent_end", "willRetry": False}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
