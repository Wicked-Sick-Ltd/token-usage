# Gemini CLI and GitHub Copilot CLI

Both hosts use the existing local Python analyser and its five stdio MCP tools.
Install Python 3.9+ with a working `python` command. No Python dependencies are
required. These integrations target the CLI products, not Gemini web chat,
Gemini Code Assist, Copilot web chat, or the VS Code Copilot extension.

## Gemini CLI

From a checkout, run `gemini extensions install /absolute/path/to/token-usage`.
After publication, use
`gemini extensions install https://github.com/Wicked-Sick-Ltd/token-usage`.
Check `gemini extensions list` and `gemini mcp list`, then ask for a token report.
The manifest sets `TOKEN_USAGE_RUNTIME=gemini`; explicit tool calls can pass
`runtime: "gemini"` and a `session_id` or `transcript` path.

The adapter reads legacy JSON snapshots and current append-only JSONL recordings
under `~/.gemini/tmp/*/chats/`. `TOKEN_USAGE_GEMINI_HOME` points directly at an
alternative `.gemini` directory. Gemini's own `GEMINI_CLI_HOME` instead names the
parent home directory. Project filtering compares Gemini's SHA-256 project hash;
history displays that hash rather than inventing a project name.

Repeated message IDs count once, using the largest recorded counters. Cached
prompt tokens are separated from uncached input. Tool-use prompt tokens count as
input; thought tokens count as output. Child and grandchild sessions roll into
the parent once, with child rows shown as subsets. Child ownership uses its start
timestamp and the latest preceding parent activity; Gemini does not record a
durable command invocation relationship for this purpose.

Context rewinds retain the API work already consumed. Missing counters produce
`partial` or `activity_only` reports. Deleted recordings cannot be reconstructed;
legacy `$set.messages` checkpoints are flagged as incomplete. Unknown model prices
remain unknown: add an [overlay](../README.md#configuration) to price a model.
Gemini does not receive the Claude Stop-hook ledger or budget notification.
`live --runtime gemini` polls the native recording instead.

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
python scripts/token_usage.py report --runtime gemini --models --agents
python scripts/token_usage.py history --runtime copilot --since 7d
python scripts/token_usage.py live --runtime copilot --iterations 1
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
It does not claim validation against a paid Copilot account or cloud agent.

Contracts checked on 2026-10-05:

- [Gemini extension reference](https://geminicli.com/docs/extensions/reference/)
- [Gemini recording implementation](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts)
- [Copilot plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference)
- [Copilot native extensions](https://docs.github.com/en/copilot/tutorials/create-an-extension)
- [Copilot event schema](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts)
