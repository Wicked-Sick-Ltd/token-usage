# Cursor Runtime Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Cursor conversation attribution and optional exact hook usage to the existing Claude-first CLI and MCP server through a shared runtime adapter.

**Architecture:** Keep the existing normalized segment and aggregate shapes. Add a small adapter registry in `scripts/token_usage.py`: Claude delegates to the proven functions, while Cursor discovers hook ledgers, Desktop SQLite composer data, and explicit Cloud exports. CLI and MCP select one adapter with `runtime`; no call mixes runtime corpora.

**Tech Stack:** Python 3.9+ standard library (`sqlite3`, `json`, `pathlib`, `urllib.parse`), pytest, and the existing hand-written stdio MCP server.

## Global Constraints

- Preserve Claude Code behavior and compatibility helpers.
- Python 3.9+ stdlib only; no dependency or install step.
- Never infer token counts from text length, context occupancy, credits, or cost.
- Open Cursor databases read-only and never mutate Cursor state.
- Hook failures are fail-open and never expose full prompts or assistant text.
- Keep all work public-friendly and MIT licensed.

---

### Task 1: Runtime adapter seam with Claude compatibility

**Files:**
- Modify: `scripts/token_usage.py`
- Test: `tests/test_runtimes.py`

**Interfaces:**
- Produces `RuntimeAdapter`, `ClaudeAdapter`, `get_runtime_adapter(name)`.
- `RuntimeAdapter` methods: `locate(arg=None, session_id=None, project_dir=None)`,
  `iter_sessions(project_dir=None)`, `parse(source)`, `project(source)`,
  `describe(source)`.
- Existing `parse_session`, `locate_transcript`, and corpus helpers retain
  their signatures and route through Claude behavior by default.

- [ ] Write `tests/test_runtimes.py` asserting `get_runtime_adapter("claude")`
  locates and parses the existing fixture, rejects an unknown runtime, and
  returns the same aggregate as direct `parse_session`.
- [ ] Run `python3 -m pytest tests/test_runtimes.py -v` and verify failures are
  due to the missing adapter API.
- [ ] Add the minimal interface and Claude adapter wrappers without moving or
  rewriting the proven parser.
- [ ] Re-run the focused test and then
  `python3 -m pytest tests/test_parsing.py tests/test_locate.py -v -W error`.
- [ ] Commit with subject `refactor: add runtime adapter seam`.

### Task 2: Cursor artifact discovery and parser

**Files:**
- Modify: `scripts/token_usage.py`
- Create: `tests/fixtures/cursor/composer.json`
- Create: `tests/fixtures/cursor/bubbles.json`
- Create: `tests/fixtures/cursor/cloud-export.json`
- Test: `tests/test_cursor_adapter.py`

**Interfaces:**
- Produces `CursorSession` source records and `CursorAdapter`.
- Cursor roots are overridden by `TOKEN_USAGE_CURSOR_DIR` in tests; platform
  defaults resolve macOS, Linux, and Windows user data.
- `CursorAdapter.iter_sessions(project_dir)` reads hook ledgers first, then
  project-matched composer IDs from workspace storage, then recent global
  composer rows when no project was supplied.
- `CursorAdapter.parse(source)` returns canonical segments and a measurement
  value: `exact`, `partial`, or `activity_only`.

- [ ] Add synthetic SQLite setup helpers in `tests/test_cursor_adapter.py`.
  Populate `cursorDiskKV` with one `composerData:<id>` row and ordered
  `bubbleId:<composer>:<bubble>` rows from the JSON fixtures.
- [ ] Test project-aware discovery through `workspaceStorage/*/workspace.json`.
- [ ] Test title/model/activity extraction and meaningful `tokenCount`
  `inputTokens`/`outputTokens`.
- [ ] Test zero/missing token counts remain zero, still count assistant turns,
  and mark the report `activity_only`.
- [ ] Test an explicit Cloud export with no usage produces activity-only
  segments without guessed tokens.
- [ ] Run the focused tests and confirm missing Cursor APIs are the failures.
- [ ] Implement read-only SQLite access, current/legacy bubble lookup, source
  ordering, conservative token normalization, and warnings.
- [ ] Re-run focused tests and the full Claude parsing/location suites.
- [ ] Commit with subject `feat: parse Cursor conversation artifacts`.

### Task 3: Cursor hook ledger

**Files:**
- Modify: `scripts/token_usage.py`
- Create: `hooks/hooks-cursor.json`
- Test: `tests/test_cursor_hook.py`

**Interfaces:**
- CLI entry point: `cursor-hook`, reading one Cursor hook JSON object from
  stdin and always returning zero.
- Ledger directory:
  `<TOKEN_USAGE_LEDGER_DIR>/cursor/<conversation_id>.jsonl`.
- `beforeSubmitPrompt` records truncated prompt/activity metadata.
- `stop` records optional input/output/cache fields and model.
- Events deduplicate by `generation_id`; if both completion hooks are ever
  received, only one cumulative usage snapshot counts.

- [ ] Write tests for prompt+stop joining, duplicate completion events,
  missing token fields, unsafe IDs, malformed input, and unwritable ledgers.
- [ ] Run the tests and verify the missing command/handler failures.
- [ ] Implement the append-only, atomic-per-line hook handler and ledger parser.
- [ ] Add hook registration for `beforeSubmitPrompt`, `stop`,
  `subagentStart`, and `subagentStop`; unsupported token fields remain
  optional.
- [ ] Re-run focused tests with warnings as errors.
- [ ] Commit with subject `feat: capture Cursor hook usage locally`.

### Task 4: CLI runtime routing and corpus queries

**Files:**
- Modify: `scripts/token_usage.py`
- Modify: `tests/test_history.py`
- Modify: `tests/test_insights.py`
- Test: `tests/test_cursor_cli.py`

**Interfaces:**
- `report`, `json`, `history`, `insights`, and `top_consumers` accept
  `--runtime claude|cursor|auto`; default is `claude`.
- New runtime-neutral helpers summarize one adapter source and scan an
  adapter corpus.
- JSON results add `runtime`, `measurement`, and `warnings`.

- [ ] Write subprocess tests for `report --runtime cursor` and
  `json --runtime cursor`, using an isolated synthetic Cursor root.
- [ ] Add history/top-consumer tests over two Cursor composers and an insights
  test proving activity-only data does not manufacture cost findings.
- [ ] Run focused tests and verify argparse/runtime failures.
- [ ] Route session and corpus commands through the selected adapter while
  preserving existing Claude defaults and cache behavior.
- [ ] Re-run all CLI, history, top-consumer, and insights tests.
- [ ] Commit with subject `feat: route reports through runtime adapters`.

### Task 5: MCP runtime routing

**Files:**
- Modify: `scripts/mcp_server.py`
- Modify: `tests/test_mcp.py`

**Interfaces:**
- All five MCP tools accept `runtime: claude|cursor|auto`.
- `TOKEN_USAGE_PROJECT_DIR`, `CLAUDE_PROJECT_DIR`, and Cursor workspace roots
  remain project hints, not implicit cross-runtime selectors.
- Session results disclose runtime, resolution source, measurement quality,
  and warnings.

- [ ] Extend schema tests and add Cursor calls for `session_cost`, `history`,
  and `insights`.
- [ ] Run the focused MCP tests and confirm schema/handler failures.
- [ ] Add runtime validation and pass the adapter through handlers.
- [ ] Run `python3 -m pytest tests/test_mcp.py -v -W error`.
- [ ] Commit with subject `feat: expose Cursor runtime through MCP`.

### Task 6: Cursor plugin and public documentation

**Files:**
- Create: `.cursor-plugin/plugin.json`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `docs/cursor-adapter.md`
- Modify: `SECURITY.md`
- Test: `tests/test_cursor_plugin.py`

**Interfaces:**
- Cursor Plugin manifest bundles `scripts/mcp_server.py`,
  `hooks/hooks-cursor.json`, and `skills/report/`.
- Manual `~/.cursor/mcp.json` installation remains documented.

- [ ] Write a manifest test checking MIT metadata, repository URL, MCP command,
  skills path, and hooks path.
- [ ] Run it and verify the manifest-missing failure.
- [ ] Add the Cursor Plugin manifest with relative, public-repository paths.
- [ ] Write `docs/cursor-adapter.md` with investigated paths, source authority,
  attribution mapping, limitations, privacy, and Gemini/Codex adapter contract.
- [ ] Update README installation/usage, CHANGELOG Unreleased, and security
  scope without implying Cursor subscription billing.
- [ ] Run the manifest test and `git diff --check`.
- [ ] Commit with subject `docs: add Cursor installation and adapter guide`.

### Task 7: Full verification and delivery

**Files:**
- Create artifact: `/opt/cursor/artifacts/cursor-adapter-validation.log`

- [ ] Confirm environment setup status before tests.
- [ ] Run `python3 -m pytest tests/ -v -W error` and save output to the artifact.
- [ ] Run `ruff check scripts tests` and append output.
- [ ] Run a CLI Cursor fixture report and an MCP initialize/tool-call smoke,
  appending redacted output.
- [ ] Run `git diff --check` and inspect `git status`.
- [ ] Commit any verification-driven fixes separately, push the branch, and
  create a draft PR titled `feat: Cursor runtime adapter (multi-AI roadmap)`.

## Self-review

The plan covers every design requirement: an adapter boundary, historical
Cursor artifacts, prospective exact hook usage, CLI and MCP routing, plugin
distribution, public docs, limitations, synthetic tests, and unchanged Claude
defaults. No task requires network access or a third-party dependency. Every
production-code task starts with a failing test and has a focused verification
command.
