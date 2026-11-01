# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Offline fixture tests for the subscription CLI runners; no inference calls."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cli_runners as cr

PROMPT = (
    "Generate one random integer from 1 to 10, inclusive. Reply with only the integer."
)
SYSTEM = "You are a helpful assistant."
MODELS = {
    "claude": "claude-opus-5-5",
    "codex": "gpt-6.1-sol",
    "grok": "grok-5",
    "agy": "gemini-3.5-pro",
}


def config_for(runner, cwd, **overrides):
    config = {
        "runner": runner,
        "model": MODELS[runner],
        "provider": cr.PROVIDERS[runner],
        "thinking": "high" if runner != "codex" else "medium",
        "system_prompt": SYSTEM,
        "cwd": str(cwd),
        "timeout": 120,
        "prompt": PROMPT,
    }
    config.update(overrides)
    return config


def claude_events(text="7", **tweaks):
    init = {
        "type": "system",
        "subtype": "init",
        "session_id": "sess-1",
        "model": MODELS["claude"],
        "tools": [],
        "mcp_servers": [],
        "apiKeySource": "none",
        "permissionMode": "default",
    }
    assistant = {
        "type": "assistant",
        "message": {
            "id": "msg_01",
            "role": "assistant",
            "model": MODELS["claude"] + "-20260301",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 40, "output_tokens": 1},
        },
        "session_id": "sess-1",
    }
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "result": text,
        "session_id": "sess-1",
        "total_cost_usd": 0.0123,
        "usage": {
            "input_tokens": 40,
            "output_tokens": 1,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        },
        "modelUsage": {MODELS["claude"] + "-20260301": {"costUSD": 0.0123}},
        "permission_denials": [],
    }
    events = {"init": init, "assistant": assistant, "result": result}
    for key, value in tweaks.items():
        events[key].update(value)
    return [init, assistant, result]


def codex_events(text="3"):
    return [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "reasoning", "text": "picking"},
        },
        {
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": text},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 120,
                "cached_input_tokens": 100,
                "output_tokens": 2,
                "reasoning_output_tokens": 1,
            },
        },
    ]


def message_events(model, text="9"):
    return [
        {
            "status": "SUCCESS",
            "response": text,
            "model": model,
            "conversation_id": "agy-1",
            "denied_actions": [],
            "usage": {"input_tokens": 30, "output_tokens": 1, "cache_read_tokens": 0},
        }
    ]


def grok_events(text="4"):
    events = claude_events(text)
    events[0]["model"] = MODELS["grok"]
    events[1]["message"]["model"] = MODELS["grok"]
    events[2]["modelUsage"] = {MODELS["grok"]: {}}
    return events


SUCCESS = {
    "claude": lambda: claude_events(),
    "codex": lambda: codex_events(),
    "grok": lambda: grok_events(),
    "agy": lambda: message_events(MODELS["agy"]),
}
EXPECTED_NUMBER = {"claude": 7, "codex": 3, "grok": 4, "agy": 9}


class RunnerFixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cli-runner-tests-")
        self.root = Path(self.temp.name)
        self.project = cr.prepare("agy", self.root / "agy-project")
        self.base_env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root)}

    def tearDown(self):
        self.temp.cleanup()

    def cwd_for(self, runner):
        return self.root

    def attempt(self, runner, events, stderr="", config=None, raw=None):
        self.counter = getattr(self, "counter", 0) + 1
        folder = self.root / "attempts" / runner / f"{self.counter:02d}"
        config = config or config_for(runner, self.cwd_for(runner))
        cr.command(config, folder, binary=f"/fake/bin/{runner}")
        text = raw if raw is not None else "".join(json.dumps(e) + "\n" for e in events)
        (folder / "events.jsonl").write_text(text)
        (folder / "stderr.log").write_text(stderr)
        return folder, config

    # -- success paths -----------------------------------------------------

    def test_success_for_every_runner(self):
        for runner in cr.RUNNERS:
            with self.subTest(runner=runner):
                folder, config = self.attempt(runner, SUCCESS[runner]())
                result = cr.parse(folder, config, 0)
                self.assertEqual(result["outcome"], "valid", result["error"])
                self.assertEqual(result["number"], EXPECTED_NUMBER[runner])
                self.assertEqual(result["evidence_level"], "cli_transcript")
                self.assertEqual(result["http_statuses"], [])
                self.assertIsNotNone(result["usage"])
                self.assertEqual(
                    set(result["usage"]) >= {"input", "output", "cacheRead"}, True
                )
                self.assertTrue(result["session_id"])
                verified = cr.verify_attempt(
                    folder, config, {**result, "returncode": 0}
                )
                self.assertEqual(verified["number"], EXPECTED_NUMBER[runner])

    def test_claude_usage_and_identity_fields(self):
        folder, config = self.attempt("claude", claude_events("10"))
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["number"], 10)
        self.assertEqual(result["usage"]["cost"]["total"], 0.0123)
        self.assertEqual(result["usage"]["cacheRead"], 10)
        self.assertEqual(result["usage"]["cacheWrite"], 5)
        self.assertEqual(result["response_id"], "msg_01")
        self.assertEqual(result["observed_model"], "claude-opus-5-5-20260301")

    def test_codex_reasoning_usage_and_last_message_cross_check(self):
        folder, config = self.attempt("codex", codex_events())
        (folder / "last-message.txt").write_text("3\n")
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "valid")
        self.assertEqual(result["usage"]["reasoning"], 1)
        self.assertEqual(result["session_id"], "thread-1")
        (folder / "last-message.txt").write_text("4\n")
        self.assertEqual(cr.parse(folder, config, 0)["outcome"], "protocol_error")

    def test_whitespace_padding_is_still_a_bare_integer(self):
        folder, config = self.attempt("claude", claude_events("\n 2 \n"))
        self.assertEqual(cr.parse(folder, config, 0)["number"], 2)

    # -- invalid responses -------------------------------------------------

    def test_non_bare_answers_are_invalid_not_valid(self):
        for runner, text in (
            ("claude", "7."),
            ("codex", "I pick 3"),
            ("grok", "0"),
            ("agy", "11"),
            ("claude", "٣"),
        ):
            with self.subTest(runner=runner, text=text):
                builder = {
                    "claude": claude_events,
                    "codex": codex_events,
                    "grok": grok_events,
                    "agy": lambda t: message_events(MODELS["agy"], t),
                }[runner]
                folder, config = self.attempt(runner, builder(text))
                result = cr.parse(folder, config, 0)
                self.assertEqual(result["outcome"], "invalid_response")
                self.assertIsNone(result["number"])

    # -- errors never yield numbers ---------------------------------------

    def test_nonzero_exit_with_digit_in_stderr_is_never_accepted(self):
        for runner in cr.RUNNERS:
            with self.subTest(runner=runner):
                folder, config = self.attempt(
                    runner, SUCCESS[runner](), stderr="fatal: 7 workers crashed\n"
                )
                result = cr.parse(folder, config, 1)
                self.assertEqual(result["outcome"], "provider_error")
                self.assertIsNone(result["number"])
                self.assertIn("7 workers", result["error"])

    def test_claude_rate_limit_result(self):
        events = claude_events(
            'API Error: 429 {"type":"error","error":{"type":"rate_limit_error"}} retry after 30 seconds'
        )
        events[1]["error"] = "rate_limit"
        events[2].update(subtype="error_during_execution", is_error=True, result="")
        folder, config = self.attempt("claude", events)
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "rate_limit")
        self.assertEqual(result["retry_after_seconds"], 30)
        self.assertIsNone(result["number"])

    def test_claude_usage_limit_and_login_errors_are_fatal(self):
        for text in (
            "You've hit your usage limit. Resets in 2 hours",
            "Not logged in. Please run /login",
        ):
            with self.subTest(text=text):
                events = claude_events(text)
                events[2].update(is_error=True, errors=[text])
                folder, config = self.attempt("claude", events)
                self.assertEqual(cr.parse(folder, config, 0)["outcome"], "fatal_error")

    def test_codex_turn_failed_and_error_events(self):
        failed = codex_events()[:2] + [
            {"type": "turn.failed", "error": {"message": "Too many requests (429)"}}
        ]
        folder, config = self.attempt("codex", failed)
        self.assertEqual(cr.parse(folder, config, 1)["outcome"], "rate_limit")
        errored = codex_events()[:2] + [
            {"type": "error", "message": "401 Unauthorized: authentication failed"}
        ]
        folder, config = self.attempt("codex", errored)
        self.assertEqual(cr.parse(folder, config, 0)["outcome"], "fatal_error")

    def test_message_stream_error_result(self):
        events = message_events(MODELS["agy"])
        events[-1] = {"type": "result", "status": "error", "error": "model overloaded"}
        folder, config = self.attempt("agy", events)
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "rate_limit")
        self.assertIsNone(result["number"])

    def test_timeout_and_interrupt_keep_usage_but_no_number(self):
        for runner in cr.RUNNERS:
            with self.subTest(runner=runner):
                folder, config = self.attempt(runner, SUCCESS[runner]())
                timed = cr.parse(folder, config, None, timed_out=True)
                self.assertEqual(timed["outcome"], "timeout")
                self.assertIsNone(timed["number"])
                self.assertIsNotNone(timed["usage"])
                stopped = cr.parse(folder, config, -15, interrupted=True)
                self.assertEqual(stopped["outcome"], "interrupted")
                self.assertIsNone(stopped["number"])

    # -- tool use and protocol violations ----------------------------------

    def test_tool_use_is_rejected_for_every_runner(self):
        claude_tool = claude_events()
        claude_tool[1]["message"]["content"].insert(
            0, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}
        )
        claude_denial = claude_events()
        claude_denial[2]["permission_denials"] = [{"tool_name": "Bash"}]
        claude_tools_on = claude_events(init={"tools": ["Bash"]})
        codex_tool = codex_events()
        codex_tool.insert(
            2,
            {
                "type": "item.completed",
                "item": {"id": "c", "type": "command_execution", "command": "ls"},
            },
        )
        grok_tool = grok_events()
        grok_tool.insert(
            2,
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "content": "x"}]},
            },
        )
        agy_tool = message_events(MODELS["agy"])
        agy_tool[0]["denied_actions"] = [{"action": "command", "display_name": "shell"}]
        for runner, events in (
            ("claude", claude_tool),
            ("claude", claude_denial),
            ("claude", claude_tools_on),
            ("codex", codex_tool),
            ("grok", grok_tool),
            ("agy", agy_tool),
        ):
            with self.subTest(runner=runner):
                folder, config = self.attempt(runner, events)
                result = cr.parse(folder, config, 0)
                self.assertEqual(result["outcome"], "protocol_error", result)
                self.assertIsNone(result["number"])

    def test_model_substitution_is_rejected(self):
        wrong_init = claude_events(init={"model": "claude-sonnet-5-5"})
        wrong_msg = claude_events()
        wrong_msg[1]["message"]["model"] = "claude-sonnet-5-5-20260301"
        wrong_usage = claude_events()
        wrong_usage[2]["modelUsage"] = {"claude-haiku-5-5": {}}
        agy_wrong = message_events("gemini-3.5-flash")
        codex_wrong = codex_events()
        codex_wrong[0]["model"] = "gpt-6.1-mini"
        for runner, events in (
            ("claude", wrong_init),
            ("claude", wrong_msg),
            ("claude", wrong_usage),
            ("agy", agy_wrong),
            ("codex", codex_wrong),
        ):
            with self.subTest(runner=runner):
                folder, config = self.attempt(runner, events)
                self.assertEqual(
                    cr.parse(folder, config, 0)["outcome"], "protocol_error"
                )

    def test_api_key_session_and_multi_turn_are_rejected(self):
        keyed = claude_events(init={"apiKeySource": "ANTHROPIC_API_KEY"})
        folder, config = self.attempt("claude", keyed)
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "protocol_error")
        self.assertIn("apiKeySource", result["error"])
        turns = claude_events(result={"num_turns": 2})
        folder, config = self.attempt("claude", turns)
        self.assertEqual(cr.parse(folder, config, 0)["outcome"], "protocol_error")

    def test_missing_terminal_event_fails_closed(self):
        for runner in cr.RUNNERS:
            with self.subTest(runner=runner):
                folder, config = self.attempt(runner, SUCCESS[runner]()[:-1])
                result = cr.parse(folder, config, 0)
                self.assertEqual(result["outcome"], "protocol_error")
                self.assertIsNone(result["number"])

    def test_malformed_transcripts_fail_closed(self):
        cases = {
            "garbage": "7\n",
            "partial-json": '{"type":"result","subtype":"success"',
            "array-line": "[1,2,3]\n",
            "empty": "",
            "wrong-format": "".join(json.dumps(e) + "\n" for e in codex_events()),
        }
        for runner in cr.RUNNERS:
            for name, raw in cases.items():
                if runner == "codex" and name == "wrong-format":
                    raw = "".join(json.dumps(e) + "\n" for e in claude_events())
                with self.subTest(runner=runner, case=name):
                    folder, config = self.attempt(runner, [], raw=raw)
                    result = cr.parse(folder, config, 0)
                    self.assertEqual(result["outcome"], "protocol_error", result)
                    self.assertIsNone(result["number"])

    def test_missing_events_file_is_protocol_error(self):
        folder, config = self.attempt("claude", claude_events())
        (folder / "events.jsonl").unlink()
        self.assertEqual(cr.parse(folder, config, 0)["outcome"], "protocol_error")

    # -- request freezing and verification --------------------------------

    def test_cli_request_records_exact_values_and_no_secrets(self):
        for runner in cr.RUNNERS:
            with self.subTest(runner=runner):
                folder, config = self.attempt(runner, SUCCESS[runner]())
                request = cr.read_json(folder / cr.REQUEST_FILE)
                for key in cr.REQUEST_KEYS:
                    self.assertEqual(request[key], config[key])
                self.assertIn(PROMPT, request["command"])
                self.assertEqual(request["command"][0], f"/fake/bin/{runner}")
                self.assertIn(config["model"], request["command"])
                self.assertNotIn("sk-", json.dumps(request))

    def test_claude_command_uses_verified_isolation_flags(self):
        folder, config = self.attempt("claude", claude_events())
        argv = cr.read_json(folder / cr.REQUEST_FILE)["command"]
        for flag in (
            "--print",
            "--safe-mode",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--verbose",
        ):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], SYSTEM)
        self.assertEqual(
            json.loads(argv[argv.index("--mcp-config") + 1]), {"mcpServers": {}}
        )
        self.assertNotIn("--fallback-model", argv)
        self.assertNotIn("--resume", argv)
        self.assertNotIn("--continue", argv)

    def test_codex_command_freezes_instructions_file(self):
        folder, config = self.attempt("codex", codex_events())
        argv = cr.read_json(folder / cr.REQUEST_FILE)["command"]
        self.assertEqual(argv[1], "exec")
        self.assertIn("--json", argv)
        self.assertIn("--ephemeral", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn('model_reasoning_effort="medium"', argv)
        self.assertIn('model_provider="openai"', argv)
        self.assertEqual((folder / "instructions.md").read_text(), SYSTEM)

    def test_agy_command_uses_dedicated_project_and_no_fake_system_override(self):
        folder, config = self.attempt("agy", message_events(MODELS["agy"]))
        request = cr.read_json(folder / cr.REQUEST_FILE)
        argv = request["command"]
        self.assertEqual(argv[argv.index("--project") + 1], cr.AGY_PROJECT)
        self.assertIn("--sandbox", argv)
        self.assertEqual(request["environment_overrides"], {})
        self.assertFalse((folder / "system.md").exists())

    def test_request_mismatch_raises(self):
        folder, config = self.attempt("claude", claude_events())
        result = {**cr.parse(folder, config, 0), "returncode": 0}
        cr.verify_attempt(folder, config, result)
        for key, value in (
            ("prompt", PROMPT + " Please."),
            ("model", "claude-sonnet-5-5"),
            ("thinking", "low"),
            ("system_prompt", "Be brief."),
            ("timeout", 60),
        ):
            with self.subTest(key=key), self.assertRaises(cr.RunnerError):
                cr.verify_attempt(folder, {**config, key: value}, result)
        request = cr.read_json(folder / cr.REQUEST_FILE)
        request["command"].append("--resume")
        cr.save(folder / cr.REQUEST_FILE, request)
        with self.assertRaises(cr.RunnerError):
            cr.verify_attempt(folder, config, result)

    def test_verify_rejects_tampered_result(self):
        folder, config = self.attempt("claude", claude_events())
        result = {**cr.parse(folder, config, 0), "returncode": 0}
        for tamper in (
            {"number": 8},
            {"response": "8"},
            {"outcome": "invalid_response"},
            {"http_statuses": [200]},
            {"evidence_level": "pi_native_session"},
        ):
            with self.subTest(tamper=tamper), self.assertRaises(cr.RunnerError):
                cr.verify_attempt(folder, config, {**result, **tamper})
        (folder / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in claude_events("8"))
        )
        with self.assertRaises(cr.RunnerError):
            cr.verify_attempt(folder, config, result)

    # -- configuration guards ---------------------------------------------

    def test_config_validation(self):
        bad = (
            {"provider": "openai"},
            {"model": " claude-opus-5-5"},
            {"model": ""},
            {"thinking": "turbo"},
            {"prompt": " "},
            {"cwd": "relative"},
            {"timeout": 0},
            {"runner": "pi"},
        )
        for override in bad:
            with self.subTest(override=override), self.assertRaises(cr.RunnerError):
                cr.command(
                    {**config_for("claude", self.root), **override},
                    self.root / "bad",
                    binary="/fake/bin/claude",
                )

    def test_environment_rejects_credentials_and_clears_nesting(self):
        for runner, key in (
            ("claude", "ANTHROPIC_API_KEY"),
            ("claude", "ANTHROPIC_BASE_URL"),
            ("claude", "CLAUDE_CODE_USE_BEDROCK"),
            ("codex", "OPENAI_API_KEY"),
            ("codex", "OPENAI_BASE_URL"),
            ("grok", "XAI_API_KEY"),
            ("agy", "GEMINI_API_KEY"),
            ("agy", "GOOGLE_GENAI_USE_VERTEXAI"),
        ):
            with self.subTest(runner=runner, key=key):
                with self.assertRaises(cr.RunnerError) as caught:
                    cr.environment(runner, {**self.base_env, key: "sk-secret-value"})
                self.assertNotIn("sk-secret-value", str(caught.exception))
        env = cr.environment(
            "codex",
            {
                **self.base_env,
                "CLAUDECODE": "1",
                "CLAUDE_CODE_ENTRYPOINT": "cli",
                "CODEX_HOME": "/keep/codex",
                "CLAUDE_CONFIG_DIR": "/keep/claude",
            },
        )
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)
        self.assertEqual(env["CODEX_HOME"], "/keep/codex")
        self.assertEqual(env["HOME"], str(self.root))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/keep/claude")
        self.assertEqual(env["NO_COLOR"], "1")
        self.assertEqual(
            cr.environment("claude", self.base_env)["DISABLE_TELEMETRY"], "1"
        )

    def test_prepare_refuses_changed_project(self):
        self.assertEqual(cr.prepare("agy", self.project.parent), self.project)
        settings = cr.read_json(self.project)
        self.assertEqual(settings["permissionGrants"]["permissionGrants"]["allow"], [])
        self.project.write_text('{"unrelated": true}')
        with self.assertRaises(cr.RunnerError):
            cr.prepare("agy", self.project.parent)
        self.assertEqual(cr.read_json(self.project), {"unrelated": True})
        self.assertIsNone(cr.prepare("claude"))

    def test_claude_thinking_and_text_blocks_share_one_message(self):
        events = claude_events("6")
        thinking = json.loads(json.dumps(events[1]))
        thinking["message"]["content"] = [{"type": "thinking", "thinking": ""}]
        events.insert(1, thinking)
        folder, config = self.attempt("claude", events)
        self.assertEqual(cr.parse(folder, config, 0)["number"], 6)
        thinking["message"]["id"] = "different-message"
        folder, config = self.attempt("claude", events)
        self.assertEqual(cr.parse(folder, config, 0)["outcome"], "protocol_error")

    def test_disagreeing_final_text_is_excluded_without_retry(self):
        events = claude_events("7", result={"result": ""})
        folder, config = self.attempt("claude", events)
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "invalid_response")
        self.assertIsNone(result["number"])

    def test_grok_observed_alias_and_partial_cost(self):
        events = grok_events()
        events[0]["apiKeySource"] = "oauth"
        events[-1]["modelUsage"] = {MODELS["grok"] + "-build": {}}
        events[-1]["cost_is_partial"] = True
        folder, config = self.attempt("grok", events)
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "valid")
        self.assertNotIn("cost", result["usage"])

    def test_bad_usage_and_stderr_error_cannot_contribute_number(self):
        events = claude_events("7", result={"total_cost_usd": "unknown"})
        folder, config = self.attempt("claude", events)
        self.assertEqual(cr.parse(folder, config, 0)["outcome"], "protocol_error")
        folder, config = self.attempt(
            "claude", claude_events(), stderr="Error: server disconnected after 7"
        )
        result = cr.parse(folder, config, 0)
        self.assertEqual(result["outcome"], "provider_error")
        self.assertIsNone(result["number"])

    def test_compound_retry_hints(self):
        self.assertEqual(cr.retry_after_seconds("Resets in 3m17s."), 197)
        self.assertEqual(cr.retry_after_seconds("Retry-After: 500ms"), 0.5)

    def test_claude_subscription_status_events(self):
        for status, overage, expected in (
            ("allowed", False, "valid"),
            ("allowed_warning", False, "valid"),
            ("rejected", False, "fatal_error"),
            ("allowed", True, "fatal_error"),
        ):
            events = claude_events()
            events.insert(
                2,
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": status, "isUsingOverage": overage},
                },
            )
            folder, config = self.attempt("claude", events)
            result = cr.parse(folder, config, 0)
            self.assertEqual(result["outcome"], expected)
            if expected != "valid":
                self.assertIsNone(result["number"])


class FakeBinary:
    """Write a shell script into a temp bin directory for preflight tests."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def install(self, name, script):
        path = self.directory / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cli-runner-preflight-")
        self.root = Path(self.temp.name)
        self.bin = FakeBinary(self.root / "bin")
        self.env = {"PATH": str(self.root / "bin"), "HOME": str(self.root)}

    def tearDown(self):
        self.temp.cleanup()

    def test_claude_oauth_first_party_passes_and_is_redacted(self):
        self.bin.install(
            "claude",
            'case "$1" in\n'
            "--version) echo '2.1.0 (Claude Code)';;\n"
            'auth) echo \'{"loggedIn":true,"authMethod":"claude.ai",'
            '"apiProvider":"firstParty","email":"person@example.com",'
            '"subscriptionType":"max","accessToken":"sk-ant-oat-SECRET"}\';;\n'
            "esac\n",
        )
        info = cr.preflight("claude", "claude-opus-5-5", env=self.env)
        self.assertEqual(info["auth_type"], "oauth")
        self.assertEqual(info["api_provider"], "firstParty")
        self.assertEqual(info["version"], "2.1.0 (Claude Code)")
        self.assertEqual(info["model"], "claude-opus-5-5")
        self.assertEqual(len(info["binary_sha256"]), 64)
        dumped = json.dumps(info)
        self.assertNotIn("SECRET", dumped)
        self.assertNotIn("person@example.com", dumped)

    def test_claude_api_key_login_fails_closed(self):
        self.bin.install(
            "claude",
            'case "$1" in\n'
            "--version) echo '2.1.0';;\n"
            'auth) echo \'{"loggedIn":true,"authMethod":"apiKey",'
            '"apiProvider":"firstParty"}\';;\n'
            "esac\n",
        )
        with self.assertRaises(cr.RunnerError):
            cr.preflight("claude", env=self.env)
        self.bin.install("claude", "echo not json\n")
        with self.assertRaises(cr.RunnerError):
            cr.preflight("claude", env=self.env)

    def test_codex_requires_chatgpt_login(self):
        self.bin.install(
            "codex",
            'case "$1" in\n'
            "--version) echo 'codex-cli 0.99.0';;\n"
            "login) echo 'Logged in using ChatGPT' >&2;;\n"
            "esac\n",
        )
        info = cr.preflight("codex", "gpt-6.1-sol", env=self.env)
        self.assertEqual(info["auth_type"], "chatgpt")
        self.bin.install(
            "codex",
            'case "$1" in\n'
            "--version) echo 'codex-cli 0.99.0';;\n"
            "login) echo 'Logged in using an API key';;\n"
            "esac\n",
        )
        with self.assertRaises(cr.RunnerError):
            cr.preflight("codex", env=self.env)

    def test_grok_requires_oauth_file_and_model_listing(self):
        self.bin.install(
            "grok",
            'case "$1" in\n'
            "--version) echo 'grok 1.4.2';;\n"
            "models) echo 'grok-5\ngrok-5-mini';;\n"
            "esac\n",
        )
        with self.assertRaises(cr.RunnerError):
            cr.preflight("grok", "grok-5", env=self.env)  # signed out: no auth file
        auth = self.root / ".grok" / "auth.json"
        auth.parent.mkdir()
        auth.write_text(json.dumps({"auth_mode": "api_key", "token": "xai-SECRET"}))
        with self.assertRaises(cr.RunnerError):
            cr.preflight("grok", "grok-5", env=self.env)
        auth.write_text(
            json.dumps({"issuer": {"auth_mode": "oidc", "token": "xai-SECRET"}})
        )
        info = cr.preflight("grok", "grok-5", env=self.env)
        self.assertEqual(info["auth_mode"], "oauth")
        self.assertTrue(info["model_listed"])
        self.assertNotIn("SECRET", json.dumps(info))
        with self.assertRaises(cr.RunnerError):
            cr.preflight("grok", "grok-5-max", env=self.env)
        self.bin.install("grok", "exit 1\n")
        with self.assertRaises(cr.RunnerError):
            cr.preflight("grok", "grok-5", env=self.env)

    def test_agy_uses_sha_version_and_models_listing(self):
        path = self.bin.install(
            "agy",
            'case "$1" in\n'
            "models) echo 'gemini-3.5-pro (Antigravity)';;\n"
            "*) exit 2;;\n"
            "esac\n",
        )
        info = cr.preflight("agy", "gemini-3.5-pro", env=self.env)
        self.assertEqual(info["version"], "sha256:" + cr.sha256_file(path))
        self.assertEqual(info["auth_type"], "antigravity_account")
        self.bin.install("agy", "echo 'Please sign in' >&2; exit 1\n")
        with self.assertRaises(cr.RunnerError):
            cr.preflight("agy", env=self.env)

    def test_preflight_rejects_credential_env_before_running_anything(self):
        self.bin.install("claude", 'echo touched > "$HOME/touched"\n')
        with self.assertRaises(cr.RunnerError):
            cr.preflight("claude", env={**self.env, "ANTHROPIC_API_KEY": "sk-x"})
        self.assertFalse((self.root / "touched").exists())
        with self.assertRaises(cr.RunnerError):
            cr.preflight("claude", env={**self.env, "PATH": str(self.root / "nope")})


if __name__ == "__main__":
    unittest.main(verbosity=2)
