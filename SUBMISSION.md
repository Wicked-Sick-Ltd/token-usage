# Marketplace submission — token-usage

Submission target: Anthropic **community** plugin marketplace
(`anthropics/claude-plugins-community`, install as `@claude-community`).
The official marketplace (`anthropics/claude-plugins-official`) is curated
separately and has no application process; the forms below do not add plugins
there.

**Last verified:** 2026-09-19 against plugin version **0.6.1**
(`.claude-plugin/plugin.json`) at `d6121a76180161855efb876e1a3f9f7b1465d3bf`.
The community catalog README still advertises
https://clau.de/plugin-directory-submission as the submit URL. That short link
currently lands on the Create plugins docs, which list the live in-app forms:

- claude.ai: https://claude.ai/admin-settings/directory/submissions/plugins/new
  (Team/Enterprise org with directory management access)
- Console: https://platform.claude.com/plugins/submit
  (individual authors)

This file remains a live copy-paste source for those forms. Page-1 field labels
below were confirmed from the public directory form; keep the answers intact
rather than shortening them.

---

## Form answers — page 1 (fields confirmed from the live form)

Copy-paste ready, in the order the form asks them.

### Plugin homepage

> *"The public homepage or documentation site for your plugin."*

```
https://discovery.wickedsick.com/token-usage-claude-code-plugin-documentation
```

(Kept current for v0.6.1. Fallback if a repo URL is preferred:
`https://github.com/Wicked-Sick-Ltd/token-usage`.)

### Plugin name

> *"You should check your name is not already taken."*

```
token-usage
```

### Plugin description

> *"A clear, concise description of what your plugin does."*

```
A token profiler, not another cost meter. Claude Code tells you how much a
session cost; token-usage tells you what the work was: it parses the session
transcript and attributes every token to the slash command, skill, or subagent
that consumed it — "the code review cost 180k tokens, the ad-hoc work cost
30k" — with subagent fan-outs rolled up into the command that spawned them and
a "(no command)" bucket so totals always reconcile.

Key features: per-command/per-skill attribution (works in Claude Code and
Cowork); report breakdowns by agent type (--agents) and model (--models);
cross-session history by project, day, command, or model with a --project
filter, CSV export, and a burn-rate footer; --diff compare mode for
before/after prompt optimisation; rule-based insights (no LLM) and
top_consumers for the costliest sessions or command labels in a window; a live
per-session ledger maintained by Stop and SubagentStop hooks powering an
optional statusline segment and opt-in budget nudges; a bundled stdio MCP
server (session_cost, history, insights, diff, top_consumers) that Claude Code
starts with the plugin; and cache-aware per-model cost estimates (cache reads
0.1x — 0.025x on Fable/Mythos 5.1 — 5-minute cache writes 1.25x, 1-hour writes
2x), clearly labelled as API-price estimates for subscription users. Python
3.9+ stdlib only — no dependencies, no network calls, no telemetry.
```

### Example use cases

> *"Provide examples of how users can use your plugin."* (format: `Example 1: ...\nExample 2: ...`)

```
Example 1: "Where did my tokens go this session?" — at the end of a long session, run /token-usage:report (or just ask in natural language) and get a per-command table: instantly see the code review consumed 6x everything else combined.
Example 2: Deciding whether a heavy workflow is worth it — multi-agent commands (deep code reviews, research fan-outs) are powerful but expensive. token-usage shows their true deduped, cache-aware cost, so "run on every PR" vs "reserve for releases" is decided with real numbers.
Example 3: Profiling slash commands and skills you author — see exactly what each invocation costs (cache-write amplification and subagent fan-out included), break it down by agent type with --agents or by model with --models, and compare before/after with --diff when optimising prompts.
Example 4: "Which project ate the tokens this week?" — history --by project --since 7d (or by day, command, or model) rolls up every session on the machine, with a burn-rate footer and CSV export; an incremental cache keeps warm runs near-instant.
Example 5: Live cost awareness while you work — the optional statusline segment renders "214k out · $33.87 · top: /code-review", updated every turn from the live ledger; set TOKEN_USAGE_BUDGET_USD for nudges when a session crosses your budget (re-warned at each multiple).
Example 6: Auditing a past or headless session — the standalone CLI analyses any transcript outside Claude Code, with JSON output for dashboards or CI cost tracking of claude -p automation.
Example 7: Asking in the session without a shell — with the plugin enabled, Claude Code starts the bundled MCP server; ask "where did my tokens go?" or "which sessions cost the most this month?" and the skill calls session_cost, insights, or top_consumers instead of shelling out (same tools work from Claude desktop if you register scripts/mcp_server.py).
Example 8: "What were the costliest sessions (or commands) this month?" — top_consumers --by session|command --since 30d ranks the window; rows whose cost is only a priced subtotal (some usage on an unpriced model) are marked * and footnoted.
```

---

## Held in reserve — for later form pages (fields not yet seen)

The form has at least one more page; everything below is kept so answers are
ready whatever it asks.

### Proposed marketplace.json entry

```json
{
  "name": "token-usage",
  "description": "A token profiler, not another cost meter: attributes Claude Code usage to the slash command, skill, or subagent that consumed it — one row per activity, agent fan-outs rolled up, cache-aware per-model cost estimates, live per-session ledger, cross-session history, insights, top_consumers, and a bundled MCP server (session_cost, history, insights, diff, top_consumers). Answers \"what did the work cost?\" at a granularity /cost and daily aggregators can't.",
  "author": {
    "name": "Craig Fletcher"
  },
  "category": "productivity",
  "source": {
    "source": "url",
    "url": "https://github.com/Wicked-Sick-Ltd/token-usage.git",
    "sha": "d6121a76180161855efb876e1a3f9f7b1465d3bf"
  },
  "homepage": "https://github.com/Wicked-Sick-Ltd/token-usage"
}
```

(Pin SHA is HEAD of `main` as of last-verified. Community-catalog CI bumps the
pin as new commits land after listing.)

### Repository

https://github.com/Wicked-Sick-Ltd/token-usage

### Author / contact

Craig Fletcher — craig@wickedsick.com

### Category

productivity

### Short description (one line)

A token profiler, not a cost meter — attributes usage to the slash command, skill, or subagent that consumed it.

### Long description

Every existing tool answers *how much* — `/cost` gives session totals,
statuslines show a running number, aggregators roll up by day or model.
token-usage answers *what the work was*: it parses the session transcript and
produces one row per slash command or skill — "the code review cost 180k
tokens, the ad-hoc work cost 30k" — with subagent transcripts rolled up into
the command that spawned them and a `(no command)` bucket so totals always
reconcile.

Attribution is sticky (a command owns every turn until the next one) and works
in Cowork too, where skills run via the Skill tool rather than slash-command
prompts. Beyond the per-session report: `history` rolls up across all sessions
by project, day, command, or model, with a `--project` filter, `--csv` export,
and a burn-rate footer (incremental cache, near-instant warm scans); `--diff`
compares two transcripts per label for before/after prompt optimisation;
`--agents` and `--models` break a command down by agent type or model;
`insights` runs rule-based spend checks (no LLM); `top_consumers` ranks the
costliest sessions or command labels in a window. Stop and SubagentStop hooks
maintain a live per-session ledger (`~/.cache/token-usage/`) powering instant
reports, an optional statusline segment, and opt-in budget nudges that re-warn
at each budget multiple. A bundled stdio MCP server
(`scripts/mcp_server.py`, registered in `.claude-plugin/plugin.json`) exposes
`session_cost`, `history`, `insights`, `diff`, and `top_consumers` to Claude
Code (auto-started with the plugin) and Claude desktop. Cost estimates are
per-model and cache-aware (cache reads 0.1x, or the model's own cache-hit rate
where it differs, 5-minute cache writes 1.25x, 1-hour writes 2x input rate),
clearly labelled as API-price estimates for subscription users.

### Components

- 1 skill: `/token-usage:report` (user-invoked; also triggers on "where did my tokens go"; prefers MCP tools when present)
- 2 hooks: Stop and SubagentStop, both updating the session ledger (command type, 15s timeout, never blocks)
- 2 scripts, Python 3.9+ stdlib only, no dependencies:
  - `scripts/token_usage.py` — `report`/`json`/`history`/`insights`/`top_consumers`/`hook`
  - `scripts/mcp_server.py` — stdio MCP server (`session_cost`, `history`, `insights`, `diff`, `top_consumers`)
- Optional statusline example (`examples/statusline.sh`, requires jq)

### Technical notes for reviewers

- Correctness: transcript entries repeat the same API request's usage across
  multiple streamed entries; the parser deduplicates by `requestId`, merging
  duplicates by per-field maxima so partial streamed snapshots can't
  undercount (a naive sum overcounts ~2.5x). Mixed-model sessions (e.g. Opus
  main loop + Haiku subagents) are priced per model; provider-prefixed IDs
  (Bedrock `us.anthropic.…`, OpenRouter `anthropic/…`) resolve too.
- Quality: 238-test pytest suite covering dedup, segmentation, subagent
  rollup, pricing, budget nudges, history caching, diff, insights,
  top_consumers, transcript location, and the MCP server; GitHub Actions CI
  on Python 3.9 and 3.12 plus `ruff check scripts tests` (green on every
  commit).
- Security/privacy: reads only local Claude Code transcripts
  (`~/.claude/projects/`, plus the read-only Cowork sandbox mount), writes
  only to `~/.cache/token-usage/`. No network calls, no telemetry, no
  credentials, no third-party services. Hook failures are swallowed (exit 0)
  so the plugin can never block a session. Session IDs are sanitised before
  being used in ledger filenames. The MCP server speaks JSON-RPC on stdout
  only; tool failures return `isError` results rather than protocol errors.
- Tested on real sessions including a 15-subagent session (1,000+ deduped
  requests), plus headless verification via `claude -p --plugin-dir`.

### License

MIT (LICENSE file in repo)

### Documentation link

https://discovery.wickedsick.com/token-usage-claude-code-plugin-documentation
(The GitHub README https://github.com/Wicked-Sick-Ltd/token-usage#readme is the
canonical fallback.)

### Example use cases (long-form originals)

1. **"Where did my tokens go this session?"** — at the end of a long session,
   run `/token-usage:report` (or ask in natural language) and get a
   per-command table: instantly see the code review consumed 6× everything
   else combined.
2. **Deciding whether a heavy workflow is worth it** — multi-agent commands
   (deep code reviews, research fan-outs) are powerful but expensive.
   token-usage shows their true deduped, cache-aware cost, so "run on every
   PR" vs "reserve for releases" is decided with real numbers.
3. **Profiling slash commands and skills you author** — plugin developers see
   exactly what each invocation costs (cache-write amplification, subagent
   fan-out included), break it down by agent type with `--agents`, and
   compare before/after with `--diff` when optimising prompts.
4. **Which project ate the tokens this week?** — `history --by project
   --since 7d` (or by day, or by command) rolls up every session on the
   machine, with an incremental cache so warm runs are near-instant.
5. **Live cost awareness while you work** — the optional statusline segment
   renders `⏶ 214k out · $33.87 · top: /code-review`, updated every turn
   from the live ledger; set `TOKEN_USAGE_BUDGET_USD` for nudges when a
   session crosses your budget (re-warned at each multiple).
6. **Auditing a past or headless session** — the standalone CLI analyses any
   transcript outside Claude Code, with JSON output for dashboards or CI
   cost tracking of `claude -p` automation.
7. **Asking in the session without a shell** — with the plugin enabled,
   Claude Code starts the bundled MCP server; the report skill calls
   `session_cost`, `history`, `insights`, `diff`, or `top_consumers` when
   those tools are present. Claude desktop can register the same
   `scripts/mcp_server.py`.
8. **What were the costliest sessions or commands this month?** —
   `top_consumers --by session|command --since 30d` ranks the window; a row
   whose cost is only a priced subtotal is marked `*` and footnoted.
9. **Sanity-checking subagent-heavy sessions** — dozens of agents is exactly
   where naive counting fails (~2.5× overcount); the `requestId` dedup is
   validated on a 15-subagent session with 1,000+ deduped requests.
