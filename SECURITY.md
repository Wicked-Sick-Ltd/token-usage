# Security Policy

## Scope

token-usage runs locally on the developer machine for Claude Code/Cowork, Codex,
Cursor, Gemini CLI and GitHub Copilot CLI. It makes **no network calls** and bundles **no credentials**. Reports returned by MCP become part of the host conversation and follow its data
policy; the plugin itself has no remote reporting service.

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

**Codex**

- Reads local rollout JSONL from CODEX_HOME sessions and archived_sessions.
- Stores separate codex-prefixed ledgers in the existing token-usage cache.
- Never reads authentication files or sends usage/transcripts over the network.
- Native hooks require user trust; the local MCP server uses the same boundaries.

**Gemini CLI / Copilot CLI**

- Gemini reads native JSON/JSONL recordings, including child sessions, under
  `~/.gemini/tmp/`. It uses the existing report/index paths and no native hooks.
- Copilot reads native session events and writes a separate minimal usage ledger
  under `$COPILOT_HOME/token-usage/` (default `~/.copilot/token-usage/`).
- Copilot's JavaScript extension uses the SDK bundled by the host. Its ledger
  contains counters, timestamps, identifiers, command/skill labels and the working
  directory, with no prompt text, tool output or credentials. Capture fails open.
- Root overrides, retention and limitations are in [the runtime guide](docs/gemini-copilot.md).

**Shared**

- Filesystem path handling (session and conversation IDs are sanitised before
  ledger filenames).
- The optional statusline script (`examples/statusline.sh`), which shells out to
  `jq`.
- The stdio MCP server (`scripts/mcp_server.py`), started by a supported host
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
