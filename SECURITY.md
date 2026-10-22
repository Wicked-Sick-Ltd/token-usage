# Security Policy

## Scope

token-usage is a local AI agent plugin. It reads usage the assistant has already
recorded on the developer machine and writes derived ledgers under a cache
directory. It makes **no network calls**, bundles **no credentials**, and does
not call Claude, Cursor, Codex, Gemini, Copilot or any other vendor API.
Reports returned by MCP become part of the host conversation and follow that
host's data policy. The plugin has no remote reporting service.

Supported runtimes are Claude Code and Cowork, OpenAI Codex, Cursor (including
an explicit Cloud Agent export file), Google Antigravity and legacy Gemini CLI,
and GitHub Copilot CLI. Grok Build and VS Code agent plugins can load a package
and call the MCP tools. They do not add a runtime of their own. Any MCP client
can start the stdio server.

**Claude Code / Cowork**

- Reads session transcripts under `~/.claude/projects/` (override
  `TOKEN_USAGE_PROJECTS_DIR`) and Cowork's read-only sandbox mount when that
  is what discovery finds.
- `Stop` and `SubagentStop` (`hooks/hooks.json`) run
  `scripts/token_usage.py hook`. They always exit 0. They write
  `~/.cache/token-usage/<session-id>.json` (override `TOKEN_USAGE_LEDGER_DIR`),
  which includes token totals and the first 120 characters of each segment
  prompt. Claude ledgers may repoint `latest.json`.

**Cursor**

- Optional hook commands in `hooks/hooks-cursor.json` (also bundled by
  `.cursor-plugin/plugin.json`) append to
  `~/.cache/token-usage/cursor/<sanitised-prefix>_<hash>.jsonl`. They fail
  open, print `{}`, and store truncated prompts only (120 characters), plus
  the raw conversation id, workspace roots, a UTC timestamp and the hook's raw
  token fields. They never store assistant output. The directory is created
  owner-only where the filesystem supports it.
- Reads Cursor Desktop `state.vscdb` **read-only** (`TOKEN_USAGE_CURSOR_DIR`
  overrides the User data root).
- Accepts an explicit Cloud Agent export JSON path the user supplies. It does
  not call Cursor cloud APIs, does not store account tokens, and does not
  follow a developer's local `~/.cursor` hooks or MCP config onto a Cloud
  Agent VM. This repository does not ship `.cursor/hooks.json`.

**Codex**

- Reads local rollout JSONL from `CODEX_HOME` `sessions` and
  `archived_sessions` (override `TOKEN_USAGE_CODEX_HOME`). It does not read
  authentication files.
- `Stop` and `SubagentStop` write `codex-<session-id>.json` in the same cache.
  Those ledgers include the same 120-character prompt excerpts and do not
  repoint `latest.json`.
- Native hooks require user trust. The local MCP server uses the same
  filesystem boundary.

**Google Antigravity / Gemini CLI**

- Reads Antigravity `transcript.jsonl` under
  `~/.gemini/antigravity*/brain/<id>/.system_generated/logs/` and legacy
  Gemini CLI recordings under `~/.gemini/tmp/*/chats/` (override
  `TOKEN_USAGE_GEMINI_HOME`, or `$GEMINI_CLI_HOME/.gemini` when that override
  is unset).
- Antigravity's root `hooks.json` registers `Stop`. The `hook` command treats
  a camelCase payload (`conversationId`, `transcriptPath`, and no
  `transcript_path`) as the `gemini` runtime, writes
  `gemini-<session-id>.json` (same 120-character prompt excerpts, no
  `latest.json` repoint), and prints `{}` unless a budget nudge is emitted.
  Legacy Gemini CLI does not register a hook.

**GitHub Copilot CLI**

- Reads native session events and writes a separate minimal usage ledger under
  `$COPILOT_HOME/token-usage/` (default `~/.copilot/token-usage/`; reader
  override `TOKEN_USAGE_COPILOT_HOME`).
- The JavaScript extension uses the SDK bundled by the host. Its ledger
  contains counters, timestamps, identifiers, command/skill labels and the
  working directory. It does not contain prompt text, tool output or
  credentials. Capture fails open. There is no Copilot hook.

**Shared**

- Session and conversation ids are sanitised before they are used in ledger
  filenames.
- The optional statusline script (`examples/statusline.sh`) shells out to
  `jq`. `examples/statusline.ps1` does not.
- The stdio MCP server (`scripts/mcp_server.py`) is started by a supported
  host or a manual MCP entry. It has no network listener. It exits quietly on
  stdin EOF or a broken pipe. Tool handlers read the locations above and may
  refresh the summary index. They do not modify transcripts.

Deleting `~/.cache/token-usage/` removes summary caches and hook ledgers.
Deleting `$COPILOT_HOME/token-usage/` removes Copilot capture files. The
plugin never prunes on its own.

## Supported versions

Security fixes are provided for the latest release: **0.9.0** (tag `v0.9.0`,
2026-10-18). Older releases are not supported.

`main` may contain the unreleased changes listed in
[CHANGELOG.md](CHANGELOG.md) on top of 0.9.0, including the Antigravity
`Stop` hook. Those commits are the development line, not a separate supported
release. Report vulnerabilities against 0.9.0 or against current `main`, and
say which one you ran.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
private vulnerability reporting on this repository (Security → Report a
vulnerability), or email **hello@wickedsick.com** with
`[token-usage security]` in the subject.

You can expect an acknowledgement within a few days. Please include
reproduction steps and your environment: OS, Python version, plugin version,
and the host (Claude Code, Cowork, Codex, Cursor, Antigravity, Gemini CLI,
Copilot CLI, Grok Build, VS Code, or another MCP client). Do not include
credentials, raw transcripts, or unredacted prompts. A redacted usage block
or ledger snippet is enough.
