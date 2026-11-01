# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Independently recount accepted native sessions and verify evidence hashes."""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

if sys.flags.optimize:
    raise RuntimeError("Verification requires assertions; do not use Python -O")

RETRYABLE = {"rate_limit", "provider_error", "timeout", "interrupted"}


def check_attempt_history(rows, max_attempts):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["trial"], []).append(row)
    for attempts in grouped.values():
        attempts.sort(key=lambda row: row["attempt"])
        if [row["attempt"] for row in attempts] != list(range(1, len(attempts) + 1)):
            raise ValueError("Physical attempt numbers must be contiguous from one")
        if len(attempts) > max_attempts:
            raise ValueError("Physical attempt budget exceeded")
        if any(row["outcome"] not in RETRYABLE for row in attempts[:-1]):
            raise ValueError("A terminal response was retried")


def load(path):
    return json.loads(path.read_text())


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify(root):
    manifest = load(root / "manifest.json")
    base_config = manifest["config"]
    assert [entry["trial"] for entry in manifest["schedule"]] == list(
        range(1, base_config["count"] + 1)
    )
    for name, sha in manifest["source_sha256"].items():
        assert (
            hashlib.sha256((root / "snapshot" / name).read_bytes()).hexdigest() == sha
        ), name
    counts = Counter()
    trials, sessions, outcomes = set(), set(), Counter()
    physical = 0
    history = []
    for path in sorted((root / "attempts").glob("*/*/result.json")):
        result = load(path)
        history.append(result)
        physical += 1
        outcomes[result["outcome"]] += 1
        assert 1 <= result["trial"] <= base_config["count"]
        scheduled = manifest["schedule"][result["trial"] - 1]
        config = {
            **base_config,
            "prompt": scheduled.get("prompt", base_config["prompt"]),
        }
        assert 1 <= result["attempt"] <= config["max_attempts"]
        files = {
            str(p.relative_to(path.parent))
            for p in path.parent.rglob("*")
            if p.is_file() and p.name != "result.json"
        }
        assert set(result["evidence_sha256"]) == files, (
            "Incomplete evidence hash coverage"
        )
        for relative, sha in result["evidence_sha256"].items():
            actual = hashlib.sha256((path.parent / relative).read_bytes()).hexdigest()
            assert actual == sha, f"Changed evidence: {path.parent / relative}"
        if result["outcome"] != "valid":
            assert result["number"] is None, "Error attempt contributed a number"
            continue
        assert result["trial"] not in trials, "Duplicate counted trial"
        trials.add(result["trial"])
        if config.get("runner", "pi") != "pi":
            import cli_runners

            cli_runners.verify_attempt(path.parent, config, result)
            session = result.get("session_id")
            if session:
                assert session not in sessions, "Reused CLI session"
                sessions.add(session)
            counts[result["number"]] += 1
            continue
        assert len(result["session_files"]) == 1
        native = jsonl(path.parent / result["session_files"][0])
        session = next(e["id"] for e in native if e["type"] == "session")
        assert session not in sessions, "Reused conversation"
        sessions.add(session)
        messages = [e["message"] for e in native if e["type"] == "message"]
        assert [m["role"] for m in messages] == ["system", "user", "assistant"]
        assert messages[0]["sections"] == {
            "preamble": config["system_prompt"],
            "cwd": f"<cwd>\n{config['cwd']}\n</cwd>",
        }
        assert messages[1]["content"] == [{"type": "text", "text": config["prompt"]}]
        final = messages[2]
        assert (final["provider"], final["model"], final["thinkingLevel"]) == (
            config["provider"],
            config["model"],
            config["thinking"],
        )
        assert final["stopReason"] == "stop" and not final.get("errorMessage")
        assert all(part["type"] != "toolCall" for part in final["content"])
        answer = "".join(p["text"] for p in final["content"] if p["type"] == "text")
        assert re.fullmatch(r"(?:[1-9]|10)", answer.strip())
        number = int(answer.strip())
        assert result["number"] == number and result["returncode"] == 0
        audit = jsonl(path.parent / "audit.jsonl")
        requests = [e for e in audit if e["type"] == "request"]
        assert len(requests) == 1
        assert [e["status"] for e in audit if e["type"] == "http_response"] == [200]
        payload = requests[0]["payload"]
        assert payload["model"] == config["model"] and not payload.get("tools")
        assert not any(k in payload for k in ("temperature", "top_p", "seed"))
        if config["provider"] == "openai":
            assert payload["reasoning"]["effort"] == config["thinking"]
            expected_input = [
                {
                    "role": "developer",
                    "content": config["system_prompt"]
                    + f"\n\n<cwd>\n{config['cwd']}\n</cwd>",
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": config["prompt"]}],
                },
            ]
            assert payload["input"] in (
                expected_input,
                [
                    *expected_input,
                    {
                        "type": "configuration_update",
                        "reasoning": {"effort": config["thinking"]},
                    },
                ],
            )
        counts[number] += 1
    check_attempt_history(history, base_config["max_attempts"])
    report = load(root / "summary.json")
    expected = {str(i): counts[i] for i in range(1, 11)}
    assert expected == report["counts"]
    assert physical == report["physical_attempts"]
    assert dict(outcomes) == report["attempt_outcomes"]
    assert len(trials) == report["accepted_trials"]
    accepted = load(root / "accepted.json")
    assert len(accepted) == len(trials) and {r["trial"] for r in accepted} == trials
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "physical_attempts": physical,
        "accepted_unique_trials": len(trials),
        "unique_sessions": len(sessions),
        "counts_from_native_sessions": expected,
        "evidence_hashes_match": True,
        "error_attempts_excluded": True,
        "configuration_matches": True,
    }
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.batch.resolve()), indent=2))
