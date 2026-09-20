# Security Policy

## Scope

token-usage runs locally on the developer machine (Claude Code, Cowork, and/or
Cursor). It makes **no network calls** and bundles **no credentials**. Data
stays on disk in the user's home directory unless they copy reports elsewhere.

**Claude Code / Cowork**

- Reads session transcripts under `~/.claude/projects/` (and Cowork mount paths
  when discovered).
- Writes per-session ledgers under `~/.cache/token-usage/` (override with
  `TOKEN_USAGE_LEDGER_DIR`).
- Executes the Stop/SubagentStop hook command in `hooks/hooks.json`.

**Cursor**

- Optional hook commands in `hooks/hooks-cursor.json` (also bundled by
  `.cursor-plugin/plugin.json`) append to
  `~/.cache/token-usage/cursor/<sanitised-prefix>_<hash>.jsonl` — fail-open,
  truncated prompts only. The directory is created owner-only where the
  filesystem supports it; records carry the raw conversation id, the workspace
  roots and a UTC timestamp, never assistant output.
- Reads Cursor Desktop `state.vscdb` **read-only** when building historical
  reports (`TOKEN_USAGE_CURSOR_DIR` overrides the User data root in tests).
- Accepts explicit Cloud Agent export JSON paths supplied by the user; does not
  call Cursor cloud APIs or store account tokens.

**Shared**

- Filesystem path handling (session and conversation IDs are sanitised before
  ledger filenames).
- The optional statusline script (`examples/statusline.sh`), which shells out to
  `jq`.
- The stdio MCP server (`scripts/mcp_server.py`), started by Claude Code or Cursor
  plugin config or a manual `mcp.json` entry — same local read/write boundaries as
  the CLI.

## Supported versions

Only the latest release on `main` is supported with fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead use
GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability), or email
**craig@wickedsick.com** with `[token-usage security]` in the subject.

You can expect an acknowledgement within a few days. Please include
reproduction steps and your environment (OS, Claude Code version, Python
version).
