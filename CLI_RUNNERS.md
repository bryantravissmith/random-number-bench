# Native CLI runners

Pi remains the default (`--runner pi`). Native CLIs are additional options. Install and sign in to the chosen CLI before planning a round. The benchmark never performs an interactive login and never selects an API-key fallback.

| Runner | Default model | Authentication | Evidence |
| --- | --- | --- | --- |
| `pi` | `gpt-6.1-sol` | Existing Pi provider login | Native session plus audited OpenAI HTTP request |
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

## Sources

Installed CLI help and authentication commands are the source of the launch flags in `cli_runners.py`. Codex documents [non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode) and [CLI authentication](https://learn.chatgpt.com/docs/auth?surface=cli). Freeze the installed version for every round: CLI formats and defaults can change.
