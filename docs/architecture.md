# Architecture

token-usage is one AI agent plugin with a package per assistant. It uses two
Python 3.9+ standard-library scripts plus per-host packaging. There is no build
step or Python dependency. Copilot supplies the SDK for its small JavaScript
collector. The same checkout is the Claude Code, Codex, Cursor, Antigravity,
Gemini CLI and Copilot CLI package, and it also runs as a standalone CLI or a
stdio MCP server. Grok Build and VS Code agent plugins load an existing package;
they are not extra runtimes.

## Repository layout

| Path | What it is |
|---|---|
| `scripts/token_usage.py` | The whole analyser: transcript parsing and deduplication, attribution, pricing, the history index, insights, rendering, and every CLI subcommand (`report`, `json`, `history`, `insights`, `top_consumers`, `dashboard`, `live`, `export`, plus the `hook`, `codex-hook` and `cursor-hook` entry points). `hook` also serves the Antigravity `Stop` payload. |
| `scripts/mcp_server.py` | A stdio MCP server that imports `token_usage.py` and exposes `session_cost`, `history`, `insights`, `diff` and `top_consumers`. |
| `data/pricing.json` | The bundled per-model API prices. The user overlay `~/.config/token-usage/pricing.json` adds to it. |
| `.claude-plugin/plugin.json` | Claude Code manifest. It declares the MCP server inline; the skill in `skills/` and the hooks in `hooks/hooks.json` load from their default locations. |
| `.codex-plugin/plugin.json` | Codex manifest: `codex-skills/`, `.mcp-codex.json` and `hooks/hooks-codex.json`. |
| `.cursor-plugin/plugin.json` | Cursor manifest: `skills/report`, `hooks/hooks-cursor.json` and an inline MCP server. |
| `gemini-extension.json` | Legacy Gemini CLI manifest, MCP server and context instructions. |
| `plugin.json`, `mcp_config.json`, `hooks.json` | Google Antigravity plugin manifest, MCP server and `Stop` hook. These are unreleased relative to tag `v0.9.0`; see [CHANGELOG](../CHANGELOG.md). |
| `.plugin/plugin.json` | Copilot CLI manifest, MCP server, skill and native extension. |
| `copilot-extensions/token-usage/extension.mjs` | Saves transient Copilot usage locally using the host-bundled SDK. |
| `skills/report/`, `codex-skills/report/` | The report skill (`/token-usage:report` in Claude Code), and its Codex counterpart. |
| `hooks/` | Claude, Codex and Cursor hook files. Antigravity's hook file is the repository-root `hooks.json`. All run `scripts/token_usage.py` and fail open. Copilot registers no hooks. |
| `examples/` | Optional statusline scripts for Claude Code (`statusline.sh` needs `jq`; `statusline.ps1` needs PowerShell 7+). |
| `tests/` | The pytest suite, with synthetic fixtures only. |
| `docs/` | This documentation. `docs/superpowers/` holds dated design records. |

## Runtime adapters

Every runtime goes through a `RuntimeAdapter` (`scripts/token_usage.py`) with five methods:
`locate` (find one session), `iter_sessions` (enumerate the corpus), `parse` (turn a
session into attributed segments), `project` and `describe`. The adapters are
`ClaudeAdapter` (Claude Code and Cowork transcripts), `CursorAdapter` (hook ledgers,
read-only Desktop SQLite and Cloud Agent exports), `CodexAdapter` (Codex rollouts),
`GeminiAdapter` (Google Antigravity `transcript.jsonl` logs, legacy Gemini CLI JSON/JSONL recordings, and subagents/children), and `CopilotAdapter` (native
events plus a minimal usage ledger).
`--runtime` and the MCP `runtime` argument pick one; `auto` picks a runtime only when the
choice is unambiguous. Reports, history, insights, the dashboard and export work on the
adapters' common segment shape, so they need no runtime-specific code. A new runtime
needs a new adapter; [cursor-adapter.md](cursor-adapter.md) describes the contract.

## How a report is built

The README's [How it works](../README.md#how-it-works) section describes the shared
adapter path and the Claude Code pipeline: stream the transcript, deduplicate by
`requestId`, segment at each real user prompt or Skill invocation, roll subagent
transcripts into the segment active when they started, then price each model's usage.
Other adapters normalise their recordings into the same segment shape before that
pricing step. The report does not record wall-clock duration.

## Local state

The hooks keep a per-session ledger, and the corpus commands keep a summary
index. Both live under `~/.cache/token-usage/`, which `TOKEN_USAGE_LEDGER_DIR` overrides;
the transcript roots have their own overrides. The README's
[Configuration](../README.md#configuration) section lists every environment variable.
Claude Code (`cached_summary`) and Cursor (`cached_adapter_summary`) reuse an entry
when the indexed file's modification time, size and pricing fingerprint still match,
so a rate change re-prices those sessions. Codex, Antigravity, Gemini CLI and Copilot
go through `cached_adapter_summary` too, but that function skips the hit for adapter
names `codex`, `gemini` and `copilot` and parses again on every scan. A child rollout,
nested chat or Copilot capture file can grow while the indexed file stays the same.
Copilot capture files live separately under
`$COPILOT_HOME/token-usage/`; see [data handling](gemini-copilot.md).

## Reusing the pieces

- `scripts/token_usage.py` can be copied into another project and run directly to parse
  Claude Code transcripts, deduplicate usage, attribute it per command and estimate cost.
- `scripts/mcp_server.py` can be registered with any MCP host that starts stdio servers;
  it needs `token_usage.py` beside it.
- The pricing overlay is the extension point for new or private models.
