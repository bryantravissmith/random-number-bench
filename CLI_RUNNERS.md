# Native CLI runners

Pi remains the default (`--runner pi`). Native CLIs are additional options. Install and sign in to the chosen CLI before planning a round. The benchmark never performs an interactive login and never selects an API-key fallback.

| Runner | Default model | Authentication | Evidence |
| --- | --- | --- | --- |
| `pi` | `gpt-6.1-sol` | Existing Pi provider login; OpenRouter credits for GPT-4o | Native session plus audited HTTP request |
| `claude` | `claude-opus-5-5` | Claude Code first-party subscription OAuth | Native CLI transcript |
| `codex` | `gpt-6.1-sol` | Codex ChatGPT login | Native CLI transcript |
| `grok` | `grok-4.7` | Grok OAuth login | Native CLI transcript |
| `agy` | `gemini-3.8-flash-medium` | AntiGravity account | Native CLI transcript |

Model names are explicit defaults, not promises of account availability. Grok currently reports the `grok-4.7` response under that name and its usage under the canonical `grok-4.7-build` name. An unavailable model stops the round; it is never substituted. Override a default with `--model <exact-id>`.

## Claude subscription example

```sh
claude auth login
uv run bench.py check --runner claude --model claude-opus-5-5
uv run bench.py plan --runner claude --model claude-opus-5-5 \
  --out runs/claude-opus-5-5-001 --count 1000 --concurrency 4
uv run bench.py run runs/claude-opus-5-5-001
uv run bench.py export runs/claude-opus-5-5-001 \
  --out results/claude-opus-5-5-001
uv run bench.py report
```

Use `check --runner codex`, `check --runner grok`, or `check --runner agy` to test the other logins without inference. Grok login uses `grok login --oauth`; Codex uses `codex login`; AntiGravity uses its account sign-in. Credentials are not copied into the result package. Planning and resume check authentication and runner version. Common API credential and endpoint environment variables are rejected, not silently removed.

Before a measured round, use a distinct `runs/smoke-...` ID with `--count 1`. Keep engineering checks separate from benchmark results. All runners share the same scheduling, adaptive concurrency, cooldown, attempt limits and error exclusions.

## Isolation controls

| Runner | Controls | Remaining context |
| --- | --- | --- |
| Claude | Safe mode, empty built-in tools and MCP configuration, no settings sources or persistence, one turn, explicit system prompt | Managed policy and built-in CLI behavior may still apply |
| Codex | Ephemeral execution, ignore user config/rules, read-only sandbox, tools disabled through feature settings, explicit instructions file | Built-in CLI instructions may still apply; response model ID is not exposed |
| Grok | Verbatim user prompt, system override, tool deny list, no plan/subagents/web, compatibility rules/skills/hooks disabled | Built-in CLI behavior and model aliases may still apply |
| AGY | Dedicated project with denied file/shell/web/MCP permissions, sandbox, no slash expansion, one print prompt | Built-in agent instructions remain; no equivalent system-prompt override |

The exact argv is retained in the private attempt evidence. CLI transcript
checks complement launch controls; an observed tool action cannot count as a
random-number answer.

## What a native CLI result establishes

Each physical attempt is a fresh CLI process with the exact user prompt. A successful terminal event and zero exit status are required. The parser rejects observed errors, tools, permission denials, model mismatches and unsupported transcript shapes. Digits in partial or failed responses cannot enter the histogram. Where exposed, session IDs must be unique. Native logs, launcher inputs and source are hashed and retained locally; export reparses the transcript.

The transcript does **not** reveal every backend HTTP request, retry, injected system instruction, sampling setting or auxiliary model call. The CLI may perform internal retries or usage-accounting calls. `physical_attempts` therefore counts CLI launches for these runners. The evidence label is `cli_transcript`, and HTTP status arrays remain empty. Pi keeps its stronger `pi_http_request` checks.

The benchmark provides the same system preamble where a CLI supports an override. Native built-in instructions may still be present. AGY does not expose the same system override. Tool permissions and observed tool activity are checked, but the complete backend tool schema is not exposed by every CLI. These differences are part of the measured runner condition. Do not attribute cross-runner differences solely to the base model.

Costs are runner-reported estimates, not subscription charges. Unknown costs display as `n/a`; a missing estimate cannot trigger the metadata-cost ceiling. Time and attempt bounds still apply. The default ceiling is 10 reported USD across the round and can be changed before planning with `--max-cost`.

## Local validation on 2026-11-01

All four native runners completed a separate two-prompt smoke check and passed
raw-to-public export verification. These checks are engineering evidence, not
published benchmark samples. Claude Code 2.1.296, Codex 0.166.0 and Grok 1.0.70
were used; AGY is identified by its executable SHA-256 in the frozen manifest.

AGY encountered two real subscription 429s before succeeding after cooldown.
Those attempts were retained and excluded from its smoke histogram. Grok uses
its existing grok.com OIDC login, which the guard recognizes as OAuth. No login
changes or API-key fallbacks were needed. Claude and Grok exposed cost estimates;
Codex and AGY did not. All four returned `7` in the final two-call smoke checks.

## Non-reasoning comparisons

**Verified subscription option: Haiku 4.5 with thinking off.** On 2026-11-01,
Claude Code 2.1.296 completed one call with each canonical prompt using
`claude-haiku-4-5-20251001 --thinking off`. Both returned `7`, reported zero
thinking tokens, and passed raw-to-public verification. These two engineering
smoke calls remain local and are not benchmark samples.

```sh
uv run bench.py plan --runner claude --model claude-haiku-4-5-20251001 \
  --thinking off --count 50 --out runs/haiku-thinking-off-001
```

The adapter maps `off` to an explicit session setting
`--settings '{"alwaysThinkingEnabled":false}'` and omits the unsupported Haiku
4.5 effort option. It rejects observed thinking blocks, thinking progress or
positive thinking-token usage in this mode. Only the Haiku 4.5 family is
currently allowed for this profile; other models need separate validation.
This is a thinking-capable model with thinking disabled, not a distinct model
family trained without a reasoning mode. An omitted effort option or `low`
effort alone does not disable thinking.

[Claude Code's model configuration](https://code.claude.com/docs/en/model-config#extended-thinking)
documents thinking controls and says its off switch has no effect on Opus 5.5,
Sonnet 5.5 or Haiku 5.5. The
[API's thinking support table](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting)
allows additional combinations, including Haiku 5.5 with thinking disabled at
high effort or below. API support does not establish CLI support. No direct API
calls are used here.

The Claude launcher rejects inherited `CLAUDE_CODE_EFFORT_LEVEL`,
`MAX_THINKING_TOKENS`, `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` and
`CLAUDE_CODE_EXTRA_BODY` so they cannot silently override the frozen settings.
The first three Opus effort plans predate this guard; all four variables were
confirmed absent before those runs. Native transcripts establish the requested
CLI controls and reported token usage, not a complete backend request payload.

As checked on 2026-11-01, the local Codex subscription catalog does not list
GPT-4o or GPT-4.1. The [Pi/OpenRouter grid](grids/pi.json) now provides a
separately validated GPT-4o comparison using the user's connected OpenRouter
account and paid credits. It sends no reasoning parameters, pins the `openai`
upstream and disables provider fallback. A loopback HTTP test and separate live
smoke checked this profile before the measured 100-trial-per-prompt run.

The audit verifies the outgoing request, HTTP response, returned model alias,
upstream provider, response ID and reported reasoning-token usage. The response
alias is `openai/gpt-4o`; it does not establish an immutable dated backend
snapshot. The system fingerprint is retained when supplied. Other OpenRouter
models and thinking controls require their own validated profile before use.

OpenAI lists GPT-4o and GPT-4.1 for API shutdown on January 14, 2027. Original
GPT-4 and GPT-4 Turbo shut down on October 23, 2026. See the official
[deprecation schedule](https://developers.openai.com/api/docs/deprecations),
[GPT-4o model page](https://developers.openai.com/api/docs/models/gpt-4o) and
[Codex model guidance](https://learn.chatgpt.com/docs/models). A future comparison
should preserve the exact two prompts, record reasoning as not applicable,
and keep API and CLI runs separate. See [grid setup and reproduction](GRIDS.md).

## Sources

Installed CLI help and authentication commands are the source of the launch flags in `cli_runners.py`. Codex documents [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode) and [CLI authentication](https://learn.chatgpt.com/docs/auth?surface=cli). Freeze the installed version for every round: CLI formats and defaults can change.
