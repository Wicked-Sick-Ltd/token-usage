# token-usage 0.7 Dashboard, Live, and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-contained dashboard, portable live terminal view, stable
JSONL export, and Windows statusline without changing existing Claude or Cursor
reporting behavior.

**Architecture:** New CLI subcommands consume the existing runtime-neutral
session aggregates and history rows. Dashboard rendering, live polling, and
export record construction are focused helpers in `scripts/token_usage.py`;
they do not introduce a server, dependency, or new persistence format.

**Tech Stack:** Python 3.9+ standard library, inline HTML/CSS/SVG, JSONL, pytest,
and dependency-free PowerShell.

## Global Constraints

- Preserve all existing Claude and Cursor behavior and JSON shapes.
- Python 3.9+ stdlib only; no network, telemetry, CDN, or browser dependency.
- Never infer missing usage or hide `partial`/`activity_only` measurement.
- Dashboard output is one portable HTML file.
- Export is local-only RFC-8259 JSONL with a stable v1 schema.
- MCP remains stdio; thresholds, fleet aggregation, and LLM insights stay
  deferred.

---

### Task 1: Static dashboard generator

**Files:**
- Modify: `scripts/token_usage.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- `dashboard_data(runtime, since, project, project_dir=None) -> dict`
- `render_dashboard(data, generated_at=None) -> str`
- `write_text_output(text, output_path) -> None`
- CLI: `dashboard --runtime --since --project --output`

- [ ] Write failing tests for deterministic cards/tables, inline SVG, escaping,
  forbidden external assets, empty/activity-only data, stdout/file output, and
  runtime routing.
- [ ] Run focused tests and confirm missing APIs/subcommand.
- [ ] Implement history collection, escaped HTML, inline SVG chart, and atomic
  output with no external references.
- [ ] Run focused tests and existing history/runtime regressions.
- [ ] Commit `feat: add self-contained token usage dashboard`.

### Task 2: Live terminal mode

**Files:**
- Modify: `scripts/token_usage.py`
- Test: `tests/test_live.py`

**Interfaces:**
- `run_live(transcript=None, runtime="claude", interval=2.0,
  iterations=None, show_agents=False, show_models=False, ...)`
- CLI: `live [TRANSCRIPT] --runtime --interval --iterations --agents --models`

- [ ] Write failing tests for finite polling, injected sleeping, TTY clearing,
  redirected separators, latest rediscovery, explicit fail-closed source,
  validation, and KeyboardInterrupt handling.
- [ ] Run focused tests and confirm missing APIs/subcommand.
- [ ] Implement the portable polling loop and CLI wiring.
- [ ] Run focused tests plus report/locate/Cursor CLI regressions.
- [ ] Commit `feat: add live terminal token report`.

### Task 3: Structured JSONL export

**Files:**
- Modify: `scripts/token_usage.py`
- Test: `tests/test_export.py`

**Interfaces:**
- `session_export_records(data, generated_at=None) -> list[dict]`
- `history_export_records(data, generated_at=None) -> list[dict]`
- `render_jsonl(records) -> str`
- CLI: `export [TRANSCRIPT] --scope session|history --by ... --runtime ...
  --since ... --project ... --output ...`

- [ ] Write failing tests for session reconciliation, history totals, schema,
  metric names, null cost, measurement/warnings, escaping/RFC-8259 output,
  stdout/file output, and argument compatibility.
- [ ] Run focused tests and confirm missing APIs/subcommand.
- [ ] Implement stable `token-usage.aggregate.v1` records and atomic output.
- [ ] Run focused tests plus history/report/runtime regressions.
- [ ] Commit `feat: export token usage aggregates as JSONL`.

### Task 4: Windows statusline and roadmap docs

**Files:**
- Create: `examples/statusline.ps1`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/cursor-adapter.md`
- Test: `tests/test_statusline_windows.py`

- [ ] Write a failing structural test for ledger override/fallback, JSON
  parsing, silent error handling, and output fields.
- [ ] Add the dependency-free PowerShell statusline.
- [ ] Document dashboard/live/export usage, privacy, Cursor measurement
  behavior, and Windows statusline setup.
- [ ] Document HTTP/SSE/OTLP transport as 0.7.1+, configurable thresholds as
  YAGNI, and fleet/LLM insights as out of scope.
- [ ] Update CHANGELOG Unreleased without duplicate headings.
- [ ] Run focused tests, command-help smoke, link/path review, and
  `git diff --check`.
- [ ] Commit `docs: document dashboard export and multi-AI roadmap`.

### Task 5: Full review and delivery

**Files:**
- Artifacts under `/opt/cursor/artifacts/`

- [ ] Push committed implementation to the existing PR branch before final
  verification and update the PR description.
- [ ] Run `python3 -m pytest tests/ -v -W error`.
- [ ] Run `ruff check scripts tests` and Python 3.9 AST compatibility.
- [ ] Generate a dashboard from synthetic indexed history and verify the file
  is self-contained.
- [ ] Run finite live mode and session/history export smokes.
- [ ] Save concise walkthrough logs and a dashboard screenshot if a browser is
  available; UI manual testing requires a video only if performed.
- [ ] Run whole-branch review, fix Critical/Important findings, push, and update
  the existing PR without merging.

## Self-review

The plan covers the pulled-forward 0.7 dashboard/live roadmap, a first-class
structured export, and cheap Windows parity. HTTP/SSE, configurable thresholds,
fleet aggregation, and LLM insights remain explicitly deferred. Every code task
starts with failing tests and reuses canonical runtime summaries rather than
adding runtime-specific data paths.
