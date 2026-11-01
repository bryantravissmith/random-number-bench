# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Subscription-backed CLI benchmark runners: Claude, Codex, Grok and AGY.

This module is a launcher and fail-closed transcript parser for one-turn,
tool-free benchmark attempts through vendor CLIs that authenticate with a
consumer subscription instead of an API key. It never executes inference by
itself; the scheduler builds the argv with ``command`` and runs it with the
``environment`` returned here, capturing stdout to ``events.jsonl`` and stderr
to ``stderr.log`` inside the attempt folder. ``parse`` turns that native
transcript into the same outcome vocabulary ``batch.py`` uses for Pi.

Evidence level is ``cli_transcript``: the CLI's own structured output. There
is no HTTP audit hook, so CLI-internal retries cannot be observed and
``http_statuses`` is always empty.

Launch flags were checked against installed CLI help. Transcript schemas are
fixture tested and live smoke checked where a subscription login is available.
Unknown transcript shapes fail closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

RUNNERS = ("claude", "codex", "grok", "agy")
PROVIDERS = {
    "claude": "anthropic",
    "codex": "openai",
    "grok": "xai",
    "agy": "antigravity",
}
FORMATS = {
    "claude": "claude-stream-json",
    "codex": "codex-exec-jsonl",
    "grok": "claude-stream-json",
    "agy": "agy-json",
}
EFFORTS = {
    "claude": ("off", "low", "medium", "high", "xhigh", "max"),
    "codex": ("minimal", "low", "medium", "high", "xhigh", "max"),
    "grok": ("low", "medium", "high"),
    "agy": ("low", "medium", "high"),
}
# Models whose thinking-off setting is supported by Claude Code, not merely
# the API. Newer Haiku/Sonnet 5.5 and Opus 5.5 ignore the CLI's off setting.
CLAUDE_THINKING_OFF_MODELS = {"claude-haiku-4-5"}
EVIDENCE_LEVEL = "cli_transcript"
OUTCOMES = (
    "valid",
    "invalid_response",
    "rate_limit",
    "provider_error",
    "timeout",
    "interrupted",
    "fatal_error",
    "protocol_error",
)
REQUEST_FILE = "cli-request.json"
REQUEST_KEYS = (
    "runner",
    "model",
    "provider",
    "thinking",
    "system_prompt",
    "cwd",
    "timeout",
    "prompt",
)
NUMBER = re.compile(r"(?:[1-9]|10)", re.ASCII)
DATED_SUFFIX = re.compile(r"-\d{8}$")

# Environment variables that would route a runner away from stored
# subscription credentials: API keys, base URLs, cloud providers, model
# overrides. Presence alone is rejected; this is a fail-closed launcher.
REJECTED_ENV = {
    "claude": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "CLAUDE_CODE_SKIP_VERTEX_AUTH",
        "AWS_BEARER_TOKEN_BEDROCK",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_EFFORT_LEVEL",
        "MAX_THINKING_TOKENS",
        "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
        "CLAUDE_CODE_EXTRA_BODY",
    ),
    "codex": (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
    ),
    "grok": (
        "XAI_API_KEY",
        "GROK_API_KEY",
        "XAI_BASE_URL",
        "XAI_API_BASE_URL",
        "GROK_BASE_URL",
    ),
    "agy": (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GEMINI_API_BASE_URL",
        "GOOGLE_GEMINI_BASE_URL",
        "ANTIGRAVITY_API_KEY",
        "AGY_API_KEY",
    ),
}
# Identity of an enclosing Claude Code session; nested launches must not
# inherit it for any runner.
NESTED_CLAUDE_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_SAFE_MODE",
    "CLAUDE_CODE_AGENT_ID",
    "CLAUDE_CODE_PARENT_SESSION_ID",
    "CLAUDE_AGENT_SDK_VERSION",
)
SAFE_ENV = {
    "claude": {
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    },
    "codex": {},
    "grok": {
        **{
            f"GROK_{source}_{item}_ENABLED": "false"
            for source in ("CLAUDE", "CURSOR")
            for item in ("AGENTS", "HOOKS", "MCPS", "RULES", "SKILLS")
        },
        "GROK_MEMORY": "false",
        "GROK_DISABLE_AUTOUPDATER": "1",
    },
    "agy": {},
}
COMMON_SAFE_ENV = {"NO_COLOR": "1"}

# Isolated project permissions use the installed AntiGravity project schema.
AGY_PROJECT = "random-number-bench-no-tools"
AGY_PROJECT_SETTINGS = {
    "id": AGY_PROJECT,
    "name": AGY_PROJECT,
    "projectResources": {},
    "permissionGrants": {
        "v2Migrated": True,
        "permissionGrants": {
            "allow": [],
            "deny": [
                "command(*)",
                "unsandboxed(*)",
                "write_file(*)",
                "read_file(*)",
                "execute_url(*)",
                "read_url(*)",
                "search_web(*)",
                "mcp(*)",
            ],
        },
    },
}

CODEX_TOOL_ITEMS = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
    "patch",
    "tool_call",
}
CLAUDE_TOOL_BLOCKS = {"tool_use", "server_tool_use", "tool_result", "mcp_tool_use"}
CLAUDE_TEXT_BLOCKS = {"text"}
CLAUDE_IGNORED_BLOCKS = {"thinking", "redacted_thinking"}


class RunnerError(RuntimeError):
    """Launcher refused to proceed or evidence failed verification."""


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as stream:
        json.dump(obj, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    """Parse a JSONL transcript; any non-object line is a format violation."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise RunnerError(f"Line {number} is not JSON: {exc}") from None
        if not isinstance(row, dict):
            raise RunnerError(f"Line {number} is not a JSON object")
        rows.append(row)
    return rows


def _check_runner(runner):
    if runner not in RUNNERS:
        raise RunnerError(f"Unknown runner {runner!r}; expected one of {RUNNERS}")


def _binary(runner, env):
    path = shutil.which(runner, path=env.get("PATH", os.defpath))
    if not path:
        raise RunnerError(f"{runner} CLI is not on PATH")
    return str(Path(path).resolve())


def _run(argv, env, timeout=60, cwd=None):
    """Run a non-inference CLI inspection command; output is never logged raw."""
    try:
        return subprocess.run(
            argv,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"{argv[0]} {argv[1:2]} failed to run: {exc}") from None


def _model_listed(model, text):
    return bool(re.search(r"(?<![\w.-])" + re.escape(model) + r"(?![\w.-])", text))


# ---------------------------------------------------------------------------
# Environment and preflight
# ---------------------------------------------------------------------------


def environment(runner, base=None):
    """Copy of ``base`` (default ``os.environ``) safe for a runner launch.

    Rejects API-key, base-URL and cloud-provider routes, clears nested Claude
    identity, applies telemetry-off settings. HOME and CODEX_HOME are never
    changed.
    """
    _check_runner(runner)
    source = dict(os.environ if base is None else base)
    present = [key for key in REJECTED_ENV[runner] if key in source]
    if present:
        raise RunnerError(
            f"{runner} launch refused: credential, routing or model-control variables set: "
            + ", ".join(sorted(present))
        )
    env = {
        key: value
        for key, value in source.items()
        if key not in NESTED_CLAUDE_ENV and not key.startswith("CLAUDECODE")
    }
    env.update(COMMON_SAFE_ENV)
    env.update(SAFE_ENV[runner])
    return env


def _claude_auth(binary, env):
    done = _run([binary, "auth", "status", "--json"], env)
    try:
        status = json.loads((done.stdout or "").strip())
    except ValueError:
        raise RunnerError(
            "claude auth status did not return JSON; refusing to guess auth"
        ) from None
    if not isinstance(status, dict):
        raise RunnerError("claude auth status returned a non-object")
    method = str(status.get("authMethod", "")).lower()
    provider = str(status.get("apiProvider", "")).lower()
    if done.returncode != 0 or not status.get("loggedIn"):
        raise RunnerError("claude is not logged in")
    if method not in ("oauth", "oauth_token", "claude.ai") or provider != "firstparty":
        raise RunnerError(
            f"claude auth is {method or 'unknown'}/{provider or 'unknown'}; "
            "require OAuth firstParty"
        )
    return {
        "auth_type": "oauth",
        "auth_method": status.get("authMethod"),
        "api_provider": status.get("apiProvider"),
        "subscription_type": status.get("subscriptionType"),
    }


def _codex_auth(binary, env):
    done = _run([binary, "login", "status"], env)
    text = ((done.stdout or "") + "\n" + (done.stderr or "")).lower()
    if done.returncode != 0 or "not logged in" in text:
        raise RunnerError("codex is not logged in")
    if "api key" in text or "chatgpt" not in text:
        raise RunnerError("codex login is not a ChatGPT subscription login")
    return {"auth_type": "chatgpt", "login_status": "chatgpt"}


def _grok_auth(binary, env, model):
    home = Path(env.get("HOME", str(Path.home())))
    auth_path = home / ".grok" / "auth.json"
    if not auth_path.exists():
        raise RunnerError("grok has no stored auth; sign in first")
    try:
        stored = read_json(auth_path)
    except ValueError:
        raise RunnerError("grok auth.json is not valid JSON") from None
    entries = (
        [stored]
        if isinstance(stored, dict) and "auth_mode" in stored
        else list(stored.values())
        if isinstance(stored, dict)
        else []
    )
    modes = [
        str(entry.get("auth_mode", "")).lower()
        for entry in entries
        if isinstance(entry, dict)
    ]
    if not modes or any(mode not in ("oauth", "oidc") for mode in modes):
        raise RunnerError(
            "grok stored auth must contain only OAuth credentials; run grok login --oauth"
        )
    mode = "oauth"

    done = _run([binary, "models"], env)
    if done.returncode != 0 or re.search(
        r"not authenticated|no auth credentials", done.stdout + done.stderr, re.I
    ):
        raise RunnerError("grok models failed; account is not ready (signed out?)")
    info = {"auth_type": "oauth", "auth_mode": mode, "models_ready": True}
    if model is not None:
        if not _model_listed(model, done.stdout or ""):
            raise RunnerError(f"grok models does not list {model!r}")
        info["model_listed"] = True
    return info


def _agy_auth(binary, env, model):
    done = _run([binary, "models"], env)
    if done.returncode != 0 or not re.search(r"(?m)^gemini-", done.stdout):
        raise RunnerError("agy models failed; Antigravity account is not ready")
    info = {"auth_type": "antigravity_account", "models_ready": True}
    if model is not None:
        if not _model_listed(model, done.stdout or ""):
            raise RunnerError(f"agy models does not list {model!r}")
        info["model_listed"] = True
    return info


def preflight(runner, model=None, env=None):
    """Redacted runner metadata; raises RunnerError on any non-subscription route."""
    _check_runner(runner)
    env = environment(runner, env)
    binary = _binary(runner, env)
    digest = sha256_file(binary)
    if runner == "agy":
        version = "sha256:" + digest
    else:
        done = _run([binary, "--version"], env)
        if done.returncode != 0:
            raise RunnerError(f"{runner} --version failed with {done.returncode}")
        version = (done.stdout or "").strip().splitlines()[0] if done.stdout else ""
        if not version:
            raise RunnerError(f"{runner} --version printed nothing")
    if runner == "claude":
        auth = _claude_auth(binary, env)
    elif runner == "codex":
        auth = _codex_auth(binary, env)
    elif runner == "grok":
        auth = _grok_auth(binary, env, model)
    else:
        auth = _agy_auth(binary, env, model)
    return {
        "runner": runner,
        "provider": PROVIDERS[runner],
        "binary": binary,
        "binary_sha256": digest,
        "version": version,
        "model": model,
        "format": FORMATS[runner],
        "evidence_level": EVIDENCE_LEVEL,
        "checked_at": now(),
        **auth,
    }


# ---------------------------------------------------------------------------
# Project preparation and command construction
# ---------------------------------------------------------------------------


def prepare(runner, root=None):
    """Create only our dedicated AGY project; refuse unrelated contents."""
    _check_runner(runner)
    if runner != "agy":
        return None
    root = Path(root) if root is not None else Path.home() / ".gemini/config/projects"
    path = root / f"{AGY_PROJECT}.json"
    if path.exists():
        if read_json(path) != AGY_PROJECT_SETTINGS:
            raise RunnerError("Dedicated AGY project differs from expected permissions")
    else:
        root.mkdir(parents=True, exist_ok=True)
        save(path, AGY_PROJECT_SETTINGS)
        path.chmod(0o600)
    return path


def validate_config(config):
    missing = [key for key in REQUEST_KEYS if key not in config]
    if missing:
        raise RunnerError("Config is missing " + ", ".join(missing))
    runner = config["runner"]
    _check_runner(runner)
    if config["provider"] != PROVIDERS[runner]:
        raise RunnerError(
            f"{runner} provider must be {PROVIDERS[runner]!r}, got {config['provider']!r}"
        )
    model = config["model"]
    if not isinstance(model, str) or not model.strip() or model != model.strip():
        raise RunnerError("Model must be an exact non-empty identifier")
    if config["thinking"] is not None and config["thinking"] not in EFFORTS[runner]:
        raise RunnerError(f"{runner} thinking must be one of {EFFORTS[runner]} or None")
    if runner == "claude" and config["thinking"] == "off":
        if DATED_SUFFIX.sub("", model) not in CLAUDE_THINKING_OFF_MODELS:
            raise RunnerError(
                "Claude Code thinking-off is not supported for this model"
            )
    for key in ("system_prompt", "prompt"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise RunnerError(f"{key} must be a non-empty string")
    if not isinstance(config["cwd"], str) or not Path(config["cwd"]).is_absolute():
        raise RunnerError("cwd must be an absolute path string")
    if not isinstance(config["timeout"], (int, float)) or config["timeout"] <= 0:
        raise RunnerError("timeout must be positive")


def _argv(config, folder, binary):
    runner = config["runner"]
    folder = Path(folder)
    model, thinking = config["model"], config["thinking"]
    if runner == "claude":
        # Verified against `claude --help`: fresh print session, no tools, no
        # MCP, no settings sources, no customizations, no persistence.
        argv = [
            binary,
            "--print",
            "--safe-mode",
            "--tools",
            "",
            "--mcp-config",
            json.dumps({"mcpServers": {}}),
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--disable-slash-commands",
            "--no-chrome",
            "--max-turns",
            "1",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--system-prompt",
            config["system_prompt"],
        ]
        if thinking == "off":
            argv += ["--settings", json.dumps({"alwaysThinkingEnabled": False})]
            # Haiku 4.5 has no effort parameter. Off is a separate thinking
            # control, not a synonym for low effort or an omitted option.
        elif thinking is not None:
            argv += ["--effort", thinking]
        return argv + ["--", config["prompt"]]
    if runner == "codex":
        # Documented `codex exec --json`; config overrides use TOML literals.
        overrides = {
            "approval_policy": "never",
            "web_search": "disabled",
            "model_provider": "openai",
            "history.persistence": "none",
            "model_instructions_file": str(folder / "instructions.md"),
        }
        if thinking is not None:
            overrides["model_reasoning_effort"] = thinking
        argv = [
            binary,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--model",
            model,
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            config["cwd"],
            "--output-last-message",
            str(folder / "last-message.txt"),
        ]
        for feature in (
            "shell_tool",
            "unified_exec",
            "browser_use",
            "browser_use_external",
            "in_app_browser",
            "computer_use",
            "image_generation",
            "apps",
            "plugins",
            "view_image",
            "tool_suggest",
            "skill_search",
            "goals",
            "sleep_tool",
            "multi_agent",
            "multi_agent_v2",
            "hooks",
            "memories",
        ):
            argv += ["--disable", feature]
        for key, value in overrides.items():
            argv += ["-c", f"{key}={json.dumps(value)}"]
        return argv + ["--", config["prompt"]]
    if runner == "grok":
        argv = [
            binary,
            "-p",
            config["prompt"],
            "--output-format",
            "streaming-messages-json",
            "--model",
            model,
            "--cwd",
            config["cwd"],
            "--system-prompt-override",
            config["system_prompt"],
            "--tools",
            "run_terminal_cmd",
            "--disallowed-tools",
            "run_terminal_cmd,Agent,search_tool,use_tool",
            "--no-plan",
            "--no-subagents",
            "--disable-web-search",
            "--permission-mode",
            "dontAsk",
            "--verbatim",
            "--max-turns",
            "1",
        ]
        if thinking is not None:
            argv += ["--reasoning-effort", thinking]
        return argv
    argv = [
        binary,
        "--print",
        config["prompt"],
        "--model",
        model,
        "--output-format",
        "json",
        "--project",
        AGY_PROJECT,
        "--sandbox",
        "--disable-slash-commands",
        "--print-timeout",
        f"{config['timeout']}s",
    ]
    if thinking is not None:
        argv += ["--effort", thinking]
    return argv


def command(config, folder, binary=None):
    """Build the subprocess argv and freeze cli-request.json in ``folder``."""
    validate_config(config)
    runner = config["runner"]
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if binary is None:
        binary = _binary(runner, os.environ)
    if runner == "codex":
        (folder / "instructions.md").write_text(config["system_prompt"])
    argv = _argv(config, folder, binary)
    request = {key: config[key] for key in REQUEST_KEYS}
    request.update(
        format=FORMATS[runner],
        evidence_level=EVIDENCE_LEVEL,
        binary=binary,
        command=argv,
        environment_overrides={},
        session_policy="fresh CLI process/conversation, one supplied prompt, no resume or fallback; tools disabled/denied",
        created_at=now(),
    )
    save(folder / REQUEST_FILE, request)
    return argv


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def error_kind(message, statuses=()):
    """Classify error text the way batch.error_kind does, without HTTP audit."""
    text = (message or "").lower()
    statuses = [
        *statuses,
        *map(
            int,
            re.findall(
                r"(?:^|\bhttp\s+|\bstatus[:= ]+|api error[:( ]+)([45][0-9]{2})\b", text
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
            "usage_limit",
            "not logged in",
            "please run /login",
            "model not found",
            "not supported",
            "unknown model",
            "unknown option",
            "unexpected argument",
            "code mode is unavailable",
            "code mode is unavailable",
        )
    ) or any(s in (400, 401, 402, 403, 404, 422) for s in statuses):
        return "fatal_error"
    if 429 in statuses or re.search(
        r"\b429\b|rate.?limit|too many requests|overloaded", text
    ):
        return "rate_limit"
    if statuses and any(400 <= s < 500 and s not in (408, 409, 425) for s in statuses):
        return "fatal_error"
    return "provider_error"


def retry_after_seconds(text):
    """Explicit retry hints in text; never shortens, zero when absent."""
    best = 0.0
    hints = re.findall(
        r"(?:retry[- _]?after|try again in|resets? in)[^0-9]{0,24}"
        r"((?:\d+(?:\.\d+)?\s*(?:milliseconds?|minutes?|seconds?|hours?|ms|mins?|secs?|[smh])?\s*)+)",
        (text or "").lower(),
    )
    scales = {
        "ms": 0.001,
        "millisecond": 0.001,
        "milliseconds": 0.001,
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hour": 3600,
        "hours": 3600,
    }
    for hint in hints:
        total = sum(
            float(value) * scales.get(unit, 1)
            for value, unit in re.findall(
                r"(\d+(?:\.\d+)?)\s*(milliseconds?|minutes?|seconds?|hours?|ms|mins?|secs?|[smh])?",
                hint,
            )
        )
        best = max(best, total)

    return best


def _usage(
    input=None,
    output=None,
    cache_read=None,
    cache_write=None,
    reasoning=None,
    cost=None,
):
    usage = {
        "input": input,
        "output": output,
        "cacheRead": cache_read,
        "cacheWrite": cache_write,
        "reasoning": reasoning,
    }
    if cost is not None:
        usage["cost"] = {"total": cost}
    return usage


def _model_matches(requested, observed):
    if observed is None:
        return True
    if observed == requested:
        return True
    return DATED_SUFFIX.sub("", observed) == requested


def _blank_result(runner):
    return {
        "outcome": "protocol_error",
        "number": None,
        "response": "",
        "error": "",
        "retry_after_seconds": 0,
        "usage": None,
        "session_id": None,
        "response_id": None,
        "observed_model": None,
        "evidence_level": EVIDENCE_LEVEL,
        "format": FORMATS[runner],
        "http_statuses": [],
        "audit_note": "CLI-internal HTTP retries are not observable at this evidence level",
    }


class _Transcript:
    """Normalized view of one native transcript, format-specific extraction."""

    def __init__(self):
        self.response = ""
        self.errors = []
        self.issues = []
        self.tool_observed = False
        self.terminal = False
        self.usage = None
        self.session_id = None
        self.response_id = None
        self.observed_model = None
        self.invalid_reason = None
        self.complete = False  # terminal event says the turn finished cleanly


def _extract_claude(events, config, transcript):
    inits = [
        e for e in events if e.get("type") == "system" and e.get("subtype") == "init"
    ]
    assistants = [e for e in events if e.get("type") == "assistant"]
    results = [e for e in events if e.get("type") == "result"]
    users = [e for e in events if e.get("type") == "user"]
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "api_retry":
            status = event.get("error_status")
            transcript.errors.append(
                "CLI internal retry observed; original attempt excluded: "
                + (f"HTTP {status} " if type(status) is int else "")
                + str(event.get("error", "unknown"))
            )
            continue
        if (
            config["runner"] == "claude"
            and event.get("type") == "system"
            and event.get("subtype") == "thinking_tokens"
        ):
            # Native progress estimates are neither output nor final usage.
            # Preserve them in evidence, but tally the terminal usage only.
            allowed = {
                "type",
                "subtype",
                "session_id",
                "uuid",
                "estimated_tokens",
                "estimated_tokens_delta",
            }
            if set(event) - allowed or any(
                type(event.get(key)) is not int or event[key] < 0
                for key in ("estimated_tokens", "estimated_tokens_delta")
            ):
                transcript.issues.append("Malformed thinking-token progress event")
            if config["thinking"] == "off":
                transcript.issues.append(
                    "Thinking progress observed with thinking disabled"
                )
            continue
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if info.get("isUsingOverage"):
                transcript.errors.append("Billing overage reported by CLI")
            if info.get("status") not in ("allowed", "allowed_warning"):
                transcript.errors.append(
                    "Subscription usage limit: " + str(info.get("status"))
                )
            continue
        if event.get("type") == "system" and event.get("subtype") not in (
            "init",
            "ui_invalidate",
        ):
            transcript.issues.append(
                f"Unexpected system event {event.get('subtype')!r}"
            )
        if event.get("type") not in ("system", "assistant", "user", "result"):
            transcript.issues.append(f"Unknown event type {event.get('type')!r}")
    if len(inits) != 1:
        transcript.issues.append("Expected exactly one system init event")
    init = inits[0] if inits else {}
    if init:
        if init.get("tools"):
            transcript.issues.append("Tools were available to the session")
        if init.get("mcp_servers"):
            transcript.issues.append("MCP servers were attached to the session")
        source = init.get("apiKeySource")
        if source not in (
            (None, "none", "oauth") if config["runner"] == "grok" else (None, "none")
        ):
            transcript.issues.append(f"Session used apiKeySource {source!r}")
        if init.get("model") != config["model"]:
            transcript.issues.append(
                f"CLI resolved model {init.get('model')!r} != requested {config['model']!r}"
            )
        transcript.session_id = init.get("session_id")
    for event in users:
        content = (event.get("message") or {}).get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") in CLAUDE_TOOL_BLOCKS for b in content
        ):
            transcript.tool_observed = True
    texts = []
    stop_reasons = []
    for event in assistants:
        message = event.get("message") or {}
        if event.get("error"):
            transcript.errors.append(f"assistant error: {event['error']}")
        if message.get("model") is not None:
            if not _model_matches(config["model"], message["model"]):
                transcript.issues.append("Assistant message used an unrequested model")
            transcript.observed_model = message["model"]
        transcript.response_id = message.get("id", transcript.response_id)
        stop_reasons.append(message.get("stop_reason"))
        for block in message.get("content") or []:
            kind = block.get("type") if isinstance(block, dict) else None
            if kind in CLAUDE_TOOL_BLOCKS:
                transcript.tool_observed = True
            elif kind in CLAUDE_TEXT_BLOCKS:
                texts.append(block.get("text", ""))
            elif kind in CLAUDE_IGNORED_BLOCKS:
                if config["thinking"] == "off":
                    transcript.issues.append(
                        "Thinking block observed with thinking disabled"
                    )
                continue
            else:
                transcript.issues.append(f"Unexpected content block {kind!r}")
    transcript.response = "".join(texts)
    if transcript.response.lstrip().lower().startswith("api error"):
        transcript.errors.append(transcript.response)
    if len(results) != 1:
        transcript.issues.append("Expected exactly one result event")
        return
    result = results[0]
    ids = {e["session_id"] for e in events if e.get("session_id")}
    if len(ids) > 1:
        transcript.issues.append("Transcript contains multiple sessions")
    transcript.terminal = True
    transcript.session_id = result.get("session_id", transcript.session_id)
    if result.get("permission_denials"):
        transcript.tool_observed = True
    if result.get("is_error") or result.get("subtype") != "success":
        errors = result.get("errors") or []
        text = "; ".join(str(e) for e in errors) or str(result.get("result", ""))
        transcript.errors.append(f"result {result.get('subtype')}: {text}")
    usage = result.get("usage") or {}
    transcript.usage = _usage(
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cache_creation_input_tokens"),
        (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
        None if result.get("cost_is_partial") else result.get("total_cost_usd"),
    )
    if config["thinking"] == "off" and (transcript.usage.get("reasoning") or 0) > 0:
        transcript.issues.append("Thinking tokens reported with thinking disabled")
    if transcript.errors:
        return
    if result.get("num_turns") != 1:
        transcript.issues.append(
            f"Expected one model turn, saw {result.get('num_turns')}"
        )
    message_ids = {(e.get("message") or {}).get("id") for e in assistants}
    if len(message_ids) != 1 or None in message_ids:
        transcript.issues.append("Expected one assistant message identity")
    if stop_reasons and stop_reasons[-1] not in (None, "end_turn"):
        transcript.issues.append(f"Unexpected stop reason {stop_reasons[-1]!r}")
    used_models = result.get("modelUsage") or {}
    if used_models and not any(
        _model_matches(config["model"], used)
        or (config["runner"] == "grok" and used == config["model"] + "-build")
        for used in used_models
    ):
        transcript.issues.append("No usage reported for the requested primary model")
    if result.get("result") != transcript.response:
        transcript.invalid_reason = (
            "Final result text differs from assistant text; no answer counted"
        )
    transcript.complete = not transcript.issues


def _extract_codex(events, config, transcript):
    messages = []
    completed = []
    for event in events:
        kind = event.get("type")
        if kind == "thread.started":
            transcript.session_id = event.get("thread_id")
            if event.get("model") is not None:
                transcript.observed_model = event["model"]
        elif kind in ("turn.started", "item.started", "item.updated"):
            item = event.get("item") or {}
            if item.get("type") in CODEX_TOOL_ITEMS:
                transcript.tool_observed = True
        elif kind == "item.completed":
            item = event.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message":
                messages.append(item.get("text", ""))
                transcript.response_id = item.get("id", transcript.response_id)
            elif item_type == "reasoning":
                continue
            elif item_type == "error":
                transcript.errors.append(f"item error: {item.get('message')}")
            elif item_type in CODEX_TOOL_ITEMS:
                transcript.tool_observed = True
            else:
                transcript.issues.append(f"Unexpected item type {item_type!r}")
        elif kind == "turn.completed":
            completed.append(event)
        elif kind == "turn.failed":
            transcript.errors.append(
                f"turn failed: {(event.get('error') or {}).get('message', event.get('error'))}"
            )
        elif kind == "error":
            transcript.errors.append(f"error: {event.get('message')}")
        else:
            transcript.issues.append(f"Unknown event type {kind!r}")
    transcript.response = "".join(messages)
    if len(completed) != 1:
        if not transcript.errors:
            transcript.issues.append("Expected exactly one turn.completed event")
        return
    transcript.terminal = True
    usage = completed[0].get("usage") or {}
    transcript.usage = _usage(
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cached_input_tokens"),
        None,
        usage.get("reasoning_output_tokens"),
        None,
    )
    if transcript.errors:
        return
    if len(messages) != 1:
        transcript.issues.append(f"Expected one agent message, saw {len(messages)}")
    transcript.complete = not transcript.issues


def _extract_agy(events, config, transcript):
    if len(events) != 1:
        transcript.issues.append("Expected exactly one AGY result")
        return
    event = events[0]
    transcript.terminal = True
    transcript.response = event.get("response", "")
    transcript.session_id = event.get("conversation_id") or event.get("session_id")
    transcript.observed_model = event.get("model")
    usage = event.get("usage") or {}
    transcript.usage = _usage(
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_tokens"),
        None,
        usage.get("thinking_tokens"),
        None,
    )
    if event.get("denied_actions") or event.get("tool_calls"):
        transcript.tool_observed = True
    if event.get("status") != "SUCCESS" or event.get("error"):
        transcript.errors.append(
            f"result {event.get('status')}: {event.get('error') or ''}"
        )
    if not isinstance(transcript.response, str):
        transcript.issues.append("AGY response must be text")
    transcript.complete = not transcript.errors and not transcript.issues


EXTRACTORS = {
    "claude-stream-json": _extract_claude,
    "codex-exec-jsonl": _extract_codex,
    "agy-json": _extract_agy,
}


def parse(folder, config, returncode, timed_out=False, interrupted=False):
    """Fail-closed outcome for one attempt from its native CLI transcript."""
    folder = Path(folder)
    runner = config.get("runner")
    _check_runner(runner)
    result = _blank_result(runner)
    stderr = ""
    try:
        stderr = (
            (folder / "stderr.log").read_text()
            if (folder / "stderr.log").exists()
            else ""
        )
    except OSError as exc:
        stderr = f"<unreadable stderr: {exc}>"
    result["stderr_excerpt"] = stderr[:4000]
    transcript = _Transcript()
    try:
        events = (
            [read_json(folder / "events.jsonl")]
            if runner == "agy"
            else read_jsonl(folder / "events.jsonl")
        )
        EXTRACTORS[FORMATS[runner]](events, config, transcript)
    except (
        RunnerError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
    ) as exc:
        transcript.issues.append(f"Malformed or missing transcript: {exc}")
    if re.search(r"(?im)^\s*(?:error|fatal)\s*:", stderr):
        transcript.errors.append("CLI stderr reported an error")
    if transcript.usage:
        values = [v for k, v in transcript.usage.items() if k != "cost"]
        values.append((transcript.usage.get("cost") or {}).get("total"))
        if any(
            v is not None
            and (type(v) not in (int, float) or not math.isfinite(v) or v < 0)
            for v in values
        ):
            transcript.issues.append("Malformed usage metadata")
            transcript.usage = None
    result.update(
        response=transcript.response,
        usage=transcript.usage,
        session_id=transcript.session_id,
        response_id=transcript.response_id,
        observed_model=transcript.observed_model,
    )
    if runner == "codex":
        last = folder / "last-message.txt"
        if last.exists() and transcript.complete:
            try:
                if last.read_text().strip() != transcript.response.strip():
                    transcript.issues.append("last-message.txt differs from JSONL text")
                    transcript.complete = False
            except OSError as exc:
                transcript.issues.append(f"Unreadable last-message.txt: {exc}")
                transcript.complete = False
    error_text = "\n".join(filter(None, [*transcript.errors, stderr.strip()]))
    result["retry_after_seconds"] = retry_after_seconds(error_text)
    if interrupted or timed_out:
        result.update(
            outcome="interrupted" if interrupted else "timeout",
            error=("Runner interrupted" if interrupted else "Attempt timed out")
            + (": " + error_text[:4000] if error_text else ""),
        )
        return result
    if transcript.tool_observed:
        result.update(
            outcome="protocol_error", error="Observed tool use or permission denial"
        )
        return result
    if returncode != 0 or transcript.errors:
        joined = error_text or f"CLI exited with {returncode} and no error text"
        result.update(outcome=error_kind(joined), error=joined[:4000])
        return result
    if not transcript.terminal:
        transcript.issues.insert(0, "Transcript has no terminal event")
    if not _model_matches(config["model"], transcript.observed_model):
        transcript.issues.append(
            f"Observed model {transcript.observed_model!r} != requested {config['model']!r}"
        )
    if transcript.issues or not transcript.complete:
        result.update(
            outcome="protocol_error",
            error="; ".join(transcript.issues or ["Transcript incomplete"]),
        )
        return result
    if transcript.invalid_reason:
        result.update(outcome="invalid_response", error=transcript.invalid_reason)
        return result
    text = transcript.response.strip()
    if NUMBER.fullmatch(text):
        result.update(outcome="valid", number=int(text), error="")
    else:
        result.update(
            outcome="invalid_response",
            error="Final response was not a complete bare integer in 1-10",
        )
    return result


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_attempt(folder, config, result):
    """Re-derive the attempt from raw evidence and raise on any disagreement."""
    folder = Path(folder)
    validate_config(config)
    path = folder / REQUEST_FILE
    if not path.exists():
        raise RunnerError(f"{path} is missing")
    request = read_json(path)
    for key in REQUEST_KEYS:
        if request.get(key) != config[key]:
            raise RunnerError(
                f"cli-request.json {key} mismatch: {request.get(key)!r} != {config[key]!r}"
            )
    if request.get("format") != FORMATS[config["runner"]]:
        raise RunnerError("cli-request.json format mismatch")
    expected = _argv(config, folder, request.get("binary"))
    if request.get("command") != expected:
        raise RunnerError("cli-request.json command does not match the frozen config")
    if request.get("environment_overrides") != {}:
        raise RunnerError("cli-request.json environment overrides mismatch")
    if result.get("command") is not None and result["command"] != expected:
        raise RunnerError("Recorded result command differs from cli-request.json")
    outcome = result.get("outcome")
    if outcome not in OUTCOMES:
        raise RunnerError(f"Unknown outcome {outcome!r}")
    again = parse(
        folder,
        config,
        result.get("returncode"),
        timed_out=outcome == "timeout",
        interrupted=outcome == "interrupted",
    )
    for key in (
        "outcome",
        "number",
        "response",
        "session_id",
        "response_id",
        "observed_model",
        "usage",
    ):
        if again[key] != result.get(key):
            raise RunnerError(
                f"Reparse {key} mismatch: {again[key]!r} != {result.get(key)!r}"
            )
    if outcome == "valid" and (
        result.get("number") is None
        or not NUMBER.fullmatch(str(result.get("response", "")).strip())
        or int(str(result["response"]).strip()) != result["number"]
    ):
        raise RunnerError("Accepted number does not equal the bare response text")
    if outcome != "valid" and result.get("number") is not None:
        raise RunnerError("Only valid attempts may carry a number")
    if result.get("evidence_level", EVIDENCE_LEVEL) != EVIDENCE_LEVEL:
        raise RunnerError("CLI attempts can only claim cli_transcript evidence")
    if result.get("http_statuses"):
        raise RunnerError("CLI attempts cannot claim audited HTTP statuses")
    return again
