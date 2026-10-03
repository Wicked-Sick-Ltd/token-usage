# Architecture

token-usage is two Python 3.9+ standard-library scripts plus per-host packaging. There is
no build step and no third-party dependency, so the same checkout works as a Claude Code,
Codex or Cursor plugin, or as a standalone CLI.

## Repository layout

| Path | What it is |
|---|---|
| `scripts/token_usage.py` | The whole analyser: transcript parsing and deduplication, attribution, pricing, the history index, insights, rendering, and every CLI subcommand (`report`, `json`, `history`, `insights`, `top_consumers`, `dashboard`, `live`, `export`, plus the `hook`, `codex-hook` and `cursor-hook` entry points). |
| `scripts/mcp_server.py` | A stdio MCP server that imports `token_usage.py` and exposes `session_cost`, `history`, `insights`, `diff` and `top_consumers`. |
| `data/pricing.json` | The bundled per-model API prices. The user overlay `~/.config/token-usage/pricing.json` adds to it. |
| `.claude-plugin/plugin.json` | Claude Code manifest. It declares the MCP server inline; the skill in `skills/` and the hooks in `hooks/hooks.json` load from their default locations. |
| `.codex-plugin/plugin.json` | Codex manifest: `codex-skills/`, `.mcp-codex.json` and `hooks/hooks-codex.json`. |
| `.cursor-plugin/plugin.json` | Cursor manifest: `skills/report`, `hooks/hooks-cursor.json` and an inline MCP server. |
| `skills/report/`, `codex-skills/report/` | The report skill (`/token-usage:report` in Claude Code), and its Codex counterpart. |
| `hooks/` | One hooks file per host. All run `scripts/token_usage.py` and fail open. |
| `examples/` | Optional statusline scripts for Claude Code (`statusline.sh` needs `jq`; `statusline.ps1` needs PowerShell 7+). |
| `tests/` | The pytest suite, with synthetic fixtures only. |
| `docs/` | This documentation. `docs/superpowers/` holds dated design records. |

## Runtime adapters

Every runtime goes through a `RuntimeAdapter` (`scripts/token_usage.py`) with five methods:
`locate` (find one session), `iter_sessions` (enumerate the corpus), `parse` (turn a
session into attributed segments), `project` and `describe`. The adapters are
`ClaudeAdapter` (Claude Code and Cowork transcripts), `CursorAdapter` (hook ledgers,
read-only Desktop SQLite and Cloud Agent exports) and `CodexAdapter` (Codex rollouts).
`--runtime` and the MCP `runtime` argument pick one; `auto` picks a runtime only when the
choice is unambiguous. Reports, history, insights, the dashboard and export work on the
adapters' common segment shape, so they need no runtime-specific code. A new runtime
needs a new adapter; [cursor-adapter.md](cursor-adapter.md) describes the contract.

## How a report is built (Claude Code)

The README's [How it works](../README.md#how-it-works) section describes the pipeline:
stream the transcript, deduplicate by `requestId`, segment at each real user prompt or
Skill invocation, roll subagent transcripts into the segment active when they started,
then price each model's usage.

## Local state

The hooks keep a per-session ledger, and the corpus commands keep an incremental summary
index. Both live under `~/.cache/token-usage/`, which `TOKEN_USAGE_LEDGER_DIR` overrides;
the transcript roots have their own overrides. The README's
[Configuration](../README.md#configuration) section lists every environment variable.
The index is keyed by transcript path and revalidated by modification time, size and a
pricing fingerprint, so a rate change re-prices cached sessions.

## Reusing the pieces

- `scripts/token_usage.py` can be copied into another project and run directly to parse
  Claude Code transcripts, deduplicate usage, attribute it per command and estimate cost.
- `scripts/mcp_server.py` can be registered with any MCP host that starts stdio servers;
  it needs `token_usage.py` beside it.
- The pricing overlay is the extension point for new or private models.
