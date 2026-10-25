# token-usage

<p align="center"><img src="assets/logo.svg" alt="token-usage logo" width="96"></p>

**AI agent plugin: where did my tokens go?** token-usage is a free, public MIT plugin from Wicked Sick Limited for measuring token use across a fleet of agents and across the assistants this repository supports. It attributes recorded usage to the work that consumed it — one row per activity in a run, with subagents rolled into the activity that spawned them — so a case study of agent-driven work can say what the review cost versus the refactor, not only that the session was large.

Current release: **0.9.0**. Manifests agree on that version. `main` also carries the unreleased Antigravity plugin files listed under [Unreleased](CHANGELOG.md#unreleased). History is in [CHANGELOG.md](CHANGELOG.md).

It runs locally, calls no model, and sends nothing off the machine. Figures are cache-aware API-price estimates, not a subscription invoice. Reports do not include wall-clock duration, and they do not roll usage up across machines or accounts. Each call reads one host.

**Supported assistants**

| Assistant | What this repo ships | What it does not attribute |
|---|---|---|
| Claude Code and Cowork | `.claude-plugin/` | — |
| Cursor, including an explicit Cloud Agent export | `.cursor-plugin/` | Cursor plan credits or invoices |
| OpenAI Codex (CLI and Desktop plugin) | `.codex-plugin/` | ChatGPT subscription charges; the public Codex directory does not list this plugin |
| Google Antigravity (`agy`, Antigravity 2.0, Antigravity IDE) and legacy Gemini CLI | root `plugin.json`, `mcp_config.json`, `hooks.json`, and `gemini-extension.json` | Gemini web chat |
| GitHub Copilot CLI | `.plugin/` | Copilot web chat, the VS Code Copilot extension, Copilot credits |
| Grok Build | the Claude-format package, unchanged | Grok's own sessions |
| VS Code agent plugins | the Claude-format and Copilot packages via a marketplace | VS Code Copilot Chat's own usage |
| Any MCP client | stdio `scripts/mcp_server.py` | Hosts with no runtime adapter |

📖 **Documentation:** this README and [`docs/`](docs/README.md). Per-flag detail is in the [CLI and MCP reference](docs/reference.md). Also: [use cases](docs/use-cases.md), [architecture](docs/architecture.md), [marketplaces](docs/marketplaces.md), and the [Codex](docs/codex-adapter.md), [Cursor](docs/cursor-adapter.md) and [Gemini/Copilot](docs/gemini-copilot.md) notes.

```
| Activity                      | Calls | Output | Input | Cache read | Cache write | Est. cost |
|-------------------------------|------:|-------:|------:|-----------:|------------:|----------:|
| `/code-review` (+5 agents)    |     1 | 180.2k |  3.1k |      42.3M |        1.2M |    $29.40 |
| (no command)                  |     4 |  31.7k |  9.9k |       3.5M |      244.5k |     $4.20 |
| `/commit`                     |     2 |   2.4k |  0.8k |     310.0k |       18.0k |     $0.27 |
| **Total**                     |       |  214k  | 13.8k |      46.1M |        1.5M |    $33.87 |
```

`(+5 agents)` means five subagent transcripts were rolled into that row. They are included once, not added again.

## Quick start

Python 3.9 or newer must be available as `python3`. The analyser uses only the Python standard library.

From a checkout:

```bash
python3 scripts/token_usage.py report
python3 scripts/token_usage.py report --runtime cursor
python3 scripts/token_usage.py report --runtime codex
python3 scripts/token_usage.py report --runtime gemini
python3 scripts/token_usage.py report --runtime copilot
```

With no path, `report` uses the newest session it can find for that runtime and the current directory. Pass a recording path for one session. The default runtime is `claude`, or `TOKEN_USAGE_RUNTIME` when that is set.

Inside an agent, install the package for that host ([Installation](#installation)), then ask "where did my tokens go this session?" or run `/token-usage:report` where the report skill is loaded. Pass `runtime` on every MCP call so the answer stays on that host.

## What it does

token-usage reads usage the agent host has already recorded on this machine and attributes it to the activity that consumed it.

- **One run.** Which command, skill, turn or composer generation used the tokens. Subagents roll into the parent activity, so a fleet of agents in one run still reconciles to one total.
- **Across sessions of one host.** History by project, day, command or model; the costliest sessions or labels; rule-based insights. Day buckets and `--since` place runs in calendar time. They are not a duration timer.
- **Case-study output.** Markdown for a write-up, JSON for a script, CSV from `history`, one HTML file from `dashboard`, and JSONL from `export` for another local tool. The MCP server exposes five reporting tools. `dashboard`, `live` and `export` are command-line only.

| Host | Usage source | Attribution |
|---|---|---|
| Claude Code / Cowork | Native transcripts and child transcripts | Command and skill labels, subagent rollups. Claude Code `Stop` and `SubagentStop` hooks and budget nudges. Cowork has no hooks. |
| Codex | Native rollouts and linked children | Turns and explicit skills, cache-aware totals, local MCP, trusted `Stop` / `SubagentStop` hooks. |
| Cursor | Hook ledger, Desktop SQLite, explicit Cloud Agent export | Recorded usage when the host sent counters; activity-only when it did not. |
| Google Antigravity / Gemini CLI | Antigravity `transcript.jsonl` and Gemini CLI JSON/JSONL | User activity and skill labels, nested subagent rollups. Antigravity's root `Stop` hook updates a `gemini-` ledger. Legacy Gemini CLI has no hook; `live` polls the recording. |
| GitHub Copilot CLI | Native events plus a capture extension | Per-call labels and subagent subsets with `--experimental`. A recovered shutdown total is `partial`. No hooks. |
| Grok Build | Loads the Claude-format package | MCP tools and the report skill analyse the runtimes above. Grok's own sessions are not attributed. The `Stop` hook exits 0 and writes nothing when the payload has no `transcript_path`. |
| VS Code agent plugins / any MCP client | The bundled stdio MCP server | The five reporting tools for the `runtime` you name. No hooks. |

All five native runtimes (`claude`, `codex`, `cursor`, `gemini`, `copilot`) support report, history, insights, comparison, live, dashboard and export. Unknown prices stay unknown. `auto` picks one runtime when the choice is unambiguous and never mixes hosts in one call.

## Features

The commands below are shared. Label rules and the history index follow the runtime. The host table and the runtime notes carry the same limits.

- **Per-activity attribution.** Each turn belongs to one activity row. Rows from `--agents` and `--models` are subsets of that parent.
  - **Claude Code and Cowork.** A command or skill label sticks until the next command or skill. `(no command)` is the work before the first label. Cowork opens a skill segment from a `Skill` tool call (`/skill-name`).
  - **Codex.** Each turn opens as `(no command)`. A user message that begins with `$` or `/` labels that turn. The next turn opens unlabelled again.
  - **Cursor.** Each composer generation is its own row. The label is that generation's command, skill, prompt, or composer title. `(no activity)` is a generation with none of those.
  - **Google Antigravity and Gemini CLI.** Each user message opens a row. A message that begins with `$` or `/` names that row. Any other message is `(no command)`. When an Antigravity turn views `skills/<name>/SKILL.md`, the rest of that turn is `$<name>`.
  - **GitHub Copilot CLI.** Each `user.message` opens a row, named from a leading `$` or `/`, otherwise `(no command)`. `skill.invoked` opens `$<name>` until the next user message. A recovered shutdown total is one `partial` `(no command)` row.
- **Cross-session history.** `history` groups by project, day (local time), command or model. `--project` filters. `--csv` is for a spreadsheet. The index under `~/.cache/token-usage/index/` serves `history`, `insights`, `top_consumers`, `dashboard` and `export`. Claude Code and Cursor reuse an entry when the recording's mtime, size and pricing fingerprint still match. Codex, Antigravity, Gemini CLI and Copilot parse again on every scan: a child rollout, nested chat or capture file can grow while the indexed file stays the same, so the saved entry is not reused.
- **Burn rate.** `history --since 7d` adds an average $/day and a projected $/week for that window.
- **Compare.** `report --diff OLD NEW` shows per-label cost and output deltas. An unpriced side renders `—`.
- **Cache-aware estimates.** Cache reads at 0.1× input (0.025× on Fable 5.1 and Mythos 5.1), 5-minute cache writes at 1.25×, 1-hour writes at 2×. Mixed-model sessions are priced per model. Bedrock (`us.anthropic.…`) and OpenRouter-style (`anthropic/…`) ids resolve. Rates live in `data/pricing.json` (checked September 2026) plus an optional overlay.
- **Claude Code dedup.** The same API request is written to several transcript entries while streaming. token-usage dedups by `requestId`, keeping per-field maxima. A naive sum overcounts by about 2.5×.
- **Budget nudges.** `TOKEN_USAGE_BUDGET_USD` makes the Claude Code, Codex and Antigravity `Stop` hooks emit one `systemMessage` per crossed multiple. Cursor's hook prints `{}` and does not nudge. Copilot and legacy Gemini CLI register no hook.
- **Insights and top consumers.** `insights` is arithmetic only: no LLM and no network. `top_consumers` ranks sessions or labels. A `*` marks a row priced on only part of its usage.
- **MCP server.** `scripts/mcp_server.py` exposes `session_cost`, `history`, `insights`, `diff` and `top_consumers`. JSON by default. `format: "markdown"` returns the table. Results are the CLI's JSON shapes plus `transcript`, `resolved_via` and `warnings`.

Per-flag detail is in the [reference](docs/reference.md).

## Privacy and data handling

token-usage's analyser runs on your machine. It uses only the Python standard library. Reports, history, insights, the dashboard, live view, export and the stdio MCP server make **no network calls**: there is no update check and no third-party analytics service. Reports returned through MCP enter the host conversation and follow that host's data policy. Copilot also runs a small JavaScript collector using its bundled SDK and Node standard library.

The optional [activity stream bridge](#activity-stream-bridge-opt-in) is the one exception. It stays off until `WIZZO_COORD_URL`, `WIZZO_TWIN_API_URL`, or `~/.config/wizzo/activity-bridge.env` names a destination. When it is on, it sends turn text (the prompt, a tool-call summary, and the assistant response) to those operator endpoints. Set `WIZZO_ACTIVITY_BRIDGE=0` to force it off. The analyser never opens a connection on its own.

**What it reads** (read-only, and only for the runtime you ask about):

| Runtime | Location | Override |
|---|---|---|
| Claude Code | `~/.claude/projects/<project-slug>/<session-id>.jsonl`, plus subagent transcripts and their `.meta.json` files under `<session-id>/subagents/` | `TOKEN_USAGE_PROJECTS_DIR` |
| Cowork | the read-only sandbox mount `~/mnt/.claude/projects/…` and `/sessions/*/mnt/.claude/projects/…` | — |
| Cursor | the hook ledgers below; Cursor Desktop's `state.vscdb`, opened with SQLite `mode=ro`, and `workspaceStorage/*/workspace.json` under Cursor's user directory; a Cloud Agent export `.json` only when you pass its path | `TOKEN_USAGE_CURSOR_DIR` |
| Codex | rollout JSONL under `$CODEX_HOME/sessions` and `archived_sessions` (default `~/.codex`) | `TOKEN_USAGE_CODEX_HOME` |
| Google Antigravity / Gemini CLI | `~/.gemini/antigravity*/brain/*/.system_generated/logs/transcript.jsonl` and `~/.gemini/tmp/*/chats/` JSON/JSONL recordings (or `$GEMINI_CLI_HOME/.gemini`) | `TOKEN_USAGE_GEMINI_HOME` |
| Copilot CLI | `~/.copilot/session-state/*/events.jsonl` and `~/.copilot/token-usage/*.jsonl` | `TOKEN_USAGE_COPILOT_HOME`, else `COPILOT_HOME` |
| All | the bundled `data/pricing.json` and your optional overlay `~/.config/token-usage/pricing.json` (honours `XDG_CONFIG_HOME`) | — |

Transcripts contain your prompts and code. token-usage reads them only to count tokens and label activities. It reads no credentials or auth files. It does not call Cursor, Codex, Gemini, Copilot or Claude cloud APIs. The activity bridge, when configured, also reads the JSONL file a hook payload names and the directories passed to `sync`. It asks git for the origin slug and the current branch. It does not read git credentials or vendor auth files.

**What it writes.** Summary caches and hook ledgers go under `~/.cache/token-usage/` (override with `TOKEN_USAGE_LEDGER_DIR`):

- `<session-id>.json` (Claude Code), `codex-<session-id>.json` (Codex) and `gemini-<session-id>.json` (Antigravity `Stop`): the session aggregate the hook keeps current. It holds token counts, cost estimates, activity labels, the transcript path, segment timestamps, and **the first 120 characters of the prompt that opened each segment**.
- `latest.json`: a best-effort symlink to the most recently written Claude Code session aggregate (the statusline fallback). Codex and Gemini ledgers never repoint it.
- `index/`: the summary cache used by `history`, `insights`, `top_consumers`, `dashboard` and `export`. It holds transcript paths, project names, activity labels and token/cost totals. Claude Code and Cursor reuse an entry when the recording's mtime, size and pricing fingerprint still match. Codex, Antigravity, Gemini CLI and Copilot rewrite the entry on every scan and do not reuse it.
- `cursor/<hash>.jsonl`: the append-only Cursor hook ledger. It holds the conversation id, UTC timestamps, workspace roots, the hook's raw token fields, and **the first 120 characters of each prompt and subagent task**. The directory is created owner-only (`0700`) where the filesystem supports it.

Copilot additionally writes `~/.copilot/token-usage/<session-id>.jsonl` under `COPILOT_HOME`: event IDs, timestamps, counters, command/skill labels, agent IDs and a session/project header. It stores no prompts or tool output. See the [collector and retention details](docs/gemini-copilot.md#github-copilot-cli).

`dashboard` and `export` also write the one file you name with `--output` (dashboard defaults to `token-usage-dashboard.html` in the current directory; `--output -` writes to stdout).

When the activity bridge is configured it also writes `activity-bridge/bridge.sqlite` under `WIZZO_ACTIVITY_STATE_DIR`, or under `TOKEN_USAGE_LEDGER_DIR/activity-bridge` when that override is set, or otherwise under `~/.cache/token-usage/activity-bridge` (`XDG_CACHE_HOME` when set). The database is a watermark of delivered turns plus a spool of undelivered JSON bodies, which include prompt and assistant text until a flush succeeds. The directory is created owner-only (`0700`) where the filesystem supports it. Bearer tokens are not written there.

**Retention.** token-usage never deletes or prunes anything. The data stays until you remove it. Deleting `~/.cache/token-usage/` (or your `TOKEN_USAGE_LEDGER_DIR`) removes those caches, hook ledgers and, when the spool lives there, undelivered bridge posts. The next run rebuilds the cache from your transcripts. Remove `$COPILOT_HOME/token-usage/` separately to remove Copilot capture files. Remove the bridge state directory separately when it is not under that cache.

**What runs.**

- **Hooks** fail open: they always exit 0 and never block the session. The token-ledger command times out after 15 seconds. The activity-bridge command times out after 8 seconds and returns immediately when it is not configured.
  - Claude Code (`hooks/hooks.json`): `Stop` and `SubagentStop` run `scripts/token_usage.py hook`, then `scripts/activity_bridge.py hook`. The ledger command's stdout is empty, or one budget `systemMessage`. The bridge command prints nothing.
  - Codex (`hooks/hooks-codex.json`): the same events run `codex-hook`, then the bridge. Ledgers are `codex-` prefixed. Windows uses `python` via `commandWindows`.
  - Antigravity (root `hooks.json`): `Stop` runs `hook`. A camelCase payload (`conversationId`, `transcriptPath`, and no `transcript_path`) is read as the `gemini` runtime. Stdout is `{}`, or a budget `systemMessage` when a multiple is crossed. Whether the Antigravity UI shows that message is the host's behaviour. Antigravity does not register the activity bridge.
  - Cursor (`hooks/hooks-cursor.json`): `beforeSubmitPrompt`, `stop`, `subagentStart` and `subagentStop` run `cursor-hook`, then the bridge. Both print `{}`. The bridge reads `beforeSubmitPrompt` for the prompt and `stop` / `subagentStop` for the finished turn.
- **MCP server**: `scripts/mcp_server.py`, a local stdio process. It has no network listener. It exits quietly on stdin EOF or a broken pipe. The five tools read the locations above and may refresh the `index/` cache. They never modify transcripts.

## Installation

Requires Python 3.9 or newer. The analyser uses only the standard library.

### Platforms

macOS and Linux need `python3` on PATH. On Windows, the Python install manager provides `python3`. With the legacy python.org installer, use the `py` launcher or add a `python3` alias. Every MCP launcher runs `python3`. The Codex hook on Windows runs `python`, from `commandWindows` in `hooks/hooks-codex.json`. The Cursor, Codex, Gemini and Copilot MCP entries set `PYTHONUTF8=1` so that process uses UTF-8 on Windows.

When `TOKEN_USAGE_CURSOR_DIR` is unset, Cursor Desktop data is read from:

| Platform | Default Cursor user directory |
|---|---|
| macOS | `~/Library/Application Support/Cursor/User` |
| Linux | `~/.config/Cursor/User` |
| Windows | `%APPDATA%\Cursor\User`, or `~/AppData/Roaming/Cursor/User` when `APPDATA` is unset |

Claude Code on Windows can use the PowerShell statusline under [Statusline](#statusline-optional).

| Host | Install | Status |
|---|---|---|
| Claude Code | `claude plugin install token-usage@wickedsick` | Wicked Sick marketplace; Anthropic directory resubmission pending |
| Codex | `codex plugin add token-usage@wickedsick` | Wicked Sick marketplace. OpenAI's public plugin directory does not accept this package (local stdio plus lifecycle hooks) |
| Cursor | Cursor Marketplace, or manual MCP | Marketplace listing pending |
| Google Antigravity / Gemini CLI | `~/.gemini/config/plugins/token-usage/` or `agy plugin install` / `gemini extensions install` | Local plugin bundle or GitHub checkout |
| GitHub Copilot CLI | `copilot plugin install token-usage@wickedsick` | Wicked Sick marketplace (or `copilot plugin install /absolute/path/to/token-usage`) |
| Grok Build | `grok plugin install Wicked-Sick-Ltd/token-usage --trust` | Direct from GitHub |
| VS Code agent plugins | `chat.plugins.marketplaces` | Wicked Sick marketplace |
| Any MCP client | stdio `python3 scripts/mcp_server.py` | Manual |

### Claude Code

```bash
# Test locally
claude --plugin-dir /path/to/token-usage

# Or install from the Wicked Sick marketplace
claude plugin marketplace add Wicked-Sick-Ltd/ai-marketplace
claude plugin install token-usage@wickedsick
```

Once the plugin is listed in Anthropic's plugin directory it will also install with `/plugin install token-usage`.

The Claude manifest is `.claude-plugin/plugin.json` (MCP server, `Stop` hook, report skill). This package is the Claude Code and Cowork install of the same multi-agent plugin. Its MCP server defaults to `runtime=claude` and can still be asked for another runtime.

### Cursor

Official install path: open **Customize** in the sidebar, find **token-usage** on the [Cursor Marketplace](https://cursor.com/marketplace) once this plugin is published, and choose **Install** (project or user scope). See [Installing plugins](https://cursor.com/docs/plugins#installing-plugins).

The Cursor manifest `.cursor-plugin/plugin.json` bundles:

- stdio MCP server → `scripts/mcp_server.py` with `TOKEN_USAGE_RUNTIME=cursor`
- hooks → `hooks/hooks-cursor.json` (`version` 1 flat schema; `beforeSubmitPrompt`, `stop`, `subagentStart`, `subagentStop`)
- skill → `skills/report/`

**Before marketplace listing / for a git checkout today:** use **manual MCP** (below). Cursor also documents copying a plugin into `~/.cursor/plugins/local/<name>/` and reloading the window for local testing ([Test plugins locally](https://cursor.com/docs/plugins#test-plugins-local)); that requires the full plugin tree (including `.cursor-plugin/plugin.json`) and may be disabled by team policy (`Allow Local Plugin Imports`).

Hook commands use `${CURSOR_PLUGIN_ROOT}`; the MCP entry uses the same variable in `args`. See [docs/cursor-adapter.md](docs/cursor-adapter.md).

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

Use MCP tool argument `runtime: "cursor"` (or `"auto"` when Cursor artifacts are unambiguous). Register hooks separately via Cursor settings if you want prospective hook ledgers without the full plugin bundle.

This repository intentionally has **no** root `mcp.json`: Claude Code also reads that file as *project-scope* MCP inside a checkout, which would register a broken server for contributors.

### Cloud agents

Cursor Cloud Agents do not inherit a developer's local plugin, `~/.cursor` hooks, or `~/.cursor/mcp.json`. Those stay on the machine that installed them. A Cloud Agent runs repository hooks from `.cursor/hooks.json` on its VM. This repository does not ship that file, so a cloud run does not load token-usage unless the VM image or the repository under analysis adds it.

To report a cloud run from a machine that has the export:

```bash
python3 scripts/token_usage.py report --runtime cursor /path/to/cloud-export.json
python3 scripts/token_usage.py json --runtime cursor /path/to/cloud-export.json
```

The same path is the MCP `transcript` argument with `runtime: "cursor"`. The adapter uses token fields only when the export actually contains them. A file with usage on at least one turn is `partial`. A file with none is `activity_only`. Nothing is inferred from message length. token-usage does not call a cloud API and does not read account credentials.

If the VM has the plugin installed, or the repository under analysis contains a `.cursor/hooks.json` whose commands run `python3 …/scripts/token_usage.py cursor-hook`, the hook ledger on that VM is the higher-confidence source. Read it there, or copy the ledger, and pass `--runtime cursor`. Do not expect the developer's laptop ledger to contain the cloud run.

Ask the agent, once the MCP server is on that VM, to call `session_cost` with `runtime: "cursor"` and to include `measurement` and `warnings` in the write-up. Activity-only output is a count of turns, not a token bill.

### Codex

```bash
codex plugin marketplace add Wicked-Sick-Ltd/ai-marketplace --sparse .agents/plugins
codex plugin add token-usage@wickedsick
```

The Codex manifest `.codex-plugin/plugin.json` bundles the report skill (`codex-skills/report/`), the stdio MCP server (`.mcp-codex.json`, which sets `TOKEN_USAGE_RUNTIME=codex`) and fail-open `Stop`/`SubagentStop` hooks (`hooks/hooks-codex.json`). Review the hooks through `/hooks` after installing; installation never grants hook trust. See [docs/codex-adapter.md](docs/codex-adapter.md).

This is the OpenAI surface the repository supports: Codex CLI and the Codex Desktop plugin. It does not call the OpenAI API and it does not read ChatGPT web sessions.

### Google Antigravity, Gemini CLI and GitHub Copilot CLI

From a local checkout:

```bash
# Google Antigravity (or stage into ~/.gemini/config/plugins/token-usage/)
agy plugin install /absolute/path/to/token-usage

# Legacy Gemini CLI
gemini extensions install /absolute/path/to/token-usage

# GitHub Copilot CLI
copilot plugin install /absolute/path/to/token-usage
copilot --experimental
```

Antigravity reads root `plugin.json`, `mcp_config.json` (MCP runtime `gemini`) and `hooks.json` (`Stop`). Gemini CLI discovers the report skill and MCP server through `gemini-extension.json`. Copilot uses `.plugin/plugin.json`; experimental extensions capture its transient usage events. Gemini CLI also installs straight from GitHub (`gemini extensions install https://github.com/Wicked-Sick-Ltd/token-usage`) and Copilot from the marketplace (`copilot plugin marketplace add Wicked-Sick-Ltd/ai-marketplace`, then `copilot plugin install token-usage@wickedsick`). See [installation, privacy and limits](docs/gemini-copilot.md).

### Grok Build

Grok Build reads Claude-format plugins, so this repository installs without a separate Grok manifest:

```bash
grok plugin install Wicked-Sick-Ltd/token-usage --trust
# or through the Wicked Sick marketplace
grok plugin marketplace add Wicked-Sick-Ltd/ai-marketplace
grok plugin install token-usage@wickedsick --trust
grok mcp doctor          # token-usage should report 5 tools
```

The MCP tools and `/token-usage:report` work in Grok and report on Claude Code, Codex, Cursor, Gemini and Copilot sessions (pass `runtime`). Grok's own sessions are not attributed yet: Grok's Stop hook sends no `transcript_path`, so the hook exits 0 without writing anything. For Grok's own spend, use `grok usage <session-id>`.

### VS Code agent plugins

With `chat.plugins.enabled` on, add the Wicked Sick marketplace to your settings and install **token-usage** from the Extensions view (`@agentPlugins`):

```json
"chat.plugins.marketplaces": ["Wicked-Sick-Ltd/ai-marketplace"]
```

VS Code reads the Claude and Copilot plugin formats. You get the MCP tools and the report skill. VS Code Copilot Chat's own usage is not attributed yet.

### Any MCP client

The server is a stdlib stdio process, so any MCP client can launch it:

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

Set `TOKEN_USAGE_RUNTIME` to the runtime you want by default (`claude`, `codex`, `cursor`, `gemini`, `copilot` or `auto`), or pass `runtime` per call.

## Troubleshooting

- **`python3: command not found` / MCP server fails to start**: install Python 3.9+ and make sure `python3` is on PATH (on Windows see the note under [Installation](#installation)). Check with `python3 --version`.
- **Report is empty or shows no sessions**: the analyser reads the host's local transcripts. Run one session in the host first, then ask again. Check the paths in [docs/reference.md](docs/reference.md).
- **Plugin installed but skills/commands missing**: restart the host after installing. In Claude Code, run `/plugin` to confirm `token-usage` is enabled.
- **Codex hooks not running**: hooks need explicit trust. Review and approve them via `/hooks`.
- **Cloud run shows no tokens**: local Cursor hooks did not run on the VM. Pass the export path, or install the hook on that VM. See [Cloud agents](#cloud-agents).
- **Garbled characters on Windows**: run with `PYTHONUTF8=1` (the bundled MCP entries already set it).
- **Still stuck?** Open an issue at https://github.com/Wicked-Sick-Ltd/token-usage/issues or email hello@wickedsick.com.

## Usage

Flag defaults and every MCP argument are in the [CLI and MCP reference](docs/reference.md).

### `/token-usage:report`

```
/token-usage:report
```

Or ask: *"where did my tokens go this session?"* Name the runtime when the host did not pin it (`runtime: "cursor"`, and so on).

Pass a recording path for a past session:

```
/token-usage:report ~/.claude/projects/<project-slug>/<session-id>.jsonl
```

### CLI

```bash
python3 scripts/token_usage.py report --runtime cursor
python3 scripts/token_usage.py report --runtime cursor /path/to/cloud-export.json
python3 scripts/token_usage.py report --agents --models --runtime gemini
python3 scripts/token_usage.py report --diff OLD.jsonl NEW.jsonl --runtime codex
python3 scripts/token_usage.py json --runtime copilot path/to/events-or-ledger

python3 scripts/token_usage.py history --runtime cursor --by day --since 7d
python3 scripts/token_usage.py history --by command --project myrepo --csv
python3 scripts/token_usage.py insights --since 30d --runtime codex
python3 scripts/token_usage.py top_consumers --since 30d --limit 10

python3 scripts/token_usage.py dashboard --since 30d --runtime gemini
python3 scripts/token_usage.py live --runtime cursor --interval 2
python3 scripts/token_usage.py export --scope history --since 30d --output usage.jsonl
```

`--runtime` accepts `claude` (the default, or whatever `TOKEN_USAGE_RUNTIME` names), `cursor`, `codex`, `gemini`, `copilot`, or `auto`.

With no argument on the Claude runtime, `report` and `json` pick the most recent session for the current directory's project; failing that, the Cowork sandbox mount; failing that, the newest transcript under any project on the machine. Pass an explicit path when the session matters. Other runtimes use their own discovery and do not fall through into Claude transcripts.

For a non-Claude runtime, `report --diff` / `json --diff` resolve both sides through the same diff implementation the MCP `diff` tool uses. Both sides must be that one runtime.

### Dashboard, live, and export

`dashboard` writes one HTML file (inline CSS and SVG, no `<script>`, CDN or network). Default output is `token-usage-dashboard.html`. An empty match still renders an empty state. Unmeasured Cursor data is labelled, not shown as a free zero.

`live` polls every `--interval` seconds (default 2) and reprints the report. **Ctrl-C exits 0.** `--iterations N` is the finite loop.

`export` writes one RFC-8259 JSON object per line, schema `token-usage.aggregate.v1`, with OTel-style metric names. It is local JSONL, not OTLP and not an HTTP export. History rows include `measurement_counts`. Redact lines before sharing them: they contain project slugs, command labels and model IDs.

### Budget nudges

Set `TOKEN_USAGE_BUDGET_USD` to a number greater than zero in the environment that runs the hook (anything else is ignored, with a warning on stderr). When estimated cost crosses the threshold, the Claude Code, Codex and Antigravity `Stop` hooks emit one `systemMessage` per multiple (2×, 3×, …). `SubagentStop` updates the ledger and does not emit the nudge. This is a usage signal, not a billing alert.

### Statusline (optional)

`examples/statusline.sh` reads the Claude Code per-session ledger and renders e.g. `⏶ 214k out · $33.87 · top: /code-review`. Wire it with `/statusline` in **Claude Code**. Requires `jq`.

On Windows with **Claude Code**, `examples/statusline.ps1` is the PowerShell 7+ counterpart (`pwsh`; Windows PowerShell 5.1 is not supported). It exits silently when the ledger is missing. It is not a Cursor statusline: Cursor ledgers are JSONL and write no `latest.json`. Use `live --runtime cursor` for a refreshing Cursor view.

### MCP server

When a host plugin is enabled it starts `scripts/mcp_server.py`. Tool name prefixes differ by host. Match the basename.

| Tool | What it answers |
|---|---|
| `session_cost` | Per-activity breakdown of one session (`transcript` or `session_id`; otherwise the newest session for the pinned runtime). |
| `history` | Cross-session rollup `by` project / day / command / model, with `since` and `project` filters. |
| `insights` | Rule-based findings for one session or a window (`since`). Optional `budget_usd`. |
| `diff` | Per-activity cost and output deltas between two sessions. |
| `top_consumers` | Costliest sessions or command labels in a window. |

Every tool takes `runtime` (`claude`, `cursor`, `codex`, `gemini`, `copilot` or `auto`). The default is the server's `TOKEN_USAGE_RUNTIME`, which each host manifest sets to itself, or `claude` when unset. Every tool takes `format`: `json` (default) or `markdown`. Failures come back as tool results with `isError`. An unknown method, or `arguments` that is present but not an object, is a JSON-RPC error (`-32602`).

**"Current session"** resolves in this order: explicit `transcript` path → `session_id` → `TOKEN_USAGE_TRANSCRIPT` → auto-discovery.

- **With `TOKEN_USAGE_PROJECT_DIR`** — the Claude manifest passes `${CLAUDE_PROJECT_DIR}`, which reaches stdio MCP servers from Claude Code 2.1.139. Older builds and other hosts may leave it unexpanded; the server treats that as unset. When it is set, the session is the newest transcript for that project only. There is no fall-through.
- **Without one**: newest transcript for the cwd's project, then the Cowork mount, then the newest transcript on the machine for that runtime. `resolved_via` says which rung matched.

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

That route starts the server without `TOKEN_USAGE_PROJECT_DIR`, but Claude Code exports `CLAUDE_PROJECT_DIR` to stdio servers (2.1.139+) and the server reads it as a fallback. User-scope tools are named `mcp__token-usage__<tool>` rather than the plugin's `mcp__plugin_token-usage_token-usage__<tool>`.

The server reads local transcripts on the host. Pricing overlay edits apply on the next call.

## Configuration

Settings are the environment variables below, plus the optional pricing overlay. The table lists every name read from `scripts/token_usage.py`, `scripts/mcp_server.py`, or `scripts/activity_bridge.py`:

| Variable | Default | Purpose |
|---|---|---|
| `TOKEN_USAGE_BUDGET_USD` | unset (no nudges) | Session budget in USD for the Stop-hook nudge and the `insights` budget-pace rule. Must be a number greater than 0. |
| `TOKEN_USAGE_LEDGER_DIR` | `~/.cache/token-usage` | Where ledgers, `latest.json`, the `index/` cache and Cursor hook ledgers are written. |
| `TOKEN_USAGE_PROJECTS_DIR` | `~/.claude/projects` | Claude Code transcript root. |
| `TOKEN_USAGE_TRANSCRIPT` | unset | Transcript to use when no path or session id is given. |
| `TOKEN_USAGE_RUNTIME` | `claude` | Default `--runtime` for the CLI and the MCP server. |
| `TOKEN_USAGE_PROJECT_DIR` | unset | MCP only: project to anchor "current session" on (the Claude manifest sets it from `${CLAUDE_PROJECT_DIR}`). Read before `CLAUDE_PROJECT_DIR`. |
| `CLAUDE_PROJECT_DIR` | set by Claude Code on stdio MCP servers | MCP fallback when `TOKEN_USAGE_PROJECT_DIR` is unset. A blank value, or one that still starts with `${`, counts as unset. |
| `TOKEN_USAGE_CURSOR_DIR` | Cursor's user directory for the OS | Cursor Desktop data root. See [Platforms](#platforms). On Windows the default uses `APPDATA` when that variable is set. |
| `APPDATA` | set by Windows | Used only to locate Cursor's user directory when `TOKEN_USAGE_CURSOR_DIR` is unset. |
| `TOKEN_USAGE_GEMINI_HOME` | unset | Gemini recording root. When unset, recordings are read from `$GEMINI_CLI_HOME/.gemini` or `~/.gemini`. |
| `GEMINI_CLI_HOME` | home directory | Gemini's own home. Used only when `TOKEN_USAGE_GEMINI_HOME` is unset, as the parent of `.gemini`. |
| `GEMINI_SESSION_ID` | unset | Gemini session id for current-session discovery when no path or session id was passed. |
| `TOKEN_USAGE_COPILOT_HOME` | unset | Copilot reader root. When unset, the reader uses `COPILOT_HOME`. |
| `COPILOT_HOME` | `~/.copilot` | Copilot home. The capture extension follows this variable. The reader uses it when `TOKEN_USAGE_COPILOT_HOME` is unset. |
| `TOKEN_USAGE_CODEX_HOME` | unset | Codex home holding `sessions/` and `archived_sessions/`. When unset, `CODEX_HOME` is used. |
| `CODEX_HOME` | `~/.codex` | Codex home. Used only when `TOKEN_USAGE_CODEX_HOME` is unset. Codex sets this. |
| `CODEX_THREAD_ID` | set by Codex | Current Codex thread, used for current-session discovery. |
| `XDG_CONFIG_HOME` | `~/.config` | Parent of the user pricing overlay `token-usage/pricing.json` and of `wizzo/activity-bridge.env`. |
| `XDG_CACHE_HOME` | `~/.cache` | Used only when `WIZZO_ACTIVITY_STATE_DIR` and `TOKEN_USAGE_LEDGER_DIR` are both unset, as the parent of `token-usage/activity-bridge/`. |

### Pricing overlay

To price a model the bundled table omits, or to replace one bundled rate, create `~/.config/token-usage/pricing.json` (under `$XDG_CONFIG_HOME` when that variable is set):

```json
{
  "my-model-id": {"input": 1.0, "output": 5.0, "cache_read": 0.1}
}
```

`input` and `output` are required US dollars per million tokens. `cache_read` is optional. Each rate must be a finite number greater than or equal to zero (`0` is a valid free tier). A key replaces the bundled rate for that same key. The analyser matches the longest prefix that ends on a segment boundary, so a dated model id can use the undated key. A malformed file is ignored. An invalid entry is skipped. Either case is reported as a warning, and the remaining rates still apply. The file is read on the next CLI command or MCP call.

### Activity stream bridge (opt-in)

`scripts/activity_bridge.py` forwards Cursor, Claude Code, Codex and Grok Bot turns to an operator-run llm1 box. Nothing is sent until a destination is configured. `WIZZO_ACTIVITY_BRIDGE=0` (also `off`, `false`, or `no`) forces it off even when URLs are present.

Hook commands in `hooks/hooks-cursor.json`, `hooks/hooks.json` and `hooks/hooks-codex.json` call `activity_bridge.py hook`. Cursor's `beforeSubmitPrompt` registers the session and heartbeats the prompt. `stop` and `subagentStop` send the finished turn. Claude Code and Codex do the same from `Stop` and `SubagentStop`, reading `transcript_path`. The command always exits 0. The first failed POST in a process is spooled; later posts in that process are spooled without waiting out another timeout.

| Variable | Default | Purpose |
|---|---|---|
| `WIZZO_COORD_URL` | unset | mcp-coordination base URL, for example `http://100.105.90.34:8140`. Receives `POST /session/register`, `POST /session/heartbeat`, `POST /session/turn`, `POST /api/ai-sessions` and `POST /session/end`. |
| `WIZZO_COORD_TOKEN` | unset | Bearer token for `WIZZO_COORD_URL`. Sent as `Authorization: Bearer …`. Omit it on a trusted network. |
| `WIZZO_TWIN_API_URL` | unset | twin-api base URL, for example `http://100.105.90.34:8100`. Receives `POST /v1/ingest`. |
| `WIZZO_TWIN_TOKEN` | unset | Bearer token for `WIZZO_TWIN_API_URL`. |
| `WIZZO_ACTIVITY_BRIDGE` | on when a URL is set | `0`, `off`, `false` or `no` disables the bridge. |
| `WIZZO_ACTIVITY_STATE_DIR` | see below | Directory for `bridge.sqlite` (watermark and spool). |
| `WIZZO_ACTIVITY_HTTP_TIMEOUT` | `0.8` | Per-request timeout in seconds. The first failure marks the process offline and the rest of the batch is spooled. |
| `WIZZO_ACTIVITY_SYNC_DIRS` | unset | `os.pathsep`-separated directories for `sync` when `--sync-dir` is omitted. |
| `WIZZO_MACHINE` | hostname | `machine` field on register, heartbeat metadata and ingest. |
| `WIZZO_REPO` | git origin slug | `repo` field. `org/name` from `origin` when unset. |
| `WIZZO_BRANCH` | current git branch | `branch` field. |
| `WIZZO_SURFACE` | per runtime | Overrides `ide`, `cloud_agent`, `grok_bot`, `claude_code`, `codex` or `antigravity`. A path containing `cloud-agent` is `cloud_agent`. |
| `WIZZO_VENDOR` | per runtime | Overrides the vendor. Cursor posts `cursor`. Grok Bot posts `grok` (ingest `source_type` `grok_bot`). Claude Code posts `claude`. Codex posts `codex`. |

The same keys can live in `~/.config/wizzo/activity-bridge.env` (`$XDG_CONFIG_HOME/wizzo/activity-bridge.env`). A non-empty process environment variable wins over the file. `export ` prefixes and matching quotes are accepted. The file is how a desktop session enables the bridge without editing the plugin.

```
WIZZO_COORD_URL=http://100.105.90.34:8140
WIZZO_COORD_TOKEN=
WIZZO_TWIN_API_URL=http://100.105.90.34:8100
WIZZO_TWIN_TOKEN=
WIZZO_REPO=Wicked-Sick-Ltd/token-usage
WIZZO_BRANCH=main
```

**SSH tunnel.** When the box is only reachable through a jump host, forward the two ports and point the bridge at localhost. A down tunnel is the offline case: turns stay in the spool and the next `flush` or `sync` retries them.

```bash
ssh -J user@bastion -L 8140:127.0.0.1:8140 -L 8100:127.0.0.1:8100 user@100.105.90.34
```

```
WIZZO_COORD_URL=http://127.0.0.1:8140
WIZZO_TWIN_API_URL=http://127.0.0.1:8100
```

**What is posted.** `POST /session/register` sends `session_id`, `machine`, `cwd`, `repo`, `branch`, `vendor`, `model` and `cost_basis`. `POST /session/heartbeat` and `POST /session/turn` send `last_prompt`, `last_output`, `turn_index`, `session_cost_usd`, `model`, `vendor` and `surface`. `POST /api/ai-sessions` upserts one session summary: `vendor`, `surface`, token counts, `turns`, `repo`, `branch`, `machine`, `model`, `started_at` and `updated_at`. `cost_basis` is `payload` when the hook included a cost and `unpriced` otherwise. `POST /v1/ingest` sends `source_type`, `source_id` (`{session_id}:turn-{N}`, or `{session_id}:{subagent_id}:turn-{N}`), markdown `content` starting with `# Chat turn — {vendor}/{model}`, and `metadata` (`repo`, `branch`, `machine`, `surface`, `timestamp`). Prompt, tool and assistant fields are capped at 20,000 characters. `POST /session/end` is the `end` subcommand (and a `sessionEnd` hook payload). A normal `stop` does not end the session.

**Live hooks.** Cursor pairs `beforeSubmitPrompt` with the later `stop` for the same `generation_id` when the stop payload has no transcript. Claude Code and Codex read the latest complete turn from `transcript_path`.

**Backfill.** On the shared Grok Bot box or a Cursor Cloud Agent VM, run a one-shot scan or leave it under cron or systemd. Delivered turns are skipped by a SQLite watermark keyed on session, endpoint and body hash, so a second scan does not repeat a post. Appending a turn sends only the new one. `bin/token-usage-activity-sync` is the same `sync` command.

```bash
python3 scripts/activity_bridge.py sync \
  --sync-dir /workspace/cloud-agent-transcripts \
  --vendor cursor --surface cloud_agent

python3 scripts/activity_bridge.py sync --watch --interval 15 \
  --sync-dir /var/lib/grok/transcripts \
  --vendor grok --surface grok_bot

python3 scripts/activity_bridge.py flush
python3 scripts/activity_bridge.py end --session-id SESSION
```

JSONL may be Claude-style (`type` / `message`), flat `{role, content, tool_calls}`, or a Grok `{role: tool}` result. Blank and malformed lines are skipped. A path containing `cloud-agent` is reported as Cursor `cloud_agent` unless `--vendor` / `--surface` say otherwise. A path containing `grok` defaults to Grok Bot.

```
# /etc/systemd/system/token-usage-activity-sync.service
[Service]
EnvironmentFile=%h/.config/wizzo/activity-bridge.env
ExecStart=/usr/bin/python3 /opt/token-usage/scripts/activity_bridge.py sync --watch --interval 15 --sync-dir /workspace/cloud-agent-transcripts --vendor cursor --surface cloud_agent
Restart=on-failure
```

A cron line that does not use `--watch` exits 0 when posts are spooled, so a down llm1 does not page on every minute:

```
* * * * * python3 /opt/token-usage/scripts/activity_bridge.py --sync-dir /workspace/cloud-agent-transcripts --vendor cursor --surface cloud_agent
```

## Insights

`insights` runs a fixed set of rule-based checks and prints only the rules that fired:

```
$ python3 scripts/token_usage.py insights
- [warn] This session ($41.20) is 4.1× your 30-day median for this project ($10.05).
- [info] 62% of spend was ad-hoc work — wrap repeated workflows in a command to make them trackable.
```

Session mode (`insights [transcript]`) checks: cost outlier vs the 30-day project median (warn ≥3×, info ≥2×; needs at least 5 prior sessions), prompt-cache regression per command (warn on a ≥20 percentage-point drop in cache-read ratio vs that command's norm), ad-hoc-work dominance (info at ≥50% of spend), unpriced models (warn), agent fan-out concentration (info at ≥70% of a command's cost coming from its subagents), and budget pace (info at 75–100% of `TOKEN_USAGE_BUDGET_USD`).

Window mode (`insights --since 7d|30d|DATE [--project SUB]`) checks: spend trend between the first and second half of the window (warn ≥+50%, info ≥±25%), the top mover behind an increase (≥30% of it), and unpriced models anywhere in the window. Pass a transcript or `--since`, not both. `--project` applies only with `--since`.

No findings is a normal result: the tool prints `No notable findings.` A window that matched nothing prints `No sessions in window — nothing was scanned.` A trailing `(baseline: …)` names rules that were switched off. `--json` returns the same findings, with the counts behind that qualifier under `baseline`.

A missing or unreadable `TOKEN_USAGE_PROJECTS_DIR` is reported as `projects_dir_missing` in `--json` and footnoted, instead of an empty table that looks like zero spend.

## How it works

Every runtime goes through one adapter (`ClaudeAdapter`, `CursorAdapter`, `CodexAdapter`, `GeminiAdapter`, `CopilotAdapter`). The adapter turns a local recording into segments. The same aggregate, price and render path then builds the report. `--runtime` and the MCP `runtime` argument pick one adapter. A new host needs a new adapter; it does not need a new report command. See [architecture](docs/architecture.md).

For Claude Code the pipeline is:

1. Stream `~/.claude/projects/<project-slug>/<session-id>.jsonl`, deduplicating assistant entries by `requestId`.
2. Start a segment at each real user prompt. A `<command-name>` marker labels the segment, and that label covers every later turn until the next command.
3. Sum each subagent transcript (`<session-id>/subagents/agent-*.jsonl`) into the segment active when the agent started.
4. Price each model's usage against `data/pricing.json`.

Cowork uses the same transcript shape on a read-only sandbox mount. Skills there open a segment from a `Skill` tool call rather than a `<command-name>` prompt. Hooks do not run in Cowork, so there is no live ledger there.

Claude Code and Codex hooks re-run this after a turn or a finished subagent and write the ledger described under [Privacy and data handling](#privacy-and-data-handling). The Antigravity `Stop` hook does the same for a `transcript.jsonl`, into `gemini-<id>.json`. Cursor hooks append raw events instead of rewriting a session aggregate. A full Claude parse is about 0.3s even for a 74MB transcript.

The `history` index is keyed by file path and re-validated by mtime, size and a pricing fingerprint. Claude Code and Cursor reuse an entry when those three still match. Codex, Antigravity, Gemini CLI and Copilot parse again on every scan, because a related file can change while the indexed file stays the same. See [architecture](docs/architecture.md).

## Cost disclaimer

Costs are **API-price estimates** from `data/pricing.json` (rates as of September 2026), plus any overlay. Subscription plans are not billed per token. Models missing from both tables show `—`. Sonnet 5 is priced at $2/$10. Fable 5.1 and Mythos 5.1 set an explicit cache-read rate of $0.25 per million. The table holds one rate per model, so promotional prices are not modelled; edit the overlay when a rate changes.

## Cursor: what v1 can and cannot measure

**Can (when data exists):** per-activity breakdowns from hook ledgers, read-only Desktop SQLite, or an explicit Cloud Agent export; cross-session history, insights and top consumers with `measurement` and `warnings`; API-price estimates.

**Cannot:** Cursor plan credits or invoice totals; guaranteed per-bubble `tokenCount` in SQLite; exact usage from before hooks were enabled; marginal token cost of individual `@` attachments; token totals the Cloud export did not contain.

Details: [docs/cursor-adapter.md](docs/cursor-adapter.md).

## Limitations

- Older Claude transcripts without `requestId` are summed without dedup and may overcount.
- `--since` skips sessions whose recordings have no timestamp.
- Day buckets use local time. Since 0.5.0 a session is split across every local day it touched.
- Brand-new models render `—` until you add an overlay rate.
- Attribution stops at the activity segment, and there is no wall-clock duration. Claude Code and Cowork keep one command or skill label across the later turns inside that segment. Codex, Antigravity, Gemini CLI and Copilot open a new segment on each turn. Cursor opens a new segment on each composer generation.
- There is no multi-machine or multi-account fleet rollup. "Fleet" here means the subagents inside one run, and the assistants you report one at a time.
- Cowork does not run hooks, so there is no live ledger, statusline or budget nudge there.
- Grok's own sessions, VS Code Copilot Chat, Gemini web and Copilot web are not runtimes.

## Development and testing

No build step. Contributor rules are in [CONTRIBUTING.md](CONTRIBUTING.md). From a virtual environment:

```bash
python3 -m pip install pytest 'ruff==0.16.6'
python3 -m pytest tests/ -v -W error
ruff check scripts tests
```

CI runs that pytest command on Python 3.9 and 3.12, plus `ruff check scripts tests` with Ruff 0.16.6, `claude plugin validate .` (pinned Claude Code), and an opt-in native Copilot job. The native Copilot check locally needs `COPILOT_TEST_CLI`.

## Versioning

Versions follow [semver](https://semver.org/). The released version is the newest `## [x.y.z]` heading in [CHANGELOG.md](CHANGELOG.md), currently **0.9.0** (2026-10-18, tag `v0.9.0`). These files must carry that same `version`:

- `.claude-plugin/plugin.json`
- `.codex-plugin/plugin.json`
- `.cursor-plugin/plugin.json`
- `.plugin/plugin.json`
- `gemini-extension.json`
- `plugin.json` (Antigravity)

User-visible changes land under `## [Unreleased]` until a release. `main` currently includes unreleased Antigravity plugin manifests and the camelCase `Stop` hook on top of 0.9.0.

## Suggest a feature or report an issue

Bugs and feature requests belong on [GitHub Issues](https://github.com/Wicked-Sick-Ltd/token-usage/issues). Support email: **hello@wickedsick.com**.

- **Something broke:** open a [bug report](https://github.com/Wicked-Sick-Ltd/token-usage/issues/new?template=bug_report.md). Include the plugin version (the `version` field in the host manifest), the host, your OS, and the Python version. For a wrong total, attach a **redacted** transcript snippet: the `usage` blocks and ids, not the conversation text.
- **Something missing:** open a [feature request](https://github.com/Wicked-Sick-Ltd/token-usage/issues/new?template=feature_request.md).
- **A security problem:** do not open a public issue. Use the private report path in [SECURITY.md](SECURITY.md).

The issue templates are [bug_report.md](.github/ISSUE_TEMPLATE/bug_report.md) and [feature_request.md](.github/ISSUE_TEMPLATE/feature_request.md).

<!-- repository-guidance:begin -->
## Contributing and agent guidance

- [Contributor guide](CONTRIBUTING.md): development workflow and validation.
- [Agent instructions](AGENTS.md): shared guidance for Codex and other coding agents.
- [Security policy](SECURITY.md): private vulnerability reporting.

## Repository license

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
<!-- repository-guidance:end -->

## Licence and credit

token-usage is free, pro bono software. Copyright (c) 2026 Wicked Sick Limited, released under the [MIT licence](LICENSE). You are free to use, modify and redistribute it; please keep the copyright notice and credit Wicked Sick Limited (see [NOTICE](NOTICE)).
