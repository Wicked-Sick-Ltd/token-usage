# token-usage documentation

This folder and the repository [README](../README.md) are the documentation for
token-usage. They replace the earlier Notion-hosted guide, which described version 0.5.0
and is no longer updated.

## Start here

- [README](../README.md): quick start, what token-usage does, installation for Claude Code, Codex, Cursor, Gemini CLI, Copilot CLI, Grok Build, VS Code agent plugins and any MCP client, configuration, insights, how to open a GitHub issue, cost disclaimer and limitations.
- [CLI and MCP reference](reference.md): every command flag, the combinations the script rejects, and every stdio MCP tool argument.
- [Use cases](use-cases.md): the problem token-usage solves and the questions it answers.
- [Architecture](architecture.md): how the code is laid out, how a report is built, and
  the extension points.

## Runtime notes

- [Codex runtime](codex-adapter.md): setup, accounting, prices and privacy for Codex.
- [Cursor runtime](cursor-adapter.md): attribution sources, measurement levels,
  limitations and privacy for Cursor.

- [Gemini and Copilot CLI](gemini-copilot.md): installation, usage capture, accounting,
  privacy, limitations and native verification evidence.

## Project records

- [CHANGELOG](../CHANGELOG.md): every release.
- [Marketplaces](marketplaces.md): where token-usage is listed, each directory's
  requirements and the resubmission checklist.
- [Contributing](../CONTRIBUTING.md) and [Security policy](../SECURITY.md).
- [Documentation audit](DOCS-AUDIT.md): the claim-by-claim check of these documents
  against the code.
- `superpowers/specs/` and `superpowers/plans/`: dated design and implementation records.
  They are historical and describe intent at the time they were written, not current
  behaviour.
