# Plugin directory submission — token-usage

Copy-paste source for submitting token-usage to Anthropic's Claude plugin directory
through the **developer portal**: claude.ai/directory/manage → **Submit new** →
**Plugin bundle**. Anthropic's process is described at
https://claude.com/docs/plugins/submit and the automated checks at
https://claude.com/docs/plugins/pre-submission-checklist.

**Portal procedure last verified:** 2026-10-03 for 0.7.0.

**Current candidate:** 0.8.0, unreleased. Local five-host checks and limits are in
the runtime guides. Merge, release publication and portal resubmission are pending;
this document does not record an approved or completed submission.

The earlier Claude Console plugin form and the claude.ai admin-settings form are no
longer supported. An earlier submission made through them has to be withdrawn or moved
first (see [Move an earlier Console submission](#move-an-earlier-console-submission)).
Nothing in this repository submits anything; the submission is a manual step for the
repository owner.

---

## Before you start

- Submit from the claude.ai organisation that should own the listing long term. The
  first organisation to submit a repository or folder holds that listing.
- On a Team or Enterprise plan the submitter needs the Owner role (or a custom role with
  the Directory permission).
- Connect GitHub on claude.ai in that organisation, with an account that has push access
  to `Wicked-Sick-Ltd/token-usage` (admin access is needed only for the optional push
  webhook).
- Raise `version` in the manifests and add a CHANGELOG entry for every release; the
  directory treats each version separately. `tests/test_directory_readiness.py` fails if
  all five host manifests or the changelog disagree.

## Source

| Field | Value |
|---|---|
| Repository | `Wicked-Sick-Ltd/token-usage` |
| Plugin path | *(leave empty: `.claude-plugin/plugin.json` is at the repository root)* |
| Branch or tag | `main` *(or leave empty for the default branch)* |

Then select **Validate**. Fix anything marked **Blocking**, push, and select
**Re-validate**. Locally, `claude plugin validate .` accepts the package with warnings: the three directory listing URL fields are
ignored by the CLI, and the contributor `CLAUDE.md` at the root is not loaded as
plugin context (it points coding agents at `AGENTS.md`). CI runs the same validator on every pull request.

## Listing details

The portal reads these from the repository:

| Listing field | Source | Value |
|---|---|---|
| Name | `plugin.json` `name` | `token-usage` |
| Display name | `plugin.json` `displayName` | Token Usage Profiler |
| Description | `plugin.json` `description` | A token profiler, not a cost meter: attributes Claude Code and Cowork usage to the slash command, skill or subagent that consumed it. |
| Homepage and documentation | `homepage`, `documentationUrl` | https://github.com/Wicked-Sick-Ltd/token-usage#readme |
| Support | `supportUrl` | https://github.com/Wicked-Sick-Ltd/token-usage/issues |
| Privacy | `privacyPolicyUrl` | https://github.com/Wicked-Sick-Ltd/token-usage#privacy-and-data-handling |
| Repository | `repository` | https://github.com/Wicked-Sick-Ltd/token-usage |
| Licence | `license` and `LICENSE` | MIT |
| Author | `author` | Craig Fletcher, craig@wickedsick.com |
| Long description | `README.md` | the README |

Category, if asked: productivity.

### Long description (if a field asks for one)

Every existing tool answers *how much*: `/cost` gives session totals, statuslines show
a running number, aggregators roll up by day or model. token-usage answers *what the
work was*. It parses the session transcript and produces one row per slash command or
skill ("the code review cost 180k tokens, the ad-hoc work cost 30k"), with subagent
transcripts rolled up into the command that spawned them and a `(no command)` bucket so
totals always reconcile.

Attribution is sticky (a command owns every turn until the next one) and works in Cowork
too, where skills run through the Skill tool rather than slash-command prompts. Beyond
the per-session report: `history` rolls up every session by project, day, command or
model, with a `--project` filter, `--csv` export and a burn-rate footer; `--diff`
compares two transcripts per label; `--agents` and `--models` break a command down by
agent type or model; `insights` runs rule-based spend checks (no LLM); `top_consumers`
ranks the costliest sessions or command labels in a window; `dashboard` writes one
self-contained HTML file; `live` refreshes the report in the terminal; `export` writes
JSONL aggregates for other tooling. Stop and SubagentStop hooks keep a per-session
ledger current, which powers instant reports, an optional statusline and opt-in budget
nudges. A bundled stdio MCP server exposes `session_cost`, `history`, `insights`, `diff`
and `top_consumers`. Cost figures are per-model, cache-aware API-price estimates,
labelled as such for subscription users. Python 3.9+ standard library only: no
dependencies, no network calls, no telemetry.

The same repository also packages the plugin for Codex (`.codex-plugin/`) and Cursor
(`.cursor-plugin/`), Gemini CLI (`gemini-extension.json`) and GitHub Copilot CLI
(`.plugin/`); this directory listing covers the Claude Code plugin.

### Example use cases

```
Example 1: "Where did my tokens go this session?" — at the end of a long session, run /token-usage:report (or just ask) and get a per-command table: see at once that the code review consumed six times everything else combined.
Example 2: Deciding whether a heavy workflow is worth it — multi-agent commands (deep code reviews, research fan-outs) are powerful but expensive. token-usage shows their true deduplicated, cache-aware cost, so "run on every PR" or "reserve for releases" is decided with real numbers.
Example 3: Profiling slash commands and skills you author — see what each invocation costs (cache-write amplification and subagent fan-out included), break it down by agent type with --agents or by model with --models, and compare before and after with --diff when optimising prompts.
Example 4: "Which project ate the tokens this week?" — history --by project --since 7d (or by day, command or model) rolls up every session on the machine, with a burn-rate footer and CSV export; an incremental cache keeps warm runs near-instant.
Example 5: Live cost awareness while you work — the optional statusline renders "214k out · $33.87 · top: /code-review", updated every turn from the live ledger; set TOKEN_USAGE_BUDGET_USD for a nudge when a session crosses your budget (and again at each multiple).
Example 6: Auditing a past or headless session — the standalone CLI analyses any transcript outside Claude Code, with JSON output for dashboards or CI cost tracking of claude -p automation.
Example 7: Asking in the session without a shell — with the plugin enabled, Claude Code starts the bundled MCP server; ask "where did my tokens go?" or "which sessions cost the most this month?" and the skill calls session_cost, insights or top_consumers instead of shelling out.
Example 8: "What were the costliest sessions (or commands) this month?" — top_consumers --by session|command --since 30d ranks the window; rows whose cost is only a priced subtotal (some usage on an unpriced model) are marked * and footnoted.
```

## Data handling

These answers must match the README section
[Privacy and data handling](README.md#privacy-and-data-handling).

**Does the plugin read or store personal data?**
Yes, locally only. It reads the user's own local AI session transcripts, which can
contain prompts and code: Claude Code `~/.claude/projects/`, the read-only Cowork sandbox
mount, and (only when asked for those runtimes) Cursor's local `state.vscdb` opened
read-only, Codex rollouts under `~/.codex`, Gemini recordings under `~/.gemini`,
and Copilot session events under `~/.copilot`. It stores derived token counts, cost
estimates, activity labels, transcript paths and prompt excerpts of up to 120 characters
in `~/.cache/token-usage/` on the user's machine. Copilot's capture extension also
stores counters, event IDs, labels and project/session metadata under
`$COPILOT_HOME/token-usage/`, without prompts or tool output. Reports returned through
MCP enter the host conversation and follow its data policy.

**Does it send data to services other than its declared connectors?**
No. It makes no network calls and has no telemetry or third-party services. The bundled
MCP server is a local stdio process (`scripts/mcp_server.py`).

**How long does it keep data?**
Until the user deletes it. The plugin never prunes; deleting `~/.cache/token-usage/`
removes summary caches and hook ledgers. Remove `$COPILOT_HOME/token-usage/`
(default `~/.copilot/token-usage/`) separately to remove Copilot capture files.

**Is it intended for people under 18?**
No. It is a developer tool.

## Compliance

- Contact email: `craig@wickedsick.com`
- Read the Anthropic Software Directory Terms and Software Directory Policy, then tick
  the four acknowledgements.

## Review and submit

- Updates: **GitHub push webhook** (select **Set up push updates**; needs admin on the
  repository) or a scheduled check.
- Auto-publish passing versions: your choice. A reviewer publishes the first version.
- Select **Submit for review**, then follow the plugin's Versions and Review tabs in the
  portal.

## Move an earlier Console submission

token-usage was first submitted in mid-2026 through the Claude Console plugin form,
when the repository was `WizzoUK2/token-usage` (it moved to `Wicked-Sick-Ltd` in
September 2026). Console submissions get no status, scan or publishing, and can make the
portal refuse the same repository as "Already submitted by another organization". Before
submitting through the portal:

1. Withdraw it at platform.claude.com → **Plugin submissions** → **Withdraw**, or
2. if there is no Withdraw button, email directory@anthropic.com and ask for it to be
   moved to the developer portal under the organisation that will own the listing.

## What the plugin contains

- 1 skill: `/token-usage:report` (also triggers on "where did my tokens go"; prefers the
  MCP tools when present).
- 2 hooks: `Stop` and `SubagentStop`, both running
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_usage.py" hook` with a 15-second
  timeout. They always exit 0 and never block the session.
- 1 stdio MCP server, declared inline in `.claude-plugin/plugin.json`:
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py`.
- 2 Python 3.9+ standard-library scripts, readable source, no dependencies:
  `scripts/token_usage.py` and `scripts/mcp_server.py`.
- `data/pricing.json` (bundled API prices) and optional statusline examples in
  `examples/`.

## Technical notes for reviewers

- Correctness: Claude Code repeats the same API request's usage across streamed
  transcript entries; the parser deduplicates by `requestId`, merging duplicates by
  per-field maxima (a naive sum overcounts about 2.5×). Mixed-model sessions are priced
  per model; provider-prefixed model IDs (Bedrock `us.anthropic.…`, OpenRouter
  `anthropic/…`) resolve too.
- Quality: the pytest suite (`python3 -m pytest tests/ -v -W error`) and
  `ruff check scripts tests` run in GitHub Actions on Python 3.9 and 3.12, together with
  `claude plugin validate .` and the directory-readiness tests.
- Security and privacy: see the README section linked above. Session IDs are sanitised
  before they are used in ledger file names. The MCP server speaks JSON-RPC on stdio
  only; tool failures come back as `isError` results.
