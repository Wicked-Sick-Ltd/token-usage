# Documentation audit — 2026-10-03

2026-10-09 addendum: user-doc gap pass against `scripts/token_usage.py` and
`scripts/mcp_server.py` at `868ef04` (before this change). Already present: host
install sections, a feature list, CLI examples, an MCP tool summary, a
configuration table, runtime notes, and contributor issue guidance in
CONTRIBUTING.md. Added on this pass: a README quick start, a short "What it
does" summary, [docs/reference.md](reference.md) (CLI flags, rejected
combinations, MCP arguments; local stdio only), the environment variables the
scripts read that the table omitted (`GEMINI_SESSION_ID`, `GEMINI_CLI_HOME`,
`CODEX_HOME`, `COPILOT_HOME`, `CLAUDE_PROJECT_DIR`, `APPDATA`), the pricing
overlay shape, platform defaults for the Cursor data directory, and a GitHub
Issues section for bugs and feature requests (security stays in SECURITY.md).
The Gemini and Copilot rows in the install table now match the
checkout-until-published steps already written further down the README. The
historical tables below were not re-checked line by line.

2026-10-05 addendum: the unreleased five-host work adds Gemini/Copilot CLI
integration, fixes the Codex launcher/listing, and updates README, architecture,
installation and privacy disclosures. See [Gemini/Copilot verification](gemini-copilot.md)
and the Codex/Cursor runtime notes. The tables below remain the historical 0.7.0
audit; their line numbers and three-runtime assumptions are not current.

This is a claim-by-claim check of the current-behaviour documents against the code on
`main` at `19e5534`, for version 0.6.1 going to 0.7.0. Line numbers are for
`scripts/token_usage.py` unless another file is named. The results are:

- **verified**: the code or a test confirms the claim;
- **fixed**: this audit corrected the claim;
- **unverifiable**: the claim depends on external facts or measurements that the code
  cannot confirm.

The scope is the README, CONTRIBUTING, SECURITY, AGENTS/CLAUDE/GEMINI, the docs/*.md
runtime notes, and the Notion-hosted guide (now ported into `docs/`). The dated records in
`docs/superpowers/` are historical and were not rewritten. The dashboard design record
mentions `scripts/dashboard.py`, but only as a rejected option, so it needs no
status note.

## Commands and flags (checked with `--help` for each subcommand)

| Claim | Result | Evidence |
|---|---|---|
| Subcommands `report`, `json`, `history`, `insights`, `top_consumers`, `dashboard`, `live`, `export` (and the hook entry points `hook`, `codex-hook`, `cursor-hook`) | verified | `token_usage.py --help` |
| `report --agents --models --diff OLD NEW [transcript]`; `json --diff` | verified | `report --help`, `json --help` |
| `history --by project\|day\|command\|model --since --project --json --csv` | verified | `history --help` |
| `insights [transcript] --since --project --json` | verified | `insights --help` |
| `top_consumers --by session\|command --since --project --limit --json` | verified | `top_consumers --help` |
| `dashboard --since --project --output` (default `token-usage-dashboard.html`) | verified | :4537 |
| `live --interval` (default 2) `--iterations --agents --models` | verified | :4541 |
| `export --scope session\|history --by … --since --project --output` | verified | `export --help` |
| README: `--runtime` accepts `claude`, `cursor` or `auto` | **fixed**: the CLI also accepts `codex` (`RUNTIME_CHOICES` :3901), and the default comes from `TOKEN_USAGE_RUNTIME` (:4402). The dashboard, live and export synopses now list `codex` | :3901, :4402 |
| README: MCP tools take `format` | verified; **added** that every tool also takes `runtime` | `scripts/mcp_server.py` :38, `_schema` |
| MCP malformed calls return JSON-RPC `-32602` | verified | `scripts/mcp_server.py` :230, :245, :252 |

## Behaviour and numbers

| Claim | Result | Evidence |
|---|---|---|
| Cache reads 0.1× input (0.025× on Fable/Mythos 5.1), 5-minute writes 1.25×, 1-hour writes 2× | verified | :51-56; `data/pricing.json` `cache_read` 0.25 |
| Bedrock `us.anthropic.…` and OpenRouter `anthropic/…` IDs resolve | verified | :175-183 |
| Sonnet 5 priced at $2/$10 | verified | `data/pricing.json` |
| "Rates as of September 2026" | unverifiable | external price lists |
| Dedup by `requestId` keeps per-field maxima | verified | `tests/test_parsing.py::test_dedup_keeps_per_field_maxima` |
| A naive sum overcounts about 2.5×; a full parse takes about 0.3 s for a 74 MB transcript | unverifiable | empirical measurements, kept as stated |
| Diff renders `—` when a side is unpriced | verified | `tests/test_diff.py` |
| Insights thresholds (3×/2× outlier, 5 prior sessions, 20 pp cache drop, 50 % ad hoc, 70 % agents, 75 % budget pace, +50 %/±25 % trend, 30 % mover) | verified | :2036-2045 |
| Burn-rate footer shows $/day and projected $/week for relative `--since` windows | verified | `burn_rate_line` :1231 |
| `TOKEN_USAGE_BUDGET_USD` must be a positive number; anything else is ignored with a warning; one nudge per multiple | verified | `budget_from_env` :4203; `_run_hook` :4366-4396 |
| Live ledger `~/.cache/token-usage/<session-id>.json`, overridable with `TOKEN_USAGE_LEDGER_DIR` | verified | `ledger_dir` :914 |
| History index `~/.cache/token-usage/index/`, keyed by path and revalidated by (mtime, size) | verified; it is also revalidated by a pricing fingerprint (documented in `docs/architecture.md`) | `cached_summary` :977 |
| `statusline.sh` needs `jq`; `statusline.ps1` needs PowerShell 7+ | verified | `examples/statusline.sh` :8; `tests/test_statusline_windows.py` |
| MCP `TOKEN_USAGE_PROJECT_DIR`, then `CLAUDE_PROJECT_DIR` fallback; `${…}` placeholders count as unset | verified | `scripts/mcp_server.py` :302-317 |
| `CLAUDE_PROJECT_DIR` reaches stdio MCP servers from Claude Code 2.1.139 | unverifiable | external Claude Code behaviour |
| Python 3.9+, standard library only | verified | imports :31-42; CI matrix 3.9/3.12 |

## Installation and configuration

| Claim | Result | Evidence |
|---|---|---|
| README Claude Code install: "install from a marketplace once published" | **fixed**: it already installs from the public Wicked Sick marketplace (`Wicked-Sick-Ltd/ai-marketplace`, catalogue name `wickedsick`). The Anthropic directory route is described as future | ai-marketplace `.claude-plugin/marketplace.json` |
| README had no Codex install | **fixed**: added `codex plugin marketplace add … --sparse .agents/plugins` and `codex plugin add token-usage@wickedsick` | ai-marketplace README and `.agents/plugins/marketplace.json` |
| README had no environment-variable reference | **fixed**: added a Configuration table generated from every `os.environ` read | :61, :892, :920, :2561, :2650, :3617, :3673, :4208, :4402; `scripts/mcp_server.py` :193, :313, :331 |
| README headline: "for Claude Code and Cursor" | **fixed**: Codex is a shipped runtime | `.codex-plugin/plugin.json`, `CodexAdapter` :3636 |
| README documentation link to the Notion-hosted guide, "kept in step with each release" | **fixed**: the guide still describes 0.5.0 (last edited 7 Sep 2026). Its content is ported into `docs/`, and the README links there | Notion page, see below |

## Other documents

| Claim | Result | Evidence |
|---|---|---|
| CONTRIBUTING: the plugin "reads `~/.claude/projects/` and writes `~/.cache/token-usage/` — nothing else" | **fixed**: it also reads the Cowork mount, Cursor and Codex data and the pricing files, and `dashboard`/`export` write the file named with `--output` | see README Configuration |
| CONTRIBUTING: "type hints" in the house style | **fixed**: the scripts use essentially no annotations (1 annotated function in `scripts/`) | `rg` over `scripts/` |
| CONTRIBUTING: version lives in `.claude-plugin/plugin.json` | **fixed**: three manifests carry `version` and must agree | `.codex-plugin/plugin.json` was already 0.7.0 while the others said 0.6.1 |
| docs/cursor-adapter.md: Codex is a "future adapter" | **fixed**: `CodexAdapter` shipped (#14) | :3636 |
| SECURITY.md scope (Claude, Cursor, Codex reads and writes; no network) | verified | as above |

## Ported from Notion

- "token-usage — Claude Code plugin documentation" (Technical Reference / Discovery,
  published at discovery.wickedsick.com). The problem statement and use cases are now in
  `docs/use-cases.md`. Three limitations not already in the README (segment granularity,
  promotional prices not modelled, no hooks in Cowork) were added to the README. Not
  ported:
  - feature, installation, usage, how-it-works, privacy and cost text that duplicates the
    README;
  - the stale "93-test suite" and "Current version: 0.5.0" lines;
  - the claim that dedup was "validated on a 15-subagent session with 1,000+ deduped
    requests", which cannot be checked from the code;
  - a link to an internal Notion page.
- The "Wicked-Sick-Ltd/token-usage" engineering-inventory record: its Structure and
  Reusable-bits notes became `docs/architecture.md`. Its claim that the ledger and index
  paths are "hardcoded, not configurable" is wrong (`TOKEN_USAGE_LEDGER_DIR`, :920) and
  was not ported. Internal fields (task tracker link, owners, risk tier, next actions)
  were not ported.
