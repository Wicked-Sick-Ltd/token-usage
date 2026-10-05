# token-usage

**Where did my tokens go?** A public MIT plugin for **Claude Code**, **Codex**, **Cursor**, **Gemini CLI** and **GitHub Copilot CLI** that attributes token usage to the work that consumed it — per-activity breakdowns (slash commands and skills in Claude Code and Cowork, turns and skills in Codex, composer generations in Cursor), subagent rollups, cross-session history, optional live ledgers, and cache-aware API-price estimates.

📖 **Documentation:** this README and the [`docs/`](docs/README.md) folder are the documentation — [use cases](docs/use-cases.md), [architecture and configuration](docs/architecture.md), and the [Codex](docs/codex-adapter.md) and [Cursor](docs/cursor-adapter.md) runtime notes.

Claude Code tells you session totals (`/cost`, OTel metrics) and tools like ccusage aggregate by day/model — but nothing answers *"the PR review cost 120k tokens, the refactor cost 800k"*. token-usage fills that gap.

```
| Activity                      | Calls | Output | Input | Cache read | Cache write | Est. cost |
|-------------------------------|------:|-------:|------:|-----------:|------------:|----------:|
| `/code-review` (+5 agents)    |     1 | 180.2k |  3.1k |      42.3M |        1.2M |    $29.40 |
| (no command)                  |     4 |  31.7k |  9.9k |       3.5M |      244.5k |     $4.20 |
| `/commit`                     |     2 |   2.4k |  0.8k |     310.0k |       18.0k |     $0.27 |
| **Total**                     |       |  214k  | 13.8k |      46.1M |        1.5M |    $33.87 |
```

## Host support

| Host | Usage source | Attribution and limits |
|---|---|---|
| Claude Code / Cowork | Native transcripts and child transcripts | Original command/skill attribution, subagent rollups; Claude Code hooks and budget nudges. |
| Codex | Native rollouts and linked children | Turns/skills, cache-aware totals, local MCP and trusted Stop hooks. |
| Cursor | Hook ledger, Desktop SQLite, explicit Cloud export | Uses recorded usage when present; activity-only when the host omits counters. |
| Gemini CLI | Native JSON/JSONL recordings | User activity labels, nested child rollups; direct polling for live reports. No Stop-hook budget nudges. |
| GitHub Copilot CLI | Native events plus a small capture extension | Per-call labels and subagent subsets with `--experimental`; uncaptured shutdown totals have partial attribution. |

All five runtimes support the report/history/insights/comparison tools and CLI
live/dashboard/export commands. Token counts depend on the host's recorded fields;
unknown prices remain unknown. Copilot support targets its CLI, and Gemini support
targets Gemini CLI. See [Gemini/Copilot setup and verification](docs/gemini-copilot.md),
[Codex notes](docs/codex-adapter.md) and [Cursor notes](docs/cursor-adapter.md).

## Features

The command, hook and budget behavior below describes the original Claude integration;
the host table above identifies the corresponding capabilities elsewhere.

- **Per-command attribution** — a slash command owns every turn until the next command, so multi-turn exchanges stay attributed to the command that triggered them. `(no command)` covers only turns that occurred before the first command in the session.
- **Works in Cowork too** — in the Claude desktop app (Cowork), skills run mid-turn via the Skill tool rather than a `<command-name>` prompt; each gets its own sticky segment (e.g. `/pptx`, `/report`). Transcript discovery falls back to the Cowork sandbox mount when there's no Claude Code project directory for the cwd, so `report` just works in both.
- **Subagent rollup** — agents spawned during a command (sidechains under `<session>/subagents/`) count toward the command that spawned them, labelled `(+N agents)`.
- **Per-agent-type breakdown** — `report --agents` adds ↳ indented rows showing token usage by agent type (e.g. `↳ claude-code-guide`, `↳ general-purpose`). These rows are **subsets** of their parent row's totals, not additive — the parent already includes them all.
- **Per-model breakdown** — `report --models` adds ↳ rows splitting each activity by model (e.g. Opus main loop vs Haiku subagents). Subset rows, same as `--agents`; `json` output always carries the `models` arrays.
- **Cross-session history** — `history` rolls up token usage across all sessions, grouped by project, day (local time), command, or model, with a `--project` substring filter that composes with any grouping and `--csv` for spreadsheet-ready raw numbers. An incremental per-transcript cache (`~/.cache/token-usage/index/`) means transcripts re-parse only when they change; warm scans are near-instant.
- **Burn rate** — `history --since 7d` appends an average $/day and a projected $/week for the window.
- **Compare mode** — `report --diff OLD NEW` (and `json --diff`) shows per-label cost and output deltas between two transcripts. Deterministic ordering; when either side has unresolvable model pricing the delta renders as `—` rather than silently faking a saving.
- **Correct dedup** — Claude Code writes the same API request's usage to multiple transcript entries while streaming. token-usage dedups by `requestId`, keeping per-field maxima across duplicates (robust to partial snapshots); a naive sum overcounts ~2.5×.
- **Cache-aware cost estimates** — per-model pricing with cache reads at 0.1× the input rate (0.025× on Fable 5.1 and Mythos 5.1), 5-minute cache writes at 1.25×, and 1-hour cache writes at 2×. Mixed-model sessions (e.g. Opus main loop + Haiku subagents) are priced per model, and reports show what prompt caching saved you. Bedrock (`us.anthropic.…`) and OpenRouter-style (`anthropic/…`) model IDs resolve too.
- **Budget nudges** — set `TOKEN_USAGE_BUDGET_USD` and the Stop hook emits a `systemMessage` warning when the session's estimated cost crosses the threshold, and again at each further multiple (2×, 3×, …). At most one warning per multiple.
- **Live ledger** — Stop and SubagentStop hooks keep `~/.cache/token-usage/<session-id>.json` current after every turn and every finished subagent, so reports are instant and a statusline stays fresh even during long multi-agent turns.
- **Insights** — `insights` runs rule-based checks over the current session
  (cost outlier vs your 30-day project median, prompt-cache regressions,
  ad-hoc-work dominance, agent fan-out concentration, budget pace, unpriced
  models) or a window (`insights --since 30d`: spend trend, top mover). Pure
  arithmetic — no LLM, no network. "No notable findings." is a valid answer.
- **User pricing overlay** — drop rates into `~/.config/token-usage/pricing.json`
  to price new models the bundled table doesn't know yet; reports name any
  unpriced models they encounter.
- **MCP server** — a bundled stdio MCP server (`scripts/mcp_server.py`, stdlib only)
  exposes `session_cost`, `history`, `insights`, `diff` and `top_consumers` as tools.
  Claude Code starts it automatically with the plugin; Claude desktop can register the
  same script. JSON by default, `format: "markdown"` for the rendered tables.
  Dashboard, live, and export stay CLI-only (local file writes; not exposed via MCP).
- **Top consumers** — `top_consumers --by session|command` lists the costliest sessions
  or command labels in a window, the question `history` could not answer directly. A row
  whose cost is only a priced subtotal (some of its usage ran on an unpriced model) is
  marked `*` and footnoted, since the rows are ranked on that number.

## Privacy and data handling

token-usage runs entirely on your machine. The analyser uses only the Python standard library and
makes **no network calls**: there is no telemetry, no remote API, no update check and
no third-party service. The plugin sends no telemetry or remote requests. Reports returned through MCP enter
the host conversation and follow that host's data policy. Copilot also runs a small
JavaScript collector using its bundled SDK and Node standard library.

**What it reads** (read-only, and only for the runtime you ask about):

| Runtime | Location | Override |
|---|---|---|
| Claude Code | `~/.claude/projects/<project-slug>/<session-id>.jsonl`, plus subagent transcripts and their `.meta.json` files under `<session-id>/subagents/` | `TOKEN_USAGE_PROJECTS_DIR` |
| Cowork | the read-only sandbox mount `~/mnt/.claude/projects/…` and `/sessions/*/mnt/.claude/projects/…` | — |
| Cursor | the hook ledgers below; Cursor Desktop's `state.vscdb`, opened with SQLite `mode=ro`, and `workspaceStorage/*/workspace.json` under Cursor's user directory; a Cloud Agent export `.json` only when you pass its path | `TOKEN_USAGE_CURSOR_DIR` |
| Codex | rollout JSONL under `$CODEX_HOME/sessions` and `archived_sessions` (default `~/.codex`) | `TOKEN_USAGE_GEMINI_HOME` | `~/.gemini` (or `$GEMINI_CLI_HOME/.gemini`) | Gemini recording root. |
| `TOKEN_USAGE_COPILOT_HOME` | `$COPILOT_HOME`, else `~/.copilot` | Copilot reader root; the collector follows `COPILOT_HOME`. |
| `TOKEN_USAGE_CODEX_HOME` |
| Gemini CLI | `~/.gemini/tmp/*/chats/` JSON and JSONL recordings | `TOKEN_USAGE_GEMINI_HOME` |
| Copilot CLI | `~/.copilot/session-state/*/events.jsonl` and `~/.copilot/token-usage/*.jsonl` | `TOKEN_USAGE_COPILOT_HOME`, else `COPILOT_HOME` |
| All | the bundled `data/pricing.json` and your optional overlay `~/.config/token-usage/pricing.json` (honours `XDG_CONFIG_HOME`) | — |

Transcripts contain your prompts and code. token-usage reads them only to count tokens
and label activities. It reads no credentials or auth files.

**What it writes.** Summary caches and Claude/Codex/Cursor hook ledgers go under `~/.cache/token-usage/` (override with
`TOKEN_USAGE_LEDGER_DIR`):

- `<session-id>.json` (Claude Code) and `codex-<session-id>.json` (Codex): the session
  aggregate the Stop/SubagentStop hooks keep current. It holds token counts, cost
  estimates, activity labels (slash command, skill or agent names), the transcript path,
  and **the first 120 characters of the prompt that opened each segment**.
- `latest.json`: a best-effort symlink to the most recently written session aggregate.
- `index/`: the per-transcript summary cache used by `history`, `insights`,
  `top_consumers`, `dashboard` and `export`. It holds transcript paths, project names,
  activity labels and token/cost totals.
- `cursor/<hash>.jsonl`: the append-only Cursor hook ledger. It holds the conversation
  id, UTC timestamps, workspace roots, the hook's raw token fields, and **the first 120
  characters of each prompt and subagent task**. The directory is created owner-only
  (`0700`) where the filesystem supports it.

Copilot additionally writes `~/.copilot/token-usage/<session-id>.jsonl` under
`COPILOT_HOME`: event IDs, timestamps, counters, command/skill labels, agent IDs and
a session/project header. It stores no prompts or tool output. See the
[collector and retention details](docs/gemini-copilot.md#github-copilot-cli).

`dashboard` and `export` also write the one file you name with `--output` (dashboard
defaults to `token-usage-dashboard.html` in the current directory; `--output -` writes
to stdout).

**Retention.** token-usage never deletes or prunes anything. The data stays until you
remove it; deleting `~/.cache/token-usage/` (or your `TOKEN_USAGE_LEDGER_DIR`) removes those caches and hook ledgers, and the next run rebuilds the cache from your transcripts.

**What runs.**

- **Hooks** (Claude Code: `Stop` and `SubagentStop`; Codex: `Stop` and `SubagentStop`;
  Cursor: `beforeSubmitPrompt`, `stop`, `subagentStart` and `subagentStop`) run
  `scripts/token_usage.py` with a 15-second timeout. They fail open: they always exit 0
  and never block the session. On stdout, a Claude Code or Codex hook prints nothing but
  the optional budget `systemMessage` (only when `TOKEN_USAGE_BUDGET_USD` is set), and the
  Cursor hook prints `{}`.
- **MCP server**: `scripts/mcp_server.py`, a local stdio process the host starts. It has
  no network listener and offers only the five reporting tools listed under
  [MCP server](#mcp-server). They read the locations above and may refresh the `index/`
  cache; they never modify transcripts.

## Installation

Requires Python 3.9+ (`python3` for Claude/Cursor; `python` for Codex/Gemini/Copilot, stdlib only — no dependencies).

### Claude Code

```bash
# Test locally
claude --plugin-dir /path/to/token-usage

# Or install from the Wicked Sick marketplace
claude plugin marketplace add Wicked-Sick-Ltd/ai-marketplace
claude plugin install token-usage@wickedsick
```

Once the plugin is listed in Anthropic's plugin directory it will also install with
`/plugin install token-usage`.

The Claude manifest is `.claude-plugin/plugin.json` (MCP server, Stop hook, report skill).

### Cursor

Official install path: open **Customize** in the sidebar, find **token-usage** on the
[Cursor Marketplace](https://cursor.com/marketplace) once this plugin is published, and
choose **Install** (project or user scope). See
[Installing plugins](https://cursor.com/docs/plugins#installing-plugins).

The Cursor manifest `.cursor-plugin/plugin.json` bundles:

- stdio MCP server → `scripts/mcp_server.py`
- hooks → `hooks/hooks-cursor.json` (`version` 1 flat schema; `beforeSubmitPrompt`,
  `stop`, `subagentStart`, `subagentStop`)
- skill → `skills/report/`

**Before marketplace listing / for a git checkout today:** use **manual MCP** (below).
Cursor also documents copying a plugin into `~/.cursor/plugins/local/<name>/` and
reloading the window for local testing ([Test plugins locally](https://cursor.com/docs/plugins#test-plugins-local));
that requires the full plugin tree (including `.cursor-plugin/plugin.json`) and may be
disabled by team policy (`Allow Local Plugin Imports`).

Hook commands use `${CURSOR_PLUGIN_ROOT}`; the MCP entry uses the same variable in
`args`. See [docs/cursor-adapter.md](docs/cursor-adapter.md) for attribution sources,
limitations, and privacy.

**Manual MCP (no plugin).** Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "token-usage": {
      "command": "python3",
      "args": ["/absolute/path/to/token-usage/scripts/mcp_server.py"]
    }
  }
}
```

Use MCP tool argument `runtime: "cursor"` (or `"auto"` when Cursor artifacts are
unambiguous). Register hooks separately via Cursor settings if you want prospective
hook ledgers without the full plugin bundle.

This repository intentionally has **no** root `mcp.json`: Claude Code also reads that
file as *project-scope* MCP inside a checkout, which would register a broken server for
contributors.

### Codex

```bash
codex plugin marketplace add Wicked-Sick-Ltd/ai-marketplace --sparse .agents/plugins
codex plugin add token-usage@wickedsick
```

The Codex manifest `.codex-plugin/plugin.json` bundles the report skill
(`codex-skills/report/`), the stdio MCP server (`.mcp-codex.json`, which sets
`TOKEN_USAGE_RUNTIME=codex`) and fail-open `Stop`/`SubagentStop` hooks
(`hooks/hooks-codex.json`). Review the hooks through `/hooks` after installing;
installation never grants hook trust. See [docs/codex-adapter.md](docs/codex-adapter.md).

### Gemini CLI and GitHub Copilot CLI

From a local checkout:

```bash
gemini extensions install /absolute/path/to/token-usage
copilot plugin install /absolute/path/to/token-usage
copilot --experimental
```

Gemini discovers the report skill and MCP server through `gemini-extension.json`.
Copilot uses `.plugin/plugin.json`; experimental extensions capture its transient
usage events. The expanded integrations are unreleased. Use the checkout until the
new release and marketplace pins are published. See
[installation, privacy and limits](docs/gemini-copilot.md).

## Usage

### `/token-usage:report`

Ask for a breakdown any time:

```
/token-usage:report
```

Or just ask naturally: *"where did my tokens go this session?"*

Pass a transcript path to analyse a past session:

```
/token-usage:report ~/.claude/projects/<project-slug>/<session-id>.jsonl
```

### CLI (outside Claude Code)

The parser is a standalone script:

```bash
# Current session — markdown table
python3 scripts/token_usage.py report [transcript.jsonl]

# Add per-agent-type ↳ breakdown rows (subsets of the parent row, not additive)
python3 scripts/token_usage.py report --agents [transcript.jsonl]

# Add per-model ↳ breakdown rows (also subsets of the parent row)
python3 scripts/token_usage.py report --models [transcript.jsonl]

# Compare two transcripts — per-label cost and output deltas
python3 scripts/token_usage.py report --diff OLD.jsonl NEW.jsonl

# Machine-readable JSON
python3 scripts/token_usage.py json [transcript.jsonl]

# JSON diff between two transcripts
python3 scripts/token_usage.py json --diff OLD.jsonl NEW.jsonl

# Cross-session history
python3 scripts/token_usage.py history                         # all sessions, by project
python3 scripts/token_usage.py history --by day                # grouped by calendar day (local time)
python3 scripts/token_usage.py history --by command            # grouped by slash command
python3 scripts/token_usage.py history --by model              # grouped by model (calls = API requests)
python3 scripts/token_usage.py history --since 7d              # last 7 days (+ burn-rate footer)
python3 scripts/token_usage.py history --since 2026-06-01      # since a specific date
python3 scripts/token_usage.py history --project myrepo        # substring filter, composes with --by
python3 scripts/token_usage.py history --by project --json     # machine-readable
python3 scripts/token_usage.py history --by day --csv          # raw numbers for spreadsheets

# Insights
python3 scripts/token_usage.py insights [transcript.jsonl]     # session-mode rule checks
python3 scripts/token_usage.py insights --since 30d            # window-mode rule checks
python3 scripts/token_usage.py insights --since 30d --project myrepo
python3 scripts/token_usage.py insights --json [transcript.jsonl]

# Costliest sessions (or --by command) in the last 30 days
python3 scripts/token_usage.py top_consumers --since 30d --limit 10
python3 scripts/token_usage.py top_consumers --by command --project my-repo --json

# Cursor runtime — latest discovered session (hook ledger, then Desktop SQLite)
python3 scripts/token_usage.py report --runtime cursor
python3 scripts/token_usage.py json --runtime cursor

# Cursor runtime — explicit Cloud Agent export JSON (activity / any present usage fields)
python3 scripts/token_usage.py report --runtime cursor /path/to/cloud-export.json
python3 scripts/token_usage.py json --runtime cursor /path/to/cloud-export.json

python3 scripts/token_usage.py history --runtime cursor --by day --since 7d
python3 scripts/token_usage.py insights --runtime cursor

# Self-contained HTML dashboard from indexed history (inline CSS/SVG only — no CDN)
python3 scripts/token_usage.py dashboard [--since 30d] [--project SUBSTRING] \
  [--output token-usage-dashboard.html] [--runtime claude|cursor|codex|gemini|copilot|auto]

# Terminal live view — repolls the current or explicit session (Ctrl-C exits 0)
python3 scripts/token_usage.py live [TRANSCRIPT] [--interval 2] [--iterations N] \
  [--agents] [--models] [--runtime claude|cursor|codex|gemini|copilot|auto]

# Structured JSONL aggregates for external spend tooling (not OTLP wire format)
python3 scripts/token_usage.py export [--scope session|history] [--by project|day|command|model] \
  [--since 30d] [--project SUBSTRING] [--output usage.jsonl] [--runtime claude|cursor|codex|gemini|copilot|auto]
python3 scripts/token_usage.py export [TRANSCRIPT] --scope session --output -
```

`--runtime` accepts `claude` (the default, or whatever `TOKEN_USAGE_RUNTIME` names), `cursor`,
`codex`, `gemini`, `copilot`, or `auto`. `auto` picks one runtime when unambiguous and never mixes runtimes in one
call. Codex examples: `report --runtime codex [rollout.jsonl]`, `history --runtime codex --since 7d`.

With no argument, `report` and `json` pick the most recent session for the current directory's project; failing that, the Cowork sandbox mount; failing that too, the newest transcript under **any** project on the machine. That last step means running these outside a directory with its own Claude Code history can pick up a different project's most recent session rather than reporting "not found" — pass an explicit transcript path when it matters which session gets analysed.

### Dashboard (`dashboard`)

Builds a single portable HTML file from the same indexed history as `history`. Summary cards, an inline SVG daily-cost chart, and top project/activity/model tables are embedded with inline CSS only — no `<script>`, remote assets, CDN, iframe, or network calls. Labels, warnings, and measurement notes are HTML-escaped. Default output is `token-usage-dashboard.html`; `--output -` writes HTML to stdout (progress stays on stderr). With no matching history, the page still renders an honest empty state. Cursor `partial` or activity-only corpora disclose when cost/token cards are unmeasured rather than showing misleading zeros.

### Live mode (`live`)

Polls every `--interval` seconds (default 2), re-rendering the session report each tick. With no `[TRANSCRIPT]`, each iteration re-runs normal latest-session discovery. Interactive terminals clear with ANSI `\x1b[2J\x1b[H`; redirected stdout uses timestamp separators instead. `--iterations N` runs a finite loop for scripts and tests. **Ctrl-C exits 0.** There is no file watcher or background daemon.

### Structured export (`export`)

Emits one RFC-8259 JSON object per line with schema `token-usage.aggregate.v1` and OTel-style metric names (for example `gen_ai.usage.output_tokens`, `gen_ai.estimated_cost.usd`). This is a stable local interchange format — **not** OTLP protobuf/HTTP. Default scope is `history` (grouped by project); `--scope session` emits one `total` row plus one row per activity label. `gen_ai.estimated_cost.usd` is JSON `null` when unpriced or unmeasured. Lines include project slugs, command labels, and model IDs — redact before sharing. File output uses atomic replace; stdout streams directly.

History-scope records also carry `measurement_counts`, the scan's per-session tally (for example `{"exact": 99, "activity_only": 1}`). `measurement` alone is the worst level any session reported, so it cannot tell one weak session from a corpus nobody measured, nor either from a scan that matched nothing — an empty tally is how you spot the last case. Session-scope records cover one session and carry no tally.

### Budget nudges

Set `TOKEN_USAGE_BUDGET_USD` (a number greater than zero — anything else is ignored
with a warning on stderr) in the environment Claude Code runs hooks with (e.g. the `env` block of `~/.claude/settings.json`):

```json
{
  "hooks": { "Stop": [{ "command": "..." }] },
  "env": { "TOKEN_USAGE_BUDGET_USD": "50" }
}
```

When the session's estimated cost crosses the threshold the Stop hook emits a `systemMessage` warning, and warns again each time a further multiple of the budget is crossed (2×, 3×, …) — one warning per multiple, naming the multiple actually crossed. The budget must be a positive number. The same API-price-estimate disclaimer applies — this is a usage signal, not a billing alert.

### Statusline (optional)

`examples/statusline.sh` reads the per-session live ledger (from stdin `session_id`) and renders e.g. `⏶ 214k out · $33.87 · top: /code-review`. Wire it up with `/statusline` in **Claude Code** or merge it into your existing statusline script. Requires `jq`.

On Windows with **Claude Code**, `examples/statusline.ps1` is a dependency-free counterpart to `statusline.sh` and requires **PowerShell 7+** (`pwsh`; Windows PowerShell 5.1 is not supported). Like the bash script it reads Claude Code's statusline JSON from stdin and resolves the ledger by `session_id`:

1. `$env:TOKEN_USAGE_LEDGER_DIR/<session_id>.json` (or `~/.cache/token-usage/<session_id>.json`) — the current session's own aggregate.
2. `.../latest.json` — a pointer to the most recent session aggregate, used only as a fall back when stdin carried no usable session id or that session has no ledger yet. The hook creates it as a symlink on a best-effort basis and Windows commonly refuses, so it is often absent.

The script formats output tokens, estimated cost, and the top `by_label` activity, and **exits silently** (code 0, no stdout/stderr) when the input or the ledger is missing or malformed. Session ids are stripped to `[A-Za-z0-9_-]`, matching how the hook names the ledger, so a hostile id cannot address a file outside the ledger directory.

```text
pwsh -NoProfile -File C:/path/to/token-usage/examples/statusline.ps1
```

**Cursor** hook ledgers live as JSONL under `~/.cache/token-usage/cursor/` and write no per-session JSON or `latest.json`, so this PowerShell example is not a Cursor statusline. For a refreshing Cursor session view in the terminal, use `python3 scripts/token_usage.py live --runtime cursor` (optional `--interval`, `--iterations`).

`statusline.ps1` is always checked structurally from its source; the behavioural smoke tests additionally execute it when `pwsh` is on PATH and are skipped otherwise. No test creates a symlink.

### MCP server

The plugin ships a stdio MCP server (registered in `.claude-plugin/plugin.json` → `scripts/mcp_server.py`, stdlib only,
no install). When the plugin is enabled, Claude Code starts it and the tools appear as
`mcp__plugin_token-usage_token-usage__<tool>`:

| Tool | What it answers |
|---|---|
| `session_cost` | Per-activity breakdown of one session (`transcript` or `session_id`; defaults to the current project's newest session). The result's `transcript` key names the transcript analysed, and `resolved_via` names the rung that found it. |
| `history` | Cross-session rollup `by` project / day / command / model, with `since` and `project` filters. |
| `insights` | Rule-based findings: session mode (one session vs the project's 30-day norms) or window mode (`since`). Optional `budget_usd`. |
| `diff` | Per-activity cost and output deltas between two sessions (paths or session ids). |
| `top_consumers` | Costliest sessions or command labels in a window (`by`, `since`, `project`, `limit`). |

Every tool also takes `runtime` (`claude`, `cursor`, `codex`, `gemini`, `copilot` or `auto`; default `claude`, or
the server's `TOKEN_USAGE_RUNTIME`, which the Codex plugin sets to `codex`).

Every tool takes `format`: `json` (default — the CLI's JSON shapes plus `transcript`,
`resolved_via` and `warnings`) or `markdown` (the rendered table, with the same warnings
as `Warning:` footnotes). Failures come back as tool results with `isError`, never as
protocol errors, so a missing transcript or a bad `since` is a readable message.
Malformed *calls* are the exception: an unknown method, or `arguments` that is present
but not an object, is a JSON-RPC error (`-32602`) rather than an answer about a session
the server guessed at.

**"Current session"** resolves in this order: explicit `transcript` path → `session_id`
(searched across every project) → `TOKEN_USAGE_TRANSCRIPT` → auto-discovery. What
auto-discovery does depends on whether there is a project dir to anchor on:

- **With `TOKEN_USAGE_PROJECT_DIR`** — the plugin manifest passes
  `${CLAUDE_PROJECT_DIR}`, which reaches stdio MCP servers from Claude Code 2.1.139;
  older builds (and other hosts) leave it unexpanded or empty, which the server treats as
  unset and falls back to discovery. When it *is* set, the session is the newest
  transcript for *that project only*. There is no fall-through: a project with no
  sessions yet is an error, never a guess at some other project's session.
- **Without one** (Claude desktop, or the script run by hand): newest transcript for the
  cwd's own project → the Cowork mount → newest transcript on the machine. In those last
  two cases nobody named the session, so `resolved_via` says `cwd` / `any_project` and
  the markdown carries a note saying which project it landed on.

**Claude desktop / Cowork.** Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "token-usage": {
      "command": "python3",
      "args": ["/absolute/path/to/token-usage/scripts/mcp_server.py"]
    }
  }
}
```

or, for a Claude Code user scope outside the plugin:

```bash
claude mcp add --scope user token-usage -- python3 /absolute/path/to/token-usage/scripts/mcp_server.py
```

That route starts the server without `TOKEN_USAGE_PROJECT_DIR`, but Claude Code exports
`CLAUDE_PROJECT_DIR` to stdio servers (2.1.139+) and the server reads it as a fallback, so
"current session" still anchors on the project you are in. User-scope tools are named
`mcp__token-usage__<tool>` rather than the plugin's
`mcp__plugin_token-usage_token-usage__<tool>`.

The server reads `~/.claude/projects` on the host, so a desktop session sees the same
history the CLI does. No caching in-process: pricing overlay edits apply on the next call
(and a malformed overlay comes back in the result's `warnings`).

## Configuration

token-usage has no config file. Optional environment variables (all read from
`scripts/token_usage.py` or `scripts/mcp_server.py`):

| Variable | Default | Purpose |
|---|---|---|
| `TOKEN_USAGE_BUDGET_USD` | unset (no nudges) | Session budget in USD for the Stop-hook nudge and the `insights` budget-pace rule. Must be a number greater than 0. |
| `TOKEN_USAGE_LEDGER_DIR` | `~/.cache/token-usage` | Where ledgers, `latest.json`, the `index/` cache and Cursor hook ledgers are written. |
| `TOKEN_USAGE_PROJECTS_DIR` | `~/.claude/projects` | Claude Code transcript root. |
| `TOKEN_USAGE_TRANSCRIPT` | unset | Transcript to use when no path or session id is given. |
| `TOKEN_USAGE_RUNTIME` | `claude` | Default `--runtime` for the CLI and the MCP server. |
| `TOKEN_USAGE_PROJECT_DIR` | unset | MCP only: project to anchor "current session" on (the Claude manifest sets it from `${CLAUDE_PROJECT_DIR}`); falls back to `CLAUDE_PROJECT_DIR`. |
| `TOKEN_USAGE_CURSOR_DIR` | Cursor's user directory for the OS | Cursor Desktop data root. |
| `TOKEN_USAGE_GEMINI_HOME` | `~/.gemini` (or `$GEMINI_CLI_HOME/.gemini`) | Gemini recording root. |
| `TOKEN_USAGE_COPILOT_HOME` | `$COPILOT_HOME`, else `~/.copilot` | Copilot reader root; the collector follows `COPILOT_HOME`. |
| `TOKEN_USAGE_CODEX_HOME` | `$CODEX_HOME`, else `~/.codex` | Codex home holding `sessions/` and `archived_sessions/`. |
| `CODEX_THREAD_ID` | set by Codex | Current Codex thread, used for current-session discovery. |
| `XDG_CONFIG_HOME` | `~/.config` | Parent of the user pricing overlay `token-usage/pricing.json`. |

## Insights

`insights` runs a fixed set of rule-based checks — pure arithmetic against the current session (or, with `--since`, a window of history) — and prints only the rules that fired:

```
$ python3 scripts/token_usage.py insights
- [warn] This session ($41.20) is 4.1× your 30-day median for this project ($10.05).
- [info] 62% of spend was ad-hoc work — wrap repeated workflows in a command to make them trackable.
```

Session mode (`insights [transcript]`) checks: cost outlier vs the 30-day project median (warn ≥3×, info ≥2×; needs at least 5 prior sessions), prompt-cache regression per command (warn on a ≥20 percentage-point drop in cache-read ratio vs that command's norm), ad-hoc-work dominance (info at ≥50% of spend), unpriced models (warn), agent fan-out concentration (info at ≥70% of a command's cost coming from its subagents), and budget pace (info at 75–100% of `TOKEN_USAGE_BUDGET_USD`).

Window mode (`insights --since 7d|30d|DATE [--project SUB]`) checks: spend trend between the first and second half of the window (warn ≥+50%, info ≥±25%), the top mover behind an increase (≥30% of it), and unpriced models anywhere in the window.

No findings is a normal, healthy result — the tool prints `No notable findings.` rather than manufacturing something to say. It also says when it *couldn't* fully look: a window that matched no sessions prints `No sessions in window — nothing was scanned.`, and a trailing `(baseline: …)` names rules that were switched off — `(baseline: N prior session(s); the comparison rules need 5)` in session mode when the project has fewer than the five prior sessions rules 1–2 need, and in window mode `(baseline: no sessions in the window's first half; the trend rules need both halves)` when every matched session lands after the window's midpoint, or `(baseline: no spend in the window's first half; …)` when the first half held sessions but no spend. That qualifier is appended whether or not anything fired: several rules need no baseline, so findings are no evidence the rest ran. `--json` returns the same findings as structured data for scripting, with the counts behind the qualifier under `baseline` (`sessions`, and in window mode `first_half_sessions` / `first_half_cost` / `first_half_spend` — the last being the rules' own unrounded predicate).

Every command that scans the corpus (`history`, `top_consumers`, window `insights`, and the `insights` baseline) also says when it had nothing to scan: a `TOKEN_USAGE_PROJECTS_DIR` that is missing, is not a directory, or cannot be listed is reported as `projects_dir_missing` in `--json` and footnoted `No readable Claude Code projects directory at <path> — nothing was scanned.`, instead of a confident empty table.

## How it works

Claude Code writes every session to `~/.claude/projects/<project-slug>/<session-id>.jsonl`. Each assistant entry carries full API usage (`input_tokens`, `output_tokens`, cache read/write, model). token-usage:

1. Streams the JSONL, deduplicating assistant entries by `requestId`.
2. Starts a new segment at each real user prompt; prompts carrying a `<command-name>` marker label the segment with that command. A command's label is sticky — it covers every subsequent turn until the next command.
3. Sums each subagent transcript (`<session-id>/subagents/agent-*.jsonl`) and attributes it to the segment active at the agent's start time.
4. Prices each model's usage against `data/pricing.json`.

In **Cowork** (the Claude desktop app) the same transcript format is mounted read-only inside the session sandbox under `<mount>/.claude/projects/…` (and `/sessions/*/mnt/.claude/projects/…`); discovery uses these when no Claude Code project matches the cwd. Skills there are invoked via a `Skill` tool_use block instead of a `<command-name>` prompt, so each such block opens its own sticky segment (deduped by tool-use id). The hooks are Claude-Code-only, so in Cowork run `report` on demand rather than relying on the live ledger.

The Stop and SubagentStop hooks (`hooks/hooks.json`) re-run this after every turn and every finished subagent, writing the result to `~/.cache/token-usage/<session-id>.json` (override the directory with `TOKEN_USAGE_LEDGER_DIR`). Hook failures never block the session, and the full parse is cheap — ~0.3s even for a 74MB transcript.

The `history` subcommand builds an incremental index under `~/.cache/token-usage/index/`, keyed by file path and re-validated by (mtime, size). Transcripts are only re-parsed when their content changes; subsequent scans skip unchanged files and complete in milliseconds.

## Cost disclaimer

Costs are **API-price estimates** from the bundled `data/pricing.json` (rates as of September 2026). Subscription plans (Pro/Max) are not billed per token — treat the figure as "what this would cost at API prices". Update `data/pricing.json` if rates change; models not in the table show `—`. Rates can be added to the user pricing overlay at `~/.config/token-usage/pricing.json`, and unpriced models are named in a report footnote either way. Each entry is `{"input": $/MTok, "output": $/MTok}` with an optional `"cache_read": $/MTok` for models whose cache-hit rate is not 0.1× input (bundled for Fable 5.1 and Mythos 5.1 at $0.25). Sonnet 5 is priced at $2/$10 — its launch price, which Anthropic made permanent in September 2026 instead of raising it to $3/$15.

## Cursor: what v1 can and cannot measure

**Can (when data exists):**

- Per-activity breakdowns from hook ledgers (prospective **exact** buckets when Cursor
  sends completion token fields), read-only Desktop SQLite composers/bubbles, or an
  explicit Cloud Agent export path.
- Cross-session `history`, `insights`, and `top_consumers` with `measurement` and
  `warnings` in JSON — same aggregate shapes as Claude. Markdown names a
  `partial` or activity-only measurement, so zero buckets are never mistaken for
  genuinely free usage.
- API-price estimates from `data/pricing.json` (not subscription billing).

**Cannot:**

- Infer Cursor plan credits, invoice totals, or subscription-tier billing.
- Guarantee per-bubble `tokenCount` in SQLite (often zero; not billing truth).
- Reconstruct exact usage for sessions before hooks were enabled.
- Attribute marginal token cost to individual `@` attachments.

Details: [docs/cursor-adapter.md](docs/cursor-adapter.md).

## Limitations

- Older transcripts without `requestId` fields are summed without dedup (may overcount).
- `--since` filters sessions by their first timestamp; sessions whose transcripts carry no timestamps are skipped.
- Day buckets in `history --by day` use local time. As of 0.5.0, a session is split across every local day it touched (not just its start day), so daily figures for the same underlying data shift vs 0.4.0 — same class of change as the 0.2.0 sticky-attribution rework.
- Brand-new models render `—` and are named in a footnote until you add rates to the user overlay.
- Attribution granularity is the command or skill segment: a command owns every turn until the next one, and there is no finer per-message split within a segment.
- The pricing table holds one rate per model, so time-limited introductory or promotional prices are not modelled; edit the overlay when a rate changes.
- In Cowork the hooks don't run, so there is no live ledger, statusline or budget nudge there; reports parse the sandbox-mounted transcript on demand instead.

## License

MIT

<!-- repository-guidance:begin -->
## Contributing and agent guidance

- [Contributor guide](CONTRIBUTING.md): development workflow and validation.
- [Agent instructions](AGENTS.md): shared guidance for Codex and other coding agents.
- [Security policy](SECURITY.md): private vulnerability reporting.

## Repository license

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
<!-- repository-guidance:end -->

## Codex

Native Codex plugin support includes the report skill, local MCP server, Stop hooks, and
`--runtime codex` across reports, history, insights, comparisons, live views, dashboards
and exports. Reads local rollouts and rolls linked subagents into their parent once.
See [setup, accounting and limitations](docs/codex-adapter.md).

## Credit

Released under the MIT licence by Wicked Sick Limited. You are free to use, modify and redistribute it; please keep the copyright notice and credit Wicked Sick Limited.
