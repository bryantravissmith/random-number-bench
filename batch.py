# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Plan, run/resume, and summarize bounded parallel headless model batches."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "random-numbers-v1.0"
RUNNER_DEFAULTS = {
    "pi": ("openai", "gpt-6.1-sol"),
    "claude": ("anthropic", "claude-opus-5-5"),
    "codex": ("openai", "gpt-6.1-sol"),
    "grok": ("xai", "grok-4.7"),
    "agy": ("antigravity", "gemini-3.8-flash-medium"),
}
PROMPT = (
    "Generate one random integer from 1 to 10, inclusive. Reply with only the integer."
)
SYSTEM = "You are a helpful assistant."
SETTINGS = {
    "retry": {"enabled": False, "maxRetries": 0, "provider": {"maxRetries": 0}},
    "cacheWarming": "off",
    "compaction": {"enabled": False},
    "transport": "sse",
    "enableInstallTelemetry": False,
    "enableAnalytics": False,
    "quietStartup": True,
}
TRANSIENT = {"rate_limit", "provider_error", "timeout", "interrupted"}
TERMINAL = {"valid", "invalid_response", "fatal_error", "protocol_error"}


def now():
    return datetime.now(UTC).isoformat()


def save(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as stream:
        json.dump(obj, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_provider_metadata(provider):
    source = Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent")))
    auth = read(source / "auth.json") if (source / "auth.json").exists() else {}
    entry = auth.get(provider, {})
    return {
        "stored_auth_type": entry.get("type") if isinstance(entry, dict) else None,
        "models_file_sha256": digest(source / "models.json")
        if (source / "models.json").exists()
        else None,
    }


def log(batch, kind, **data):
    with (batch / "controller.jsonl").open("a") as stream:
        stream.write(json.dumps({"timestamp": now(), "event": kind, **data}) + "\n")


def retry_after(headers, clock=None):
    """Accept HTTP seconds/date or millisecond extension; never shorten a hint."""
    clock = time.time() if clock is None else clock
    delays = []
    for key, scale in (("retry-after", 1), ("retry-after-ms", 0.001)):
        value = headers.get(key)
        if value is None:
            continue
        try:
            delay = float(value) * scale
        except ValueError:
            try:
                delay = (
                    parsedate_to_datetime(value).timestamp() - clock
                    if scale == 1
                    else 0
                )
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay):
            delays.append(max(0, delay))
    return max(delays, default=0)


def error_kind(message, statuses):
    text = message.lower()
    statuses = [
        *statuses,
        *map(
            int,
            re.findall(
                r"(?:^|\bhttp\s+|\bstatus[:= ]+|api error \()([45][0-9]{2})\b", text
            ),
        ),
    ]
    if any(
        word in text
        for word in (
            "insufficient_quota",
            "billing",
            "credit balance",
            "quota exceeded",
            "invalid api key",
            "incorrect api key",
            "authentication",
            "unauthorized",
            "permission denied",
            "usage limit",
            "model not found",
            "not supported",
            "unknown model",
        )
    ) or any(s in (400, 401, 402, 403, 404, 422) for s in statuses):
        return "fatal_error"
    if 429 in statuses or re.search(r"\b429\b|rate.?limit|too many requests", text):
        return "rate_limit"
    if statuses and any(400 <= s < 500 and s not in (408, 409, 425) for s in statuses):
        return "fatal_error"
    return "provider_error"


def expected_system(config):
    return {
        "preamble": config["system_prompt"],
        "cwd": f"<cwd>\n{config['cwd']}\n</cwd>",
    }


def expected_input(config):
    return [
        {
            "role": "developer",
            "content": config["system_prompt"]
            + "\n\n"
            + expected_system(config)["cwd"],
        },
        {"role": "user", "content": [{"type": "input_text", "text": config["prompt"]}]},
    ]


def trial_config(manifest, trial):
    """Only the frozen prompt, model and effort may vary inside a service grid."""
    entry = manifest["schedule"][trial - 1]
    if entry["trial"] != trial:
        raise ValueError("Schedule trial identity mismatch")
    return {
        **manifest["config"],
        **{
            key: entry[key]
            for key in ("prompt", "model", "thinking", "openrouter_profile")
            if key in entry
        },
    }


def parse_attempt(folder, config, returncode, timed_out=False, interrupted=False):
    """Fail closed: a valid-looking digit from an errored request never counts."""
    if config.get("runner", "pi") != "pi":
        import cli_runners

        return cli_runners.parse(folder, config, returncode, timed_out, interrupted)
    result = {
        "outcome": "provider_error",
        "number": None,
        "response": "",
        "retry_after_seconds": 0,
        "usage": None,
        "session_files": [],
    }
    try:
        audit = events(folder / "audit.jsonl")
        stream = events(folder / "events.jsonl")
        sessions = sorted((folder / "sessions").glob("*.jsonl"))
        result["session_files"] = [str(p.relative_to(folder)) for p in sessions]
        native = events(sessions[0]) if len(sessions) == 1 else []
        messages = [e["message"] for e in native if e.get("type") == "message"]
        assistants = [m for m in messages if m.get("role") == "assistant"]
        final = assistants[-1] if assistants else {}
        result["response"] = "".join(
            b.get("text", "")
            for b in final.get("content", [])
            if b.get("type") == "text"
        )
        result["usage"] = final.get("usage")
        if config.get("openrouter_profile"):
            # OpenRouter returns the pinned route's actual usage cost. Pi's
            # generic catalog estimate can describe a different upstream.
            provider_usage = [
                e["usage"]
                for e in audit
                if e.get("type") == "provider_metadata"
                and isinstance(e.get("usage"), dict)
            ]
            result["usage"] = dict(result["usage"] or {})
            result["usage"]["cost"] = {
                "total": next(
                    (u["cost"] for u in reversed(provider_usage) if "cost" in u), None
                )
            }
            cost = result["usage"]["cost"]["total"]
            if cost is not None and (
                type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0
            ):
                result["usage"]["cost"]["total"] = None
                raise ValueError("Invalid OpenRouter reported cost")
        result["response_id"] = final.get("responseId")
        result["session_id"] = next(
            (e.get("id") for e in native if e.get("type") == "session"), None
        )
        http = [e for e in audit if e.get("type") == "http_response"]
        statuses = [e["status"] for e in http]
        result["http_statuses"] = statuses
        result["retry_after_seconds"] = max(
            (retry_after(e.get("headers", {})) for e in http), default=0
        )
        stderr = (folder / "stderr.log").read_text()
        error = "\n".join(filter(None, [final.get("errorMessage", ""), stderr]))
        result["error"] = error[:4000]
        if interrupted or timed_out:
            result["outcome"] = "interrupted" if interrupted else "timeout"
            return result
        if (
            returncode != 0
            or final.get("stopReason") in ("error", "aborted")
            or final.get("errorMessage")
            or any(s >= 400 for s in statuses)
        ):
            result["outcome"] = error_kind(error, statuses)
            return result
        requests = [e for e in audit if e.get("type") == "request"]
        issues = []
        truncated = False
        if len(sessions) != 1 or [m.get("role") for m in messages] != [
            "system",
            "user",
            "assistant",
        ]:
            issues.append(
                "Expected one session with exactly system/user/assistant messages"
            )
        if len(requests) != 1 or len(http) != 1 or statuses != [200]:
            issues.append(
                "Expected one audited provider request and one HTTP 200 response"
            )
        if any(
            e.get("willRetry") or e.get("type", "").startswith("auto_retry")
            for e in stream
        ):
            issues.append("Detected hidden Pi retry")
        if len(messages) == 3:
            if messages[0].get("sections") != expected_system(config) or messages[
                0
            ].get("content"):
                issues.append("Effective system prompt mismatch")
            if messages[1].get("content") != [
                {"type": "text", "text": config["prompt"]}
            ]:
                issues.append("User prompt mismatch")
        if (final.get("provider"), final.get("model"), final.get("thinkingLevel")) != (
            config["provider"],
            config["model"],
            config["thinking"],
        ):
            issues.append("Returned model/provider/reasoning mismatch")
        if any(
            b.get("type") == "toolCall"
            for m in assistants
            for b in m.get("content", [])
        ):
            issues.append("Unexpected tool call")
        if requests:
            payload = requests[0]["payload"]
            if payload.get("model") != config["model"] or payload.get("tools"):
                issues.append("Requested model mismatch or declared tools")
            if not config.get("openrouter_profile") and any(
                k in payload for k in ("temperature", "top_p", "seed")
            ):
                issues.append("Unexpected sampling override")
            if config["provider"] == "openai":
                expected = expected_input(config)
                allowed_update = {
                    "type": "configuration_update",
                    "reasoning": {"effort": config["thinking"]},
                }
                if payload.get("input") not in (expected, [*expected, allowed_update]):
                    issues.append("Effective provider input mismatch")
                if payload.get("reasoning", {}).get("effort") != config["thinking"]:
                    issues.append("Effective provider reasoning mismatch")
            elif config["provider"] == "openrouter" and config.get(
                "openrouter_profile"
            ):
                import openrouter_profiles

                try:
                    observed = openrouter_profiles.audit(
                        config,
                        payload,
                        [e for e in audit if e.get("type") == "provider_metadata"],
                        final,
                    )
                    truncated = observed.pop("truncated")
                    result.update(observed)
                    if result.get("usage") is not None:
                        result["usage"]["reasoning"] = observed.get("reasoning_tokens")
                except ValueError as exc:
                    issues.append(str(exc))
            elif config["provider"] == "openrouter":
                expected = [
                    {
                        "role": "system",
                        "content": config["system_prompt"]
                        + "\n\n"
                        + expected_system(config)["cwd"],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": config["prompt"]}],
                    },
                ]
                if payload.get("messages") != expected:
                    issues.append("Effective OpenRouter messages mismatch")
                if config["thinking"] != "off" or any(
                    k in payload for k in ("reasoning", "reasoning_effort")
                ):
                    issues.append(
                        "Unexpected reasoning parameter for non-reasoning profile"
                    )
                if payload.get("provider") != config["openrouter_routing"]:
                    issues.append("OpenRouter provider pin or fallback policy mismatch")
                metadata = [e for e in audit if e.get("type") == "provider_metadata"]
                if not metadata or any(e.get("error") for e in metadata):
                    issues.append("Missing or errored OpenRouter response metadata")
                models = {e["model"] for e in metadata if e.get("model")}
                providers = {e["provider"] for e in metadata if e.get("provider")}
                ids = {e["id"] for e in metadata if e.get("id")}
                if (
                    models != {config["model"]}
                    or providers != {"OpenAI"}
                    or len(ids) != 1
                ):
                    issues.append(
                        "OpenRouter response model/provider/identity mismatch"
                    )
                result["observed_model"] = (
                    next(iter(models)) if len(models) == 1 else None
                )
                result["upstream_provider"] = (
                    next(iter(providers)) if len(providers) == 1 else None
                )
                result["system_fingerprints"] = sorted(
                    {
                        e["system_fingerprint"]
                        for e in metadata
                        if e.get("system_fingerprint")
                    }
                )
                if any(
                    (e.get("usage") or {})
                    .get("completion_tokens_details", {})
                    .get("reasoning_tokens", 0)
                    for e in metadata
                ):
                    issues.append("Non-reasoning profile reported reasoning tokens")
            else:
                issues.append("Unsupported audited Pi provider")
        if issues:
            result.update(outcome="protocol_error", error="; ".join(issues))
            return result
        if truncated:
            result.update(
                outcome="invalid_response",
                invalid_reason="truncated",
                error="Truncated provider response",
                number=None,
            )
            return result
        text = result["response"].strip()
        if final.get("stopReason") == "stop" and re.fullmatch(
            r"(?:[1-9]|10)", text, re.ASCII
        ):
            result.update(outcome="valid", number=int(text))
        else:
            result.update(
                outcome="invalid_response",
                error="Final response was not a complete bare integer in 1-10",
            )
            if config.get("openrouter_profile"):
                result["invalid_reason"] = "format"
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result.update(
            outcome="interrupted"
            if interrupted
            else "timeout"
            if timed_out
            else "protocol_error",
            error=f"Malformed or missing evidence: {exc}",
        )
    return result


class Controller:
    def __init__(self, config, state=None):
        self.config = config
        self.state = state or {
            "limit": config["concurrency"],
            "cooldown_until": 0,
            "failures": 0,
            "healthy": 0,
            "stopped": None,
            "accounted": [],
        }

    def observe(self, result, clock=None):
        clock = time.time() if clock is None else clock
        s, c = self.state, self.config
        key = f"{result['trial']}/{result['attempt']}"
        if key in s.setdefault("accounted", []):
            return
        s["accounted"].append(key)
        outcome = result["outcome"]
        if outcome == "interrupted":
            return
        if outcome in ("fatal_error", "protocol_error"):
            s["stopped"] = outcome + ": " + result.get("error", "")[:300]
        elif outcome in TRANSIENT:
            s["failures"] += 1
            s["healthy"] = 0
            s["limit"] = max(1, s["limit"] // 2)
            base = min(
                c["max_backoff"], c["base_backoff"] * 2 ** min(s["failures"] - 1, 20)
            )
            # Stable local jitter; never enters the model prompt.
            key = f"{result.get('trial')}/{result.get('attempt')}/{s['failures']}"
            fraction = (
                int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            )
            delay = (
                max(base, result.get("retry_after_seconds", 0)) + base * 0.2 * fraction
            )
            s["cooldown_until"] = max(s["cooldown_until"], clock + delay)
            if s["failures"] >= c["circuit_errors"]:
                s["stopped"] = "Circuit opened after repeated transport failures"
        elif clock >= s["cooldown_until"]:
            s["healthy"] += 1
            if s["healthy"] >= c["recovery_successes"]:
                s["healthy"] = 0
                s["failures"] = 0
                s["limit"] = min(c["concurrency"], s["limit"] + 1)


def records(batch):
    return [read(path) for path in sorted((batch / "attempts").glob("*/*/result.json"))]


def summary(batch, manifest, rows=None):
    rows = records(batch) if rows is None else rows
    by_trial = {}
    for row in rows:
        by_trial.setdefault(row["trial"], []).append(row)
    accepted = []
    for attempts in by_trial.values():
        successes = [r for r in attempts if r["outcome"] == "valid"]
        if len(successes) > 1:
            raise ValueError("Duplicate accepted trial")
        accepted.extend(successes)
    accepted.sort(key=lambda r: r["trial"])
    counts = {str(i): sum(r["number"] == i for r in accepted) for i in range(1, 11)}
    n = len(accepted)
    config = manifest["config"]
    terminal = [
        v[-1]
        for v in by_trial.values()
        if v[-1]["outcome"] in TERMINAL or len(v) >= config["max_attempts"]
    ]
    completed_responses = sum(
        r["outcome"] in ("valid", "invalid_response") for r in terminal
    )
    usage_cost = sum(
        (((r.get("usage") or {}).get("cost") or {}).get("total") or 0) for r in rows
    )
    out = {
        "schema_version": VERSION,
        "updated_at": now(),
        "planned_trials": config["count"],
        "attempted_trials": len(by_trial),
        "terminal_trials": len(terminal),
        "physical_attempts": len(rows),
        "accepted_trials": n,
        "excluded_attempts": len(rows) - n,
        "attempt_outcomes": dict(Counter(r["outcome"] for r in rows)),
        "counts": counts,
        "model_valid_response_rate": n / completed_responses
        if completed_responses
        else None,
        "operational_success_rate": n / len(by_trial) if by_trial else None,
        "distribution_denominator": "One successful, protocol-verified integer per logical trial",
        "tv_to_uniform": sum(abs(v / n - 0.1) for v in counts.values()) / 2
        if n
        else None,
        "pi_reported_cost": usage_cost,
        "cost_basis": "Runner usage metadata, includes failed attempts with usage; not a verified bill",
        "missing_usage_attempts": sum(r.get("usage") is None for r in rows),
        "missing_cost_attempts": sum(
            ((r.get("usage") or {}).get("cost") or {}).get("total") is None
            for r in rows
        ),
        "sequence_note": "Parallel launch order is descriptive, not a serial independence test",
    }
    save(batch / "summary.json", out)
    save(
        batch / "accepted.json",
        [
            {k: r[k] for k in ("trial", "attempt", "number", "session_id", "folder")}
            for r in accepted
        ],
    )
    return out


@contextmanager
def locked(batch):
    with (batch / "runner.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another runner owns this batch") from None
        yield


class PiEnvironment:
    def __init__(self, manifest=None):
        self.manifest = manifest

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="random-number-pi-")
        path = Path(self.tmp.name)
        path.chmod(0o700)
        source = Path(
            os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent"))
        )
        # Credentials stay outside the repo; auth updates remain owned by Pi.
        auth = source / "auth.json"
        if auth.exists():
            (path / "auth.json").symlink_to(auth.resolve())
        if (source / "models.json").exists():
            shutil.copyfile(source / "models.json", path / "models.json")
            (path / "models.json").chmod(0o600)
        if (
            self.manifest
            and self.manifest["config"]["provider"] == "openrouter"
            and not self.manifest["config"].get("openrouter_profile")
        ):
            models = (
                read(path / "models.json") if (path / "models.json").exists() else {}
            )
            provider = models.setdefault("providers", {}).setdefault("openrouter", {})
            overrides = provider.setdefault("modelOverrides", {})
            selected = {
                entry.get("model", self.manifest["config"]["model"])
                for entry in self.manifest["schedule"]
            }
            for model in selected:
                compat = overrides.setdefault(model, {}).setdefault("compat", {})
                compat["openRouterRouting"] = self.manifest["config"][
                    "openrouter_routing"
                ]
            save(path / "models.json", models)
            (path / "models.json").chmod(0o600)
        save(path / "settings.json", SETTINGS)
        self.env = {
            **os.environ,
            "PI_CODING_AGENT_DIR": str(path),
            "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0",
        }
        return self

    def __exit__(self, *_):
        self.tmp.cleanup()


async def terminate(process):
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), 3)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def attempt(batch, manifest, environment, trial, attempt_no, limit, generation):
    scheduled = manifest["schedule"][trial - 1]
    assert scheduled["trial"] == trial
    config = trial_config(manifest, trial)
    folder = batch / "attempts" / f"{trial:05d}" / f"{attempt_no:02d}"
    folder.mkdir(parents=True, exist_ok=False)
    command = [
        *manifest["pi_command"],
        "--offline",
        "--print",
        "--mode",
        "json",
        "--provider",
        config["provider"],
        "--model",
        config["model"],
        "--thinking",
        config["thinking"],
        "--no-tools",
        "--no-mcp",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--system-prompt",
        config["system_prompt"],
        "--extension",
        str(batch / "snapshot/pi-audit.ts"),
        "--session-dir",
        str(folder / "sessions"),
        "--",
        config["prompt"],
    ]
    attempt_env = {**environment, "RANDOM_BENCH_AUDIT": str(folder / "audit.jsonl")}
    if config.get("openrouter_profile"):
        profile_path = folder / "request-profile.json"
        save(profile_path, config["openrouter_profile"])
        attempt_env["RANDOM_BENCH_REQUEST_PROFILE"] = str(profile_path)
        position = command.index("--extension")
        command[position:position] = [
            "--extension",
            str(batch / "snapshot/pi-profile.ts"),
        ]
    if config.get("runner", "pi") != "pi":
        import cli_runners

        binary = manifest["runner_metadata"]["binary"]
        command = cli_runners.command(config, folder, binary=binary)
    start = {
        "trial": trial,
        "attempt": attempt_no,
        "started_at": now(),
        "started_epoch": time.time(),
        "concurrency_limit": limit,
        "generation": generation,
        "folder": str(folder.relative_to(batch)),
        "command": command,
        "runner": config.get("runner", "pi"),
        "evidence_level": "pi_http_request"
        if config.get("runner", "pi") == "pi"
        else "cli_transcript",
    }
    save(folder / "started.json", start)
    process, timed_out, interrupted = None, False, False
    before = time.monotonic()
    try:
        with (
            (folder / "events.jsonl").open("w") as stdout,
            (folder / "stderr.log").open("w") as stderr,
        ):
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=config["cwd"],
                env=attempt_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            start["pid"] = process.pid
            save(folder / "started.json", start)
            try:
                await asyncio.wait_for(process.wait(), config["timeout"])
            except TimeoutError:
                timed_out = True
                await asyncio.shield(terminate(process))
            except asyncio.CancelledError:
                interrupted = True
                await asyncio.shield(terminate(process))
        result = parse_attempt(
            folder, config, process.returncode, timed_out, interrupted
        )
    except asyncio.CancelledError:
        interrupted = True
        if process:
            await asyncio.shield(terminate(process))
        result = {
            "outcome": "interrupted",
            "number": None,
            "error": "Runner interrupted",
            "retry_after_seconds": 0,
        }
    except OSError as exc:
        result = {
            "outcome": "fatal_error",
            "number": None,
            "error": str(exc),
            "retry_after_seconds": 0,
        }
    result.update(
        start,
        finished_at=now(),
        elapsed_seconds=round(time.monotonic() - before, 3),
        returncode=process.returncode if process else None,
    )
    result["evidence_sha256"] = {
        str(p.relative_to(folder)): digest(p)
        for p in folder.rglob("*")
        if p.is_file() and p.name != "result.json"
    }
    save(folder / "result.json", result)
    return result


def recover_unfinished(batch, manifest):
    for folder in sorted((batch / "attempts").glob("*/*")):
        if not folder.is_dir():
            continue
        path = folder / "started.json"
        if (folder / "result.json").exists():
            continue
        start = (
            read(path)
            if path.exists()
            else {
                "trial": int(folder.parent.name),
                "attempt": int(folder.name),
                "folder": str(folder.relative_to(batch)),
                "started_at": None,
                "generation": 0,
            }
        )
        pid = start.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                pass
            else:
                command = subprocess.run(
                    ["ps", "-ww", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
                if str(folder / "sessions") in command or (
                    manifest["config"].get("runner", "pi") != "pi" and command.strip()
                ):
                    raise RuntimeError(
                        f"Unfinished attempt PID {pid} is still alive; refusing duplicate work"
                    )
        config = trial_config(manifest, start["trial"])
        recovered = parse_attempt(folder, config, None, interrupted=True)
        save(
            folder / "result.json",
            {
                **start,
                **recovered,
                "runner": config.get("runner", "pi"),
                "evidence_level": "pi_http_request"
                if config.get("runner", "pi") == "pi"
                else "cli_transcript",
                "outcome": "interrupted",
                "number": None,
                "error": "Recovered unfinished attempt; outcome unknown and excluded",
                "finished_at": now(),
                "evidence_sha256": {
                    str(p.relative_to(folder)): digest(p)
                    for p in folder.rglob("*")
                    if p.is_file() and p.name != "result.json"
                },
            },
        )


async def execute(batch, manifest, env):
    config = manifest["config"]
    recover_unfinished(batch, manifest)
    state_path = batch / "controller.json"
    ctl = Controller(config, read(state_path) if state_path.exists() else None)
    existing = records(batch)
    # A crash can happen after result.json is durable but before the controller
    # checkpoint. Replay only unaccounted results before launching anything.
    for result in sorted(existing, key=lambda r: r["finished_at"]):
        ctl.observe(result)
    save(state_path, ctl.state)
    if ctl.state["stopped"]:
        raise RuntimeError(
            "Batch is stopped: "
            + ctl.state["stopped"]
            + ". Create a new batch after resolving it."
        )
    by_trial = {}
    for result in existing:
        by_trial.setdefault(result["trial"], []).append(result)
    pending = [
        trial
        for trial in range(1, config["count"] + 1)
        if trial not in by_trial
        or (
            by_trial[trial][-1]["outcome"] in TRANSIENT
            and len(by_trial[trial]) < config["max_attempts"]
        )
    ]
    attempts = {trial: len(rows) for trial, rows in by_trial.items()}
    tasks = {}
    run_started = time.monotonic()
    elapsed_before = (
        read(batch / "status.json").get("elapsed_seconds", 0)
        if (batch / "status.json").exists()
        else 0
    )
    generation = max((r.get("generation", 0) for r in existing), default=0) + 1
    draining = False
    last_progress = 0
    report = summary(batch, manifest, existing)
    stop_requested = False
    cancellation_sent = False
    loop = asyncio.get_running_loop()

    def stop():
        nonlocal stop_requested
        stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    log(
        batch,
        "runner_start",
        pid=os.getpid(),
        resumed=bool(existing),
        generation=generation,
    )
    try:
        while pending or tasks:
            elapsed = elapsed_before + time.monotonic() - run_started
            if elapsed >= config["max_seconds"]:
                ctl.state["stopped"] = "Batch wall-time limit reached"
            if report["pi_reported_cost"] >= config["max_cost"]:
                ctl.state["stopped"] = (
                    "Runner metadata cost limit reached (in-flight calls may overshoot)"
                )
            if stop_requested and not cancellation_sent:
                for task in tasks:
                    task.cancel()
                cancellation_sent = True
            if draining and not tasks:
                draining = False
                generation += 1
            can_launch = (
                not stop_requested
                and not ctl.state["stopped"]
                and not draining
                and time.time() >= ctl.state["cooldown_until"]
            )
            while can_launch and pending and len(tasks) < ctl.state["limit"]:
                if config.get("runner", "pi") != "pi":
                    import cli_runners

                    # Stop before allocating an attempt if the installed CLI
                    # changed. Existing calls drain and remain verifiable.
                    metadata = manifest["runner_metadata"]
                    try:
                        unchanged = (
                            cli_runners.sha256_file(metadata["binary"])
                            == metadata["binary_sha256"]
                        )
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        ctl.state["stopped"] = (
                            "CLI executable changed; a new plan requires a new version"
                        )
                        break
                trial = pending.pop(0)
                attempts[trial] = attempts.get(trial, 0) + 1
                task = asyncio.create_task(
                    attempt(
                        batch,
                        manifest,
                        env,
                        trial,
                        attempts[trial],
                        ctl.state["limit"],
                        generation,
                    )
                )
                tasks[task] = trial
                log(
                    batch,
                    "launch",
                    trial=trial,
                    attempt=attempts[trial],
                    limit=ctl.state["limit"],
                    generation=generation,
                )
            if not tasks and (stop_requested or ctl.state["stopped"]):
                break
            if tasks:
                done, _ = await asyncio.wait(
                    tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED
                )
                for task in sorted(done, key=lambda t: tasks[t]):
                    trial = tasks.pop(task)
                    result = task.result()
                    existing.append(result)
                    previous = {k: v for k, v in ctl.state.items() if k != "accounted"}
                    ctl.observe(result)
                    if result["outcome"] in TRANSIENT:
                        draining = True
                        if attempts[trial] < config["max_attempts"]:
                            pending.insert(0, trial)
                    save(state_path, ctl.state)
                    log(
                        batch,
                        "completed",
                        trial=trial,
                        attempt=attempts[trial],
                        outcome=result["outcome"],
                        number=result["number"],
                        previous_controller=previous,
                        controller={
                            k: v for k, v in ctl.state.items() if k != "accounted"
                        },
                    )
                    if result["outcome"] not in ("valid", "invalid_response"):
                        print(
                            f"{trial:05d}/{attempts[trial]} {result['outcome']}; limit={ctl.state['limit']}; "
                            f"cooldown={max(0, ctl.state['cooldown_until'] - time.time()):.1f}s",
                            flush=True,
                        )
                if done:
                    report = summary(batch, manifest, existing)
            else:
                await asyncio.sleep(
                    min(1, max(0.01, ctl.state["cooldown_until"] - time.time()))
                )
            if time.monotonic() - last_progress >= 10:
                last_progress = time.monotonic()
                status = {
                    "state": "running",
                    "elapsed_seconds": elapsed,
                    "active": len(tasks),
                    "pending": len(pending),
                    "controller": ctl.state,
                }
                save(batch / "status.json", status)
                print(
                    f"{report['terminal_trials']}/{config['count']} trials finished; "
                    f"{report['accepted_trials']} accepted; {report['excluded_attempts']} attempts excluded; "
                    f"active={len(tasks)} limit={ctl.state['limit']}",
                    flush=True,
                )
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        report = summary(batch, manifest)
        state = (
            "stopped"
            if ctl.state["stopped"]
            else "paused"
            if stop_requested
            else (
                "complete"
                if report["terminal_trials"] == config["count"]
                else "stopped"
            )
        )
        save(state_path, ctl.state)
        save(
            batch / "status.json",
            {
                "state": state,
                "elapsed_seconds": elapsed_before + time.monotonic() - run_started,
                "controller": ctl.state,
                "finished_at": now(),
            },
        )
        log(batch, "runner_end", state=state, accepted=report["accepted_trials"])
    print(json.dumps(report, indent=2), flush=True)
    return 0 if state == "complete" else 2


def plan(args):
    batch = Path(args.out).resolve()
    batch.mkdir(parents=True, exist_ok=False)
    (batch / "snapshot").mkdir()
    (batch / "attempts").mkdir()
    config = {
        key: getattr(args, key)
        for key in (
            "count",
            "provider",
            "model",
            "thinking",
            "concurrency",
            "max_attempts",
            "timeout",
            "base_backoff",
            "max_backoff",
            "recovery_successes",
            "circuit_errors",
            "max_seconds",
            "max_cost",
        )
    }
    config.update(prompt=args.prompt, system_prompt=SYSTEM, cwd=str(args.cwd.resolve()))
    config["runner"] = args.runner
    if getattr(args, "openrouter_profile", None):
        if args.runner != "pi" or args.provider != "openrouter":
            raise ValueError("Fixed-decoding profile requires Pi/OpenRouter")
        config["openrouter_profile"] = args.openrouter_profile
    elif args.runner == "pi" and args.provider == "openrouter":
        if args.thinking != "off":
            raise ValueError("The OpenRouter profile currently requires thinking off")
        config["openrouter_routing"] = {"only": ["openai"], "allow_fallbacks": False}
    schedule = getattr(args, "trial_schedule", None) or [
        {
            "trial": i,
            "block": (i - 1) // 100 + 1,
            **({"prompt": args.trial_prompts[i - 1]} if args.trial_prompts else {}),
        }
        for i in range(1, args.count + 1)
    ]
    if [row.get("trial") for row in schedule] != list(range(1, args.count + 1)):
        raise ValueError("Trial schedule must cover exactly the planned trials")
    for row in schedule:
        if set(row) - {
            "trial",
            "block",
            "prompt",
            "model",
            "thinking",
            "cell",
            "condition",
            "condition_trial",
            "openrouter_profile",
        }:
            raise ValueError("Unexpected per-trial override")
        if "prompt" in row and (
            not isinstance(row["prompt"], str) or not row["prompt"].strip()
        ):
            raise ValueError("Invalid per-trial prompt")
    if config.get("openrouter_profile"):
        import openrouter_profiles

        for row in schedule:
            selected = trial_config(
                {"config": config, "schedule": schedule}, row["trial"]
            )
            openrouter_profiles.validate_profile(
                selected["openrouter_profile"], selected["model"], selected["thinking"]
            )
    command = (
        json.loads(args.pi_command) if args.pi_command else [shutil.which("pi") or "pi"]
    )
    runner_metadata = None
    if args.runner == "pi":
        version = subprocess.check_output(
            [*command, "--offline", "--version"], text=True
        ).strip()
    else:
        import cli_runners

        cli_runners.validate_config(config)
        for row in schedule:
            cli_runners.validate_config(
                trial_config({"config": config, "schedule": schedule}, row["trial"])
            )
        runner_metadata = cli_runners.preflight(args.runner, args.model)
        version = runner_metadata["version"]
    files = ["batch.py", "pi-audit.ts", "verify.py"]
    if getattr(args, "grid_spec", None):
        files.append("grid.py")
    if config.get("openrouter_profile"):
        files.extend(["openrouter_profiles.py", "pi-profile.ts"])
    if args.runner != "pi":
        files.append("cli_runners.py")
    for name in files:
        shutil.copyfile(ROOT / name, batch / "snapshot" / name)
    manifest = {
        "schema_version": VERSION,
        "created_at": now(),
        "config": config,
        "pi_command": command,
        "pi_version": version if args.runner == "pi" else None,
        "runner_version": version,
        "runner_metadata": runner_metadata,
        "pi_settings": SETTINGS if args.runner == "pi" else {},
        "provider_metadata": local_provider_metadata(args.provider)
        if args.runner == "pi"
        else {"stored_auth_type": runner_metadata["auth_type"]},
        "source_sha256": {name: digest(ROOT / name) for name in files},
        "schedule": schedule,
        "prompt_policy": "Frozen per-trial prompts"
        if args.trial_prompts or getattr(args, "trial_schedule", None)
        else "One fixed prompt",
        "sampling": "No temperature/top_p/seed override; Pi verifies payload defaults, native CLI backend defaults are opaque",
        "error_policy": "Transport failures excluded; bounded retries within logical trial; invalid answers never retried",
    }
    if getattr(args, "grid_spec", None):
        manifest["grid"] = args.grid_spec
    if config.get("openrouter_profile"):
        manifest["sampling"] = (
            "Explicit fixed decoding and reasoning settings per frozen OpenRouter trial profile; request audit verifies their application"
        )
    save(batch / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "batch": str(batch),
                "config": config,
                "max_physical_attempts": args.count * args.max_attempts,
                "runner": args.runner,
                "runner_version": version,
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("plan", help="Freeze a batch without model calls")
    p.add_argument("--out", required=True)
    for name, default, kind in (
        ("count", 1000, int),
        ("concurrency", 4, int),
        ("max-attempts", 3, int),
        ("timeout", 120, float),
        ("base-backoff", 15, float),
        ("max-backoff", 300, float),
        ("recovery-successes", 20, int),
        ("circuit-errors", 8, int),
        ("max-seconds", 7200, float),
        ("max-cost", 10, float),
    ):
        p.add_argument("--" + name, type=kind, default=default)
    p.add_argument("--provider")
    p.add_argument(
        "--runner", choices=["pi", "claude", "codex", "grok", "agy"], default="pi"
    )
    p.add_argument("--model")
    p.add_argument("--thinking", default="medium")
    p.add_argument(
        "--cwd",
        type=Path,
        default=ROOT,
        help="Fixed working directory included by Pi in its system prompt",
    )
    prompts = p.add_mutually_exclusive_group()
    prompts.add_argument(
        "--prompt", default=PROMPT, help="Exact user prompt to freeze in the manifest"
    )
    prompts.add_argument(
        "--trial-prompts",
        type=Path,
        help="JSON list with one exact prompt per logical trial; frozen before execution",
    )
    p.add_argument(
        "--pi-command",
        help="JSON argv prefix; for offline fake-provider integration tests",
    )
    for action in ("run", "summarize"):
        sub.add_parser(action).add_argument("batch", type=Path)
    args = parser.parse_args()
    if args.action == "plan":
        provider, model = RUNNER_DEFAULTS[args.runner]
        args.provider = args.provider or provider
        args.model = args.model or model
        if args.runner != "pi" and args.pi_command:
            parser.error("--pi-command requires --runner pi")
        numeric = [
            args.count,
            args.concurrency,
            args.max_attempts,
            args.timeout,
            args.base_backoff,
            args.max_backoff,
            args.recovery_successes,
            args.circuit_errors,
            args.max_seconds,
            args.max_cost,
        ]
        if any(not math.isfinite(x) or x <= 0 for x in numeric):
            parser.error("All counts, limits and delays must be finite and positive")
        if not args.prompt.strip():
            parser.error("Prompt must not be empty")
        if args.trial_prompts is not None:
            try:
                args.trial_prompts = read(args.trial_prompts)
            except (OSError, ValueError) as exc:
                parser.error(f"Cannot read trial prompts: {exc}")
            if (
                not isinstance(args.trial_prompts, list)
                or len(args.trial_prompts) != args.count
                or any(
                    not isinstance(p, str) or not p.strip() for p in args.trial_prompts
                )
            ):
                parser.error(
                    "Trial prompts must be a JSON list of exactly --count nonempty strings"
                )
        plan(args)
        return 0
    batch = args.batch.resolve()
    manifest = read(batch / "manifest.json")
    with locked(batch):
        if args.action == "summarize":
            print(json.dumps(summary(batch, manifest), indent=2))
            return 0
        for name, sha in manifest["source_sha256"].items():
            if digest(ROOT / name) != sha or digest(batch / "snapshot" / name) != sha:
                raise RuntimeError(
                    "Runner snapshot changed; use the frozen snapshot/batch.py to resume"
                )
        if manifest["config"].get("runner", "pi") != "pi":
            import cli_runners

            runner = manifest["config"]["runner"]
            actual = cli_runners.preflight(runner, manifest["config"]["model"])
            frozen = manifest["runner_metadata"]
            if any(
                actual[k] != frozen[k]
                for k in ("runner", "version", "auth_type", "binary", "binary_sha256")
            ):
                raise RuntimeError(
                    "CLI version or subscription authentication changed since planning"
                )
            cli_runners.prepare(runner)
            return asyncio.run(
                execute(batch, manifest, cli_runners.environment(runner))
            )
        actual_version = subprocess.check_output(
            [*manifest["pi_command"], "--offline", "--version"], text=True
        ).strip()
        if actual_version != manifest["pi_version"]:
            raise RuntimeError("Pi version changed since planning")
        if (
            local_provider_metadata(manifest["config"]["provider"])
            != manifest["provider_metadata"]
        ):
            raise RuntimeError(
                "Provider model configuration or credential class changed since planning"
            )
        with PiEnvironment(manifest) as environment:
            return asyncio.run(execute(batch, manifest, environment.env))


if __name__ == "__main__":
    raise SystemExit(main())
