# Google Antigravity, Gemini CLI and GitHub Copilot CLI

All hosts use the existing local Python analyser and its five stdio MCP tools.
Install Python 3.9+ with a working `python3` command (`python` on Windows when
configuring local `mcp_config.json`; published manifests launch `python3` because
macOS and most Linux distributions ship no bare `python`). No Python dependencies are
required. These integrations target Google Antigravity (`agy` CLI, Antigravity 2.0
desktop app, and Antigravity IDE), legacy Gemini CLI, and GitHub Copilot CLI — not
Gemini web chat, Copilot web chat, or the VS Code Copilot extension.

## Google Antigravity and Gemini CLI

In Google Antigravity (`agy` CLI, Antigravity 2.0, and Antigravity IDE), install as a
plugin under `~/.gemini/config/plugins/token-usage/` (with `plugin.json`, `mcp_config.json`,
`skills/report/`, `scripts/`, and `data/`) or stage via `agy plugin install`. In legacy
Gemini CLI, run `gemini extensions install /absolute/path/to/token-usage` (or
`gemini extensions install https://github.com/Wicked-Sick-Ltd/token-usage`).
The manifest sets `TOKEN_USAGE_RUNTIME=gemini`; explicit tool calls can pass
`runtime: "gemini"` and a `session_id` or `transcript` path.

The `gemini` adapter reads:

- **Google Antigravity JSONL transcripts** under
  `~/.gemini/antigravity*/brain/<conversation-id>/.system_generated/logs/transcript.jsonl`
  (covering Antigravity 2.0 `~/.gemini/antigravity/`, Antigravity IDE
  `~/.gemini/antigravity-ide/`, and Antigravity CLI `~/.gemini/antigravity-cli/`).
- **Legacy Gemini CLI recordings** (JSON snapshots and append-only JSONL) under
  `~/.gemini/tmp/*/chats/`.

`TOKEN_USAGE_GEMINI_HOME` points directly at an alternative `.gemini` directory.
Legacy Gemini CLI's `GEMINI_CLI_HOME` instead names the parent home directory. For
Antigravity transcripts, workspace paths are inferred from recorded tool working
directories (`Cwd` / `workspacePath`) and hashed for project filtering; for legacy
Gemini CLI recordings, project filtering compares Gemini's SHA-256 project hash.

Repeated message or step IDs count once, using the largest recorded counters. Cached
prompt tokens are separated from uncached input (Antigravity `PLANNER_RESPONSE` steps
record `input_tokens` already excluding `cache_read_tokens`, whereas legacy Gemini CLI
`tokens.input` includes `tokens.cached`). Tool-use prompt tokens count as input; thought
tokens count as output. In Antigravity, skills activated via `view_file` on
`skills/<name>/SKILL.md` open `$<name>` segments, and subagent conversations linked via
step 0 `SYSTEM_MESSAGE` `sender=<parent-session-id>` roll into the parent session once.
In legacy Gemini CLI, child and grandchild sessions under `chats/<parent-id>/` roll into
the parent once by start timestamp.

Context rewinds and `<CONTEXT_SUMMARY>` compactions retain the API work already consumed.
Missing counters produce `partial` or `activity_only` reports. Deleted recordings cannot
be reconstructed; legacy `$set.messages` checkpoints are flagged as incomplete. Unknown
model prices remain unknown: add an [overlay](../README.md#configuration) to price a
model. `live --runtime gemini` polls the native recording directly.

### Antigravity Stop hook

Root `hooks.json` registers `Stop` for Google Antigravity. It runs
`python3 scripts/token_usage.py hook` with a 15-second timeout and fails open.
A payload that carries `conversationId` or `transcriptPath` and does not carry
`transcript_path` is stored as the `gemini` runtime in
`~/.cache/token-usage/gemini-<session-id>.json` (override `TOKEN_USAGE_LEDGER_DIR`).
That ledger includes the first 120 characters of each segment prompt and does not
repoint `latest.json`. Stdout is `{}`, or one budget `systemMessage` when
`TOKEN_USAGE_BUDGET_USD` is crossed. Antigravity does not register `SubagentStop`.
Legacy Gemini CLI does not register this hook. This packaging is on `main` and is
listed under Unreleased; tag `v0.9.0` does not include the root hook file.

## GitHub Copilot CLI

From a checkout, run `copilot plugin install /absolute/path/to/token-usage`.
Use an absolute path: Copilot 1.0.91 rejects a bare `.`. Direct installs are
deprecated upstream; the marketplace entry will be the supported distribution
route once this version is published:

```bash
copilot plugin marketplace add Wicked-Sick-Ltd/ai-marketplace
copilot plugin install token-usage@wickedsick
copilot --experimental
```

The `.plugin/plugin.json` manifest supplies the report skill, MCP server and a
small native extension. Copilot provides the extension SDK itself; no npm install
is required. `--experimental` enables the extension API in the tested CLI.
`copilot plugin list --json`, `copilot skill list` and `copilot mcp list` show what
was loaded. The MCP runtime defaults to `copilot`.

Copilot's `assistant.usage` events are transient. The extension saves just the
event IDs, timestamps, model, token counters, command/skill labels and agent IDs
to `$COPILOT_HOME/token-usage/<session-id>.jsonl` (default `~/.copilot`). Its header
also records the session ID and working directory. It does not save prompt text,
tool results, credentials, billing counters, or code. It fails open if the SDK or
filesystem is unavailable. Remove this directory to delete captured usage;
uninstalling the plugin does not delete it.

The adapter combines that ledger with native
`$COPILOT_HOME/session-state/<session-id>/events.jsonl`, deduplicating by event ID.
Input totals already include cache reads and writes; output already includes
reasoning. Subagent usage is included once and displayed as a subset. Cache writes
without a lifetime use the standard 5-minute estimate, with an explicit warning.

Without the extension, saved `session.shutdown.modelMetrics` can recover session
totals. These are cumulative across resumes, so only uncaptured usage is added.
That recovery is labelled `partial`: command, time and subagent attribution are
unavailable for the recovered portion. An active session with no usage counters
is `activity_only`, not free. Copilot credits, premium-request multipliers and
nano-AI units are never treated as dollars. Prices remain API-equivalent estimates.

`TOKEN_USAGE_COPILOT_HOME` overrides the reader's root; `COPILOT_HOME` configures
both the native host and collector. Use `session_id` when multiple sessions are
running. This plugin does not add Copilot budget notifications.

## Reporting

```bash
python3 scripts/token_usage.py report --runtime gemini --models --agents
python3 scripts/token_usage.py history --runtime copilot --since 7d
python3 scripts/token_usage.py live --runtime copilot --iterations 1
```

Both runtimes also support JSON reports, comparisons, insights, top consumers,
dashboard and export. `auto` refuses ambiguous runtime discovery. Selecting a
runtime keeps its corpus separate from every other host.

## Verification and upstream contracts

Gemini CLI 0.62.0 validated and installed the extension in an isolated profile;
its native MCP list reported Connected and discovered the report skill. Synthetic
fixtures exercise JSON/JSONL accounting, duplicates, rewinds and nested agents.
The Windows installer completed but emitted a Node/libuv shutdown assertion;
subsequent extension-list and MCP health checks succeeded. A model-driven Gemini
turn has not been verified.

Copilot CLI 1.0.91 installed the plugin and discovered its skill and MCP server.
`tests/test_copilot_native.py` runs the actual host against a loopback synthetic
provider in offline mode, with OpenAI and Anthropic wire formats. It verifies all
five tool schemas, captured counters, resumed totals and prompt redaction without
credentials or model spend. Set `COPILOT_TEST_CLI` to the executable to rerun it.
It does not claim validation against a paid Copilot account or a Copilot cloud agent.

Contracts checked on 2026-10-05:

- [Gemini extension reference](https://geminicli.com/docs/extensions/reference/)
- [Gemini recording implementation](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)
- [Copilot plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference)
- [Copilot native extensions](https://docs.github.com/en/copilot/tutorials/create-an-extension)
- [Copilot event schema](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts)
