# token-usage 0.7 — dashboard, live mode, and structured export

**Date:** 2026-09-20
**Status:** approved by the roadmap pull-forward request
**Baseline:** Cursor runtime adapter branch (`b18f730`)

## Goal

Pull forward the useful, dependency-free parts of the existing 0.7 roadmap
while the runtime adapter boundary is already open:

1. generate a self-contained static HTML dashboard from indexed history;
2. provide a terminal live view that refreshes the current session;
3. export stable JSONL aggregate records for external spend tooling;
4. add Windows statusline parity where it does not complicate runtime code.

Claude Code remains the default runtime. Every new surface accepts the same
Cursor runtime semantics and measurement disclosures introduced by the adapter
work.

## Constraints

- Python 3.9+ standard library only.
- No CDN, remote assets, network calls, telemetry, or browser dependency.
- Dashboard HTML is one portable file with inline CSS and inline SVG.
- Export never invents missing usage. Cursor `partial` and `activity_only`
  measurements remain explicit.
- Existing report/history/insights/MCP behavior and JSON shapes do not change.
- Dashboard, live, and export are ordinary CLI paths; hooks do not invoke them.

## Considered approaches

### Dashboard

1. **Standalone `scripts/dashboard.py`.** Keeps HTML code separate, but creates
   a second CLI and duplicates argument/runtime handling.
2. **`token_usage.py dashboard` with focused renderer helpers.** Reuses runtime,
   history, pricing, warnings, and cache behavior without installation changes.
3. **Dynamic local web server.** Enables refresh and interaction, but introduces
   lifecycle, port, browser, and security concerns.

Decision: option 2. Static HTML is the roadmap requirement and is easier to
archive, email, or open offline. Dynamic serving remains unnecessary.

### Live mode

1. File watching by platform-specific APIs.
2. Portable polling with a configurable interval.

Decision: portable polling. It reparses only the selected session, clears the
terminal when interactive, and exits cleanly on Ctrl-C. A finite `--iterations`
option makes automation and testing deterministic.

### Export

1. Full OTLP protobuf/HTTP exporter.
2. OTLP-shaped JSON.
3. Stable token-usage JSONL records with OTel-style metric names.

Decision: option 3. It is streamable, dependency-free, easy for Spend Radar and
similar tools to ingest, and does not claim wire compatibility with OTLP.
HTTP/SSE and OTLP transport require authentication, retry/backpressure, and
remote-server lifecycle decisions, so they remain 0.7.1+.

## Dashboard command

```bash
python3 scripts/token_usage.py dashboard \
  [--runtime claude|cursor|auto] [--since 30d|DATE] \
  [--project SUBSTRING] [--output token-usage-dashboard.html]
```

`dashboard` obtains runtime-isolated history grouped by day, project, command,
and model using the existing index-backed queries. It writes:

- summary cards: estimated cost, output tokens, input tokens, cache reads,
  sessions/calls, and measurement quality;
- an inline SVG daily-cost chart;
- top projects, activities, and models as accessible HTML tables;
- source/runtime/window/measurement/warning notes;
- a generation timestamp.

The document contains no `<script>`, external stylesheet, remote image, CDN,
iframe, or fetch. Labels and warnings are HTML-escaped. The default output is
`token-usage-dashboard.html`; `--output -` writes HTML to stdout. Normal
progress/warnings stay on stderr.

If no history exists, the page still renders with an honest empty-state and
missing-root disclosure. Activity-only Cursor data can appear as calls/turns,
but its cost/token cards and chart disclose that zeros are unmeasured.

## Live command

```bash
python3 scripts/token_usage.py live [TRANSCRIPT] \
  [--runtime claude|cursor|auto] [--interval 2] \
  [--agents] [--models] [--iterations N]
```

Each iteration resolves the same explicit source, or re-runs normal latest
session discovery when no source was supplied, then renders the existing
report. Interactive stdout is cleared with ANSI `\x1b[2J\x1b[H`; redirected
stdout uses a timestamped separator so logs remain readable. `--interval` must
be positive and `--iterations` must be positive when supplied. Ctrl-C exits
zero. No file watcher, daemon, or background process is introduced.

The implementation exposes a pure-ish `run_live` loop with injected output,
sleep, clock, and TTY checks so tests never wait or clear a real terminal.

## Structured export command

```bash
# Current/latest session
python3 scripts/token_usage.py export [TRANSCRIPT] \
  --scope session [--runtime cursor] [--output usage.jsonl]

# Indexed history
python3 scripts/token_usage.py export --scope history \
  [--by project|day|command|model] [--since 30d] \
  [--project SUBSTRING] [--runtime cursor] [--output usage.jsonl]
```

Default scope is `history`, default grouping is `project`, and default output is
stdout (`-`). Each line is one RFC-8259 JSON object:

```json
{
  "schema": "token-usage.aggregate.v1",
  "runtime": "claude",
  "scope": "history",
  "group_by": "project",
  "key": "-workspace-repo",
  "timestamp": "2026-09-20T07:00:00Z",
  "dimensions": {"project": "-workspace-repo"},
  "metrics": {
    "gen_ai.usage.input_tokens": 1200,
    "gen_ai.usage.output_tokens": 450,
    "gen_ai.usage.cache_read_tokens": 9000,
    "gen_ai.usage.cache_write_tokens": 300,
    "gen_ai.usage.requests": 3,
    "gen_ai.estimated_cost.usd": 0.04
  },
  "measurement": "exact",
  "warnings": []
}
```

Session scope emits one `total` record and one `activity` record per aggregated
label. History scope emits one record per requested grouping row and a final
`total` record. `cost_usd` remains `null` when unavailable. Output files use an
atomic temp-file replacement; stdout streams directly. Export is local-only and
contains labels/project identifiers, so docs advise redaction before sharing.

This is “OTel-ish” naming, not OTLP. A future OTLP exporter can map this stable
schema to SDK/protobuf types without changing parsers.

## Windows statusline

Add `examples/statusline.ps1`, a dependency-free PowerShell sibling of the
bash+jq script. It reads
`$env:TOKEN_USAGE_LEDGER_DIR/latest.json` or
`~/.cache/token-usage/latest.json`, prints output tokens, estimated cost, and
top activity, and exits silently when the ledger is absent/malformed.

## MCP and transport

The existing stdio MCP server remains unchanged. Dashboard and export are file
operations better initiated by the local CLI; exposing arbitrary output paths
through MCP would widen the write surface.

HTTP/SSE is deferred to 0.7.1+ because a safe implementation requires explicit
binding defaults, authentication, origin policy, concurrency, shutdown, and
stream resumption semantics. It must not be bolted onto the stdio loop.

## Testing

### Dashboard

- deterministic HTML generation from synthetic history rows;
- inline SVG and CSS present;
- no `http://`, `https://`, `<script>`, `<iframe>`, or external assets;
- HTML escaping for project/activity/model names and warnings;
- empty history and activity-only measurement notes;
- atomic file output and stdout mode;
- Claude and Cursor runtime routing.

### Live

- finite two-iteration run with injected sleep;
- TTY clear sequence vs redirected separators;
- explicit source remains fail-closed;
- latest-session rediscovery when omitted;
- positive interval/iteration validation;
- Ctrl-C exits cleanly at the CLI boundary.

### Export

- session total/activity records reconcile;
- history row/total records reconcile;
- stable schema and OTel-style metric names;
- null cost and activity-only measurement are preserved;
- RFC-8259 output (no NaN/Infinity);
- atomic file and stdout output;
- runtime/project/since filters remain isolated.

### Windows statusline

- static script assertions for ledger override, fallback path, JSON parsing,
  silent failure, and expected output fields;
- run under `pwsh` when available, otherwise document that CI validates syntax
  and behavior structurally only.

The full existing pytest and ruff gates remain mandatory.

## Roadmap after this slice

- **0.7.1+:** optional authenticated HTTP/SSE MCP transport and/or true OTLP
  mapping after transport semantics are designed.
- **Deferred by YAGNI:** user-configurable insight thresholds.
- **Explicitly out of scope:** fleet/multi-machine aggregation and
  LLM-generated insights.
- **Future runtimes:** Gemini, Codex, and others implement the adapter contract
  documented in `docs/cursor-adapter.md`; dashboard/export require no
  runtime-specific changes once an adapter emits canonical summaries.
