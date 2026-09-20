# Cursor adapter — evidence and contract

This note records what is **official**, what was **reverse-engineered**, what we
**observed** in exports, and how a future **Gemini/Codex** adapter should plug
in. It supports the public MIT `token-usage` Cursor Plugin
(`.cursor-plugin/plugin.json`) and the `CursorAdapter` in `scripts/token_usage.py`.

## Official Cursor surfaces (authoritative)

| Surface | What token-usage uses | Source |
| --- | --- | --- |
| **Hooks** | `beforeSubmitPrompt`, `stop`, `subagentStart`, `subagentStop` via `hooks/hooks-cursor.json` | [Cursor hooks](https://cursor.com/docs/hooks) |
| **Plugin bundle** | MCP server, hooks file, `skills/report/` | [Cursor plugins](https://cursor.com/docs/plugins), [plugin reference](https://cursor.com/docs/reference/plugins) |
| **MCP in plugins** | Inline `mcpServers` in `.cursor-plugin/plugin.json`; `${CURSOR_PLUGIN_ROOT}` in `command`/`args` | Plugin reference (MCP servers) |

Hook payloads (when present) include stable `conversation_id`, per-turn
`generation_id`, model, workspace roots, and `transcript_path`. Completion hooks
may also carry cumulative `input_tokens`, `output_tokens`, `cache_read_tokens`,
and `cache_write_tokens`. Those token fields are **optional** and are not yet
documented on the main hooks reference page; treat them as best-effort when
present. They are stored **verbatim**: subtracting the cache buckets out of a
cumulative `input_tokens` happens at parse time, where a payload whose cache
exceeds its input can be reported as `partial` instead of silently clamped.

Ledger records also carry the workspace roots, which give a hook-captured
session its project identity (`history --by project`, `--project`, and
`project_dir` discovery); a session whose payloads carried none falls back to
the `cursor-hooks` project.

The hook command is **fail-open**: it appends to a local ledger and never blocks
the agent. Prompt text in the ledger is truncated to the same 120-character
preview used for Claude reports.

## Reverse-engineered SQLite (input only)

Cursor Desktop stores VS Code–derived state under:

| OS | Path |
| --- | --- |
| macOS | `~/Library/Application Support/Cursor/User` |
| Linux | `~/.config/Cursor/User` |
| Windows | `%APPDATA%/Cursor/User` |

`globalStorage/state.vscdb` table `cursorDiskKV` holds keys such as
`composerData:<id>` and `bubbleId:<composer>:<bubble>`. Workspace folders map
through `workspaceStorage/*/workspace.json`.

This schema is **not** a public API. `CursorAdapter` opens the database
**read-only** with stdlib `sqlite3`, never migrates or writes. Discovery selects
only `composerData:%` rows, so a scan never pulls every bubble blob through
memory, and a missing or renamed `cursorDiskKV` degrades to an empty,
warned-about read rather than a traceback. Per-bubble `tokenCount` values are
described by Cursor staff as often zero and not billing
truth ([forum discussion](https://forum.cursor.com/t/cursordiskkv-table-records-always-show-0-for-tokencount/155984)).

Override the root in tests with `TOKEN_USAGE_CURSOR_DIR`.

## Observed Cloud Agent export

A sample Cloud Agent JSON export (see `tests/fixtures/cursor/cloud-export.json`)
carries messages, tool calls, and child-agent references. It did **not** expose
a stable public token-usage field. V1 accepts an explicit export path as an
**activity-only** source and uses token fields only when they are actually
present — no undocumented cloud API and no account credentials.

Cloud Agents run repository hooks from `.cursor/hooks.json` on the VM; a user's
local `~/.cursor` hooks and local MCP registrations do not automatically follow
the agent.

## Attribution mapping (Claude → Cursor)

| Claude concept | Cursor concept | V1 unit |
| --- | --- | --- |
| Session transcript | Composer / Cloud run | Session |
| Slash-command segment | User generation in composer | Sticky activity |
| Skill tool use | Skill/command when recorded | Activity label |
| Subagent transcript | Task/subagent child | Child activity (rollup when linked) |
| Model on request | Composer/bubble model | Per-model bucket |
| Prompt cache | Hook/cache fields when present | Cache buckets |
| `@` context | Attachments, rules | Metadata only (no marginal token cost) |

Activity label precedence: explicit command/skill → subagent type → composer
title → bounded first-user-prompt summary → `(no activity)`.

## Read path confidence (CursorAdapter)

1. **Hook ledger** — `~/.cache/token-usage/cursor/<sanitised-prefix>_<hash>.jsonl`
   (override with `TOKEN_USAGE_LEDGER_DIR`). The filename hashes the raw
   conversation id for path safety, so each record also stores that id verbatim;
   it is what deduplicates a hook-captured conversation against Cursor's own
   composer row for the same conversation, and what `session_id` resolves.
   Prospective **exact** attribution when completion hooks include token fields
   (**partial** when a payload's cache buckets exceed its reported input, which
   is disclosed rather than clamped); dedupe by `generation_id`.
2. **Desktop SQLite** — historical composers/bubbles; `partial` when any
   `tokenCount` is usable and `activity_only` otherwise. It never claims
   `exact`: per-bubble counts are best-effort, not a billing source.
3. **Explicit Cloud export JSON** — activity and any present usage fields only.

CLI and MCP accept `--runtime` / `runtime`: `claude` (default), `cursor`, or
`auto`. One call never mixes corpora.

## V1 can measure

- Per-activity tables and JSON when hook ledgers or SQLite yield segments.
- Optional **exact** buckets from hook completion fields when Cursor supplies them.
- Cross-session `history`, `insights`, and `top_consumers` over Cursor sources
  when data exists (insights does not invent cost findings on activity-only data).
- API-price **estimates** from `data/pricing.json` — same disclaimer as Claude;
  **not** Cursor subscription billing.

## V1 cannot measure (explicit)

- Subscription credits, invoice totals, or plan-tier billing.
- Reliable per-bubble tokens from SQLite when `tokenCount` is zero or missing.
- Retrospective exact usage before hooks were installed.
- Cursor's parent completion hook reports the parent turn only, so a subagent is
  measured only when its own `subagentStop` event is captured and linked. Each
  captured child is merged into its spawning segment exactly once, and the
  per-agent rows stay subsets of that segment's total.
- Marginal token cost of individual `@` attachments.
- Guaranteed Cloud export token totals.

## Privacy and security

- No network calls, telemetry, or bundled credentials.
- SQLite and exports are read locally; hook ledger stores truncated prompts only.
- See [SECURITY.md](../SECURITY.md) for hook commands and ledger paths.

## Future adapter contract (Gemini, Codex, others)

Any new runtime adapter added to `get_runtime_adapter()` should document, before
claiming parity with Claude or Cursor:

1. **Authoritative artifact** — official hook, transcript, or export path.
2. **Deduplication key** — e.g. `requestId`, `generation_id`, message id.
3. **Token semantics** — which buckets exist, cumulative vs per-turn, cache fields.
4. **Unavailable dimensions** — listed explicitly in reports (`measurement`,
   `warnings`).
5. **Privacy boundary** — what is persisted, truncated, or never stored.

Adapters implement `RuntimeAdapter` (`locate`, `iter_sessions`, `parse`,
`project`, `describe`) and must not infer tokens from text length, context
window occupancy, credits, or cost.

## Manual MCP install (without the plugin)

Users who do not install the Cursor Plugin can register the same stdio server in
`~/.cursor/mcp.json` (absolute path to `scripts/mcp_server.py`). Pass
`runtime: "cursor"` on MCP tools or `--runtime cursor` on the CLI. No root
`mcp.json` in this repository — Claude Code would treat it as project-scope
config inside a checkout.

## Dashboard, live, and export (0.7)

These CLI paths reuse the same runtime adapters and measurement disclosures as
`report` / `history`. They are **local file operations** — no network, CDN, or
telemetry — and are intentionally **not** exposed through the stdio MCP server
(arbitrary output paths would widen the write surface).

| Command | Role | Cursor notes |
| --- | --- | --- |
| `dashboard` | Static HTML archive of indexed history | Activity-only or `partial` corpora disclose unmeasured cost/token cards |
| `live` | Polling refresh of one session report | Same session discovery and measurement warnings as `report` |
| `export` | JSONL aggregates (`token-usage.aggregate.v1`) | `measurement` and `warnings` preserved; redact labels before sharing |

Export uses OTel-style **metric names**, not OTLP protobuf/HTTP. A future OTLP
exporter can map this stable schema without breaking JSONL consumers.

## Roadmap after 0.7

| Topic | Status |
| --- | --- |
| Authenticated HTTP/SSE MCP transport | **0.7.1+** — needs binding, auth, origin policy, concurrency, shutdown, and stream resumption design |
| True OTLP wire export | **0.7.1+** — maps from `token-usage.aggregate.v1`, not a separate invented schema |
| User-configurable insight thresholds | **Deferred (YAGNI)** — fixed rules stay predictable |
| Fleet / multi-machine aggregation | **Out of scope** |
| LLM-generated insights | **Out of scope** — `insights` stays rule-based arithmetic |
| Gemini, Codex, other runtimes | **Future adapters** — implement `RuntimeAdapter` per the contract above; dashboard/export need no runtime-specific code once summaries are canonical |

Windows statusline parity lives in `examples/statusline.ps1` (reads
`latest.json` under `TOKEN_USAGE_LEDGER_DIR` or `~/.cache/token-usage/`).
