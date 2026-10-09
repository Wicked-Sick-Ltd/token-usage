# CLI and MCP reference

The reporting interface is local. `scripts/token_usage.py` is the command-line script. `scripts/mcp_server.py` is a stdio MCP server that imports it. Both use the Python 3.9+ standard library. Neither opens a network port. Dashboard, live view and JSONL export are command-line commands. The MCP server exposes five tools: `session_cost`, `history`, `insights`, `diff` and `top_consumers`.

Costs are API-price estimates. Unknown model prices stay unknown. Runtime-specific limits are in the [Codex](codex-adapter.md), [Cursor](cursor-adapter.md) and [Gemini/Copilot](gemini-copilot.md) notes. Environment variables and the pricing overlay are in the README [Configuration](../README.md#configuration) section.

## Command-line reference

```bash
python3 scripts/token_usage.py <command> [options]
```

`--help` on the program or on one command prints the argparse text. The notes below include defaults and the combinations the script rejects, which `--help` does not spell out.

### Shared `--runtime`

`report`, `json`, `history`, `insights`, `top_consumers`, `dashboard`, `live` and `export` all take:

```text
--runtime {claude,cursor,codex,gemini,copilot,auto}
```

The default is the value of `TOKEN_USAGE_RUNTIME`, or `claude` when that variable is unset. `auto` selects one runtime only when the choice is unambiguous, and one call never mixes runtimes. A Codex, Cursor, Gemini or Copilot plugin sets `TOKEN_USAGE_RUNTIME` for its MCP server. Pass `--runtime` yourself when you run the script by hand.

### `report`

Per-activity markdown table for one session.

| Argument | Default | Meaning |
|---|---|---|
| `TRANSCRIPT` | newest discovered session | Recording path for the selected runtime. |
| `--agents` | off | Add per-agent-type rows. They are subsets of the parent row. |
| `--models` | off | Add per-model rows. They are subsets of the parent row. |
| `--diff OLD NEW` | unset | Per-label cost and output deltas between two recordings. |
| `--runtime` | see above | Which host to read. |

`--diff` cannot be combined with a positional recording, `--agents` or `--models`. When `--runtime` is not `claude`, `--diff` resolves each side through the MCP `diff` tool. An unpriced side renders the cost delta as a dash (`—`).

With no path, Claude discovery uses the current directory's project, then the Cowork mount, then the newest transcript on the machine. Other runtimes use their own discovery (hook ledger and Desktop data for Cursor, rollout files for Codex, recordings for Gemini, events plus the capture ledger for Copilot). A Cursor composer id is accepted as MCP `session_id`. It is not a CLI positional argument. Pass a Cloud Agent export `.json` path when you want that file.

`json` output of a report always includes the per-model arrays. `--agents` and `--models` change the markdown table only.

### `json`

Same session selection and `--diff` rules as `report`, printed as indented JSON. There are no `--agents` or `--models` flags. For a runtime other than `claude`, the object includes `runtime`, `measurement` and `warnings`.

### `history`

Cross-session rollup.

| Argument | Default | Meaning |
|---|---|---|
| `--by {project,day,command,model}` | `project` | Grouping. |
| `--since` | unset (all sessions) | `Nd` or `YYYY-MM-DD` (optional ISO time suffix). |
| `--project` | unset | Substring of the project slug. Composes with `--by`. |
| `--json` | off | Indented JSON. |
| `--csv` | off | Raw numbers for a spreadsheet. |
| `--runtime` | see above | Which corpus to scan. |

`--json` and `--csv` together exit with an error. A relative `--since` such as `7d` adds an average $/day and a projected $/week. Day buckets use local time. Sessions with no timestamp are skipped by `--since`. `Nd` accepts a day count from 0 through 36500. A value that is not `Nd` or a real calendar date is rejected.

### `insights`

Rule-based checks. The thresholds and the baseline footnotes are in the README [Insights](../README.md#insights) section.

| Argument | Default | Meaning |
|---|---|---|
| `TRANSCRIPT` | newest discovered session | Session mode. |
| `--since` | unset | Window mode. Same values as `history`. |
| `--project` | unset | Window mode only. Substring filter. |
| `--json` | off | Indented JSON, including the `baseline` object. |
| `--runtime` | see above | Which host to read. |

Pass a transcript or `--since`, not both. `--project` without `--since` exits with an error. The budget-pace rule reads `TOKEN_USAGE_BUDGET_USD`. The CLI has no `--budget` flag.

### `top_consumers`

Costliest sessions or command labels.

| Argument | Default | Meaning |
|---|---|---|
| `--by {session,command}` | `session` | What to rank. |
| `--since` | `30d` | Same values as `history`. |
| `--project` | unset | Substring filter. |
| `--limit` | `10` | Row count. Must be 1 or greater. |
| `--json` | off | Indented JSON. |
| `--runtime` | see above | Which corpus to scan. |

A trailing `*` on a markdown cost means the row is ranked on a priced subtotal because some of its usage was on an unpriced model.

### `dashboard`

One HTML file from the same indexed history as `history`. Inline CSS and SVG only: no `<script>`, remote asset, CDN or iframe.

| Argument | Default | Meaning |
|---|---|---|
| `--since` | `30d` | Same values as `history`. |
| `--project` | unset | Substring filter. |
| `--output` | `token-usage-dashboard.html` | File path, or `-` for HTML on stdout. Progress stays on stderr. |
| `--runtime` | see above | Which corpus to scan. |

An empty match still writes a page that says so. Cursor `partial` or activity-only data is labelled as unmeasured.

### `live`

Polls and reprints the session report. Ctrl-C exits 0. There is no file watcher and no background daemon.

| Argument | Default | Meaning |
|---|---|---|
| `TRANSCRIPT` | rediscovered each tick | Recording path. |
| `--interval` | `2` | Seconds between ticks. Must be greater than 0. |
| `--iterations` | until Ctrl-C | Finite loop for scripts. Must be greater than 0. |
| `--agents` | off | Same as `report --agents`. |
| `--models` | off | Same as `report --models`. |
| `--runtime` | see above | Which host to read. |

A terminal clears with an ANSI sequence. Redirected stdout uses a UTC timestamp separator between ticks.

### `export`

JSONL aggregates for another local tool. Schema `token-usage.aggregate.v1`. Metric names follow an OTel style (`gen_ai.usage.output_tokens`, `gen_ai.estimated_cost.usd`). The file is local JSONL, separate from OTLP.

| Argument | Default | Meaning |
|---|---|---|
| `TRANSCRIPT` | unset | Session scope only. |
| `--scope {session,history}` | `history` | One session, or a corpus rollup. |
| `--by {project,day,command,model}` | `project` in history scope | History grouping. |
| `--since` | unset | History scope only. |
| `--project` | unset | History scope only. |
| `--output` | `-` (stdout) | File path, or `-`. A file is replaced atomically. |
| `--runtime` | see above | Which host to read. |

History scope rejects a positional recording. Session scope rejects `--by`, `--since` and `--project`. Session scope writes one `total` row and one row per activity label. History rows include `measurement_counts`, the scan's per-session tally. `gen_ai.estimated_cost.usd` is JSON `null` when the amount is unpriced or unmeasured. Redact lines before sharing them: they contain project slugs, command labels and model IDs.

### Hook commands

`hook`, `codex-hook` and `cursor-hook` are entry points for the host hook files. They read a hook payload on stdin, always exit 0, and are not a reporting interface.

| Command | Who runs it | What it writes |
|---|---|---|
| `hook` | Claude Code `Stop` and `SubagentStop` (`hooks/hooks.json`) | `~/.cache/token-usage/<session-id>.json`, and it may repoint `latest.json`. |
| `codex-hook` | Codex `Stop` and `SubagentStop` (`hooks/hooks-codex.json`) | `codex-<session-id>.json` in the same cache. It does not repoint `latest.json`. |
| `cursor-hook` | Cursor `beforeSubmitPrompt`, `stop`, `subagentStart`, `subagentStop` (`hooks/hooks-cursor.json`) | Append-only JSONL under `cursor/` in the cache. Prints `{}`. |

Claude and Codex hooks print nothing on stdout except an optional budget `systemMessage` when `TOKEN_USAGE_BUDGET_USD` is a number greater than zero. The nudge fires on `Stop`, once per crossed multiple. `SubagentStop` updates the ledger and does not emit the nudge. Gemini CLI and Copilot CLI do not register these hooks. The timeout in each hook file is 15 seconds.

## MCP server

Start it as a stdio process. One JSON-RPC 2.0 object per line on stdin. Replies are one JSON object per line on stdout. Diagnostics go to stderr. A JSON-RPC batch (an array) is rejected. Supported `initialize` protocol versions are `2024-11-05`, `2025-03-26` and `2025-06-18`. The server advertises tools only.

```json
{
  "mcpServers": {
    "token-usage": {
      "command": "python3",
      "args": ["/absolute/path/to/token-usage/scripts/mcp_server.py"],
      "env": {"TOKEN_USAGE_RUNTIME": "auto"}
    }
  }
}
```

Host manifests set `TOKEN_USAGE_RUNTIME` to that host (`claude`, `cursor`, `codex`, `gemini` or `copilot`). The Claude manifest also sets `TOKEN_USAGE_PROJECT_DIR` from `${CLAUDE_PROJECT_DIR}`. Tool names in the client depend on the host. Match the basename (`session_cost`, and so on).

Every tool accepts `runtime` and `format`. Unknown arguments are rejected. `format` is `json` (the default) or `markdown`. JSON results add `transcript`, `resolved_via` and `warnings` around the CLI shapes where a session was resolved. `markdown` is the rendered table, with warnings as `Warning:` lines. A missing session or a bad window comes back as a tool result with `isError`. A call whose `arguments` is present and is not an object, or an unknown method, is a JSON-RPC error (`-32602` for invalid params).

`runtime` is `claude`, `cursor`, `codex`, `gemini`, `copilot` or `auto`. When the argument is omitted, the server uses `TOKEN_USAGE_RUNTIME`, then `claude`. `auto` never mixes hosts in one call. `diff` refuses two sides that resolve to different runtimes.

`since` uses the same values as the CLI (`Nd` or a real `YYYY-MM-DD`, with an optional ISO time suffix). In tool errors the field is called `since`, not `--since`.

### `session_cost` tool

Per-activity breakdown of one session.

| Argument | Required | Meaning |
|---|---|---|
| `transcript` | no | Recording path. |
| `session_id` | no | Session id, searched in the selected runtime. Cursor composer ids go here. |
| `agents` | no | Markdown only: per-agent-type rows. |
| `models` | no | Markdown only: per-model rows. |
| `runtime` | no | See above. |
| `format` | no | `json` or `markdown`. |

Pass `transcript` or `session_id`, not both. Blank strings are errors. With neither, resolution follows the README [MCP server](../README.md#mcp-server) order: `TOKEN_USAGE_TRANSCRIPT`, then discovery. An explicit project directory does not fall through to another project.

### `history` tool

| Argument | Required | Default | Meaning |
|---|---|---|---|
| `by` | no | `project` | `project`, `day`, `command` or `model`. |
| `since` | no | all sessions | Window start. |
| `project` | no | unset | Substring filter. |
| `runtime` | no | see above | Corpus to scan. |
| `format` | no | `json` | `json` or `markdown`. |

### `insights` tool

| Argument | Required | Meaning |
|---|---|---|
| `transcript` | no | Session mode. |
| `session_id` | no | Session mode. |
| `since` | no | Window mode. |
| `project` | no | Window mode only. Pass it with `since`. |
| `budget_usd` | no | Positive finite number for the budget-pace rule. Overrides `TOKEN_USAGE_BUDGET_USD` for this call. |
| `runtime` | no | Which host to read. |
| `format` | no | `json` or `markdown`. |

Pass a transcript or session id, or pass `since`, not both. `project` without `since` is an error. `budget_usd` must be greater than 0. The CLI has no equivalent flag. It reads the environment variable only.

### `diff` tool

| Argument | Required | Meaning |
|---|---|---|
| `old` | yes | Baseline recording path or session id. |
| `new` | yes | Comparison recording path or session id. |
| `runtime` | no | Both sides use this runtime. |
| `format` | no | `json` or `markdown`. |

A value containing a path separator, or ending in `.jsonl` or `.json`, is treated as a path. Anything else is a session id. Both sides must resolve to the same runtime. An unpriced side leaves the cost delta null in JSON and `—` in markdown.

### `top_consumers` tool

| Argument | Required | Default | Meaning |
|---|---|---|---|
| `by` | no | `session` | `session` or `command`. |
| `since` | no | `30d` | Window start. |
| `project` | no | unset | Substring filter. |
| `limit` | no | `10` | Integer greater than or equal to 1. |
| `runtime` | no | see above | Corpus to scan. |
| `format` | no | `json` | `json` or `markdown`. |
