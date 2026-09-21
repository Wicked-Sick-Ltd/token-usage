# Cursor runtime adapter design

**Date:** 2026-09-20
**Status:** approved by the implementation request

## Goal

Extend token-usage into a public, MIT-licensed multi-runtime tool without
regressing Claude Code or creating a private Cursor-only fork. Cursor users
should get the closest attribution its available artifacts support, and every
missing measurement must be explicit rather than estimated.

## Investigation findings

### Cursor Desktop

Cursor exposes two different classes of local artifact:

1. Official hooks receive stable `conversation_id` and per-user-turn
   `generation_id` values, the selected model, workspace roots, and a
   `transcript_path`. `beforeSubmitPrompt` includes the prompt text. Cursor's
   current interactive IDE also supplies cumulative parent-turn
   `input_tokens`, `output_tokens`, `cache_read_tokens`, and
   `cache_write_tokens` on completion hooks, although those token fields are
   not yet in the main hooks reference. They must therefore be treated as
   optional. Parent hook counts exclude subagent usage.
2. Cursor's VS Code-derived application state is stored in SQLite under:
   - macOS: `~/Library/Application Support/Cursor/User`
   - Linux: `~/.config/Cursor/User`
   - Windows: `%APPDATA%/Cursor/User`

   The global `globalStorage/state.vscdb` database has `cursorDiskKV` rows
   keyed as `composerData:<id>` and `bubbleId:<composer>:<bubble>`. Workspace
   directories contain `workspace.json` files that map opaque storage IDs to
   project folders. Composer records carry titles, modes, models, ordered
   bubble headers, and context metadata; bubble records carry prompts,
   responses, tools, and a `tokenCount` object in some versions. Cursor staff
   describe those per-bubble token counts as best-effort and often zero, not a
   billing source of truth.

Cursor also writes supplementary agent transcripts under
`~/.cursor/projects/<sanitized-project>/agent-transcripts/`. They are useful
for activity recovery but do not promise token counts.

Sources:

- [Cursor hooks](https://cursor.com/docs/hooks)
- [Cursor plugins](https://cursor.com/docs/plugins)
- [Cursor plugin reference](https://cursor.com/docs/reference/plugins)
- [Cursor tokenCount reliability response](https://forum.cursor.com/t/cursordiskkv-table-records-always-show-0-for-tokencount/155984)
- [Cursor hook usage discussion](https://forum.cursor.com/t/how-to-obtain-token-usage-per-request/168317)

The SQLite schema is reverse-engineered and version-sensitive. It is an input
adapter, never a database token-usage writes to.

### Cursor Cloud Agents

Cloud Agent exports expose conversation messages, tool calls, agent metadata,
and child-agent references. The export inspected for this design did not
promise a public token-usage field. Cloud agents do run repository command
hooks from `.cursor/hooks.json`; local user hooks and local MCP registrations
do not follow them to the VM. Team-configured MCP servers are the supported
Cloud Agent MCP surface.

V1 accepts explicit exported JSON as a best-effort activity source and uses
only token fields actually present. It does not call an undocumented cloud
API or require account credentials.

### Distribution

Cursor Plugins are the closest native distribution surface. They can bundle
MCP servers, skills, hooks, rules, and commands, and remain ordinary public Git
repositories. Agent Plugins are the more portable open format for skills and
MCP, but cannot express Cursor-specific hooks. This repository therefore adds
a thin Cursor Plugin manifest around the existing stdlib MCP server and keeps
manual MCP registration documented for users who do not install the plugin.
A VS Code extension would add packaging and update machinery without improving
the underlying usage data, so it is deferred.

## Attribution mapping

| Claude concept | Cursor concept | V1 attribution unit |
|---|---|---|
| Session transcript | Composer conversation / Cloud Agent run | Session |
| Slash-command segment | User generation within a composer | Sticky activity |
| Skill tool use | Skill/command invocation when recorded | Activity label |
| Subagent transcript | Task/subagent child conversation | Child activity, rolled up only when a parent link exists |
| Model on assistant request | Composer or bubble model | Per-model bucket |
| Prompt cache usage | Hook/cache fields when present | Cache buckets |
| Prompt context | Attachments, rules, `@` references | Metadata only |

Cursor activity labels follow this precedence:

1. explicit command or skill name recorded by Cursor;
2. subagent type/name for a child run;
3. composer title;
4. a bounded first-user-prompt summary;
5. `(no activity)`.

The label remains sticky for assistant/tool events in that generation. A new
user generation starts a new segment. `@` context is recorded as context
metadata but is not assigned token cost: Cursor does not expose the marginal
token contribution of each attachment.

## Architecture

The existing report model remains canonical:

```text
runtime artifact
    -> RuntimeAdapter.locate / iter_sessions / parse
    -> normalized segments {label, start_ts, by_model, prompt, subagents}
    -> existing aggregate / render / pricing / insights
    -> CLI and MCP
```

`ClaudeAdapter` wraps the current Claude locator and parser. Its public helper
functions remain as compatibility shims so existing callers and golden tests
continue to work.

`CursorAdapter` has three read paths, in confidence order:

1. token-usage Cursor hook ledgers under
   `~/.cache/token-usage/cursor/` (override with the existing ledger env);
2. Cursor Desktop `state.vscdb`, opened read-only with stdlib `sqlite3`;
3. an explicitly supplied Cloud Agent export JSON.

The adapter emits the same five usage buckets used today. Missing buckets stay
zero and carry a `measurement` disclosure (`exact`, `partial`, or `activity
only`) plus warnings. It never derives tokens from character counts, context
window occupancy, subscription credits, or cost.

The hook command is append-only and fail-open. It stores the minimum fields
needed for attribution, not assistant response text or attachment contents.
Events are deduplicated by `generation_id`; completion-hook cumulative totals
are recorded once rather than summed across `afterAgentResponse` and `stop`.

## CLI and MCP

`--runtime {claude,cursor,auto}` is accepted by `report`, `json`, `history`,
`insights`, and `top_consumers`. `claude` remains the default for backward
compatibility. `auto` selects Cursor only when a Cursor selector or Cursor
artifact is unambiguous; it never silently mixes both corpora.

Session-bearing MCP tools gain the same `runtime` selector. Corpus tools scan
one runtime per call. Results add `runtime`, `measurement`, and warnings while
retaining existing keys.

## Error handling and privacy

- Cursor internal-schema changes produce a warning and a partial/activity-only
  report, not a traceback or fabricated count.
- SQLite is opened read-only and never migrated, vacuumed, or written.
- Hook failures return an empty JSON response and never block the agent.
- Prompts stored in the hook ledger are truncated to the same 120-character
  report preview used by Claude parsing.
- No network calls, telemetry, credentials, or private fixtures are added.

## Testing

Synthetic fixtures cover:

- current and legacy Cursor composer/bubble shapes;
- meaningful and zero/missing `tokenCount`;
- hook events with input/output/cache fields and duplicate completion events;
- project-aware session discovery;
- explicit Cloud export activity with no usage;
- CLI `report --runtime cursor`;
- MCP `runtime: cursor`;
- Cursor history/insights degradation when only activity is measurable.

The full Claude suite remains the compatibility gate. Validation is:

```bash
python3 -m pytest tests/ -v -W error
ruff check scripts tests
git diff --check
```

## V1 limitations and roadmap

- Cursor's local database is private implementation detail and may change.
- Historical bubble token counts can be absent or zero.
- Hook usage is prospective and interactive-IDE coverage may differ from CLI.
- Parent hook totals exclude subagents; child usage is only exact when the
  child's own events are captured and linked.
- Cloud export token totals are not assumed.
- Cursor subscription billing is not inferred from provider API pricing.

The runtime interface deliberately leaves room for Gemini, Codex, and other
adapters. Each future adapter must document its authoritative artifact,
deduplication key, token semantics, and unavailable dimensions before it can
claim parity.
