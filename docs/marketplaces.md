# Marketplaces and directories

Where token-usage is listed, what each directory requires, and what is still to do.
Nothing in this repository submits anything: every submission and listing PR is a
manual step for the repository owner.

**Last verified:** 2026-10-08, for the 0.8.0 candidate.

## Status

| Directory | How it lists | Status |
|---|---|---|
| Claude plugin directory (Anthropic) | Developer portal, reviewed | Not listed. Resubmit 0.8.0; see [SUBMISSION.md](../SUBMISSION.md). |
| Wicked Sick marketplace (`Wicked-Sick-Ltd/ai-marketplace`) | Claude, Codex and Copilot catalogues, commit-pinned | Listed at the 0.8.0 candidate commit; bump the pins after the release. Cursor catalogue empty. |
| Cursor Marketplace | cursor.com/marketplace/publish, manually reviewed | Not listed. Ready to submit after the release. |
| cursor.directory | Community submission | Not listed. Optional. |
| Gemini CLI extensions gallery | Automatic crawl of public repos | Not listed. Needs the `gemini-cli-extension` topic and a release tag. |
| Grok Build (`xai-org/plugin-marketplace`) | Pull request, SHA-pinned | Not listed. Installs directly from GitHub today. |
| GitHub Copilot CLI (`awesome-copilot`) | Pull request to the built-in marketplace | Not listed. Installs from the Wicked Sick marketplace today. |
| VS Code agent plugins | Any marketplace repo in `chat.plugins.marketplaces` | Works through the Wicked Sick marketplace. |
| Codex plugin directory (OpenAI) | Reviewed | Not eligible yet (see below). Distribution stays through the Wicked Sick marketplace. |
| Official MCP Registry | `mcp-publisher` with `server.json` | Not eligible yet: needs a published package. |
| Smithery | `smithery mcp publish` of an `.mcpb` bundle | Not eligible yet: needs an MCPB bundle. |

## Claude plugin directory

- Submit at claude.ai/directory/manage, **Submit new**, **Plugin bundle**. Process:
  https://claude.com/docs/plugins/submit. Automated checks:
  https://claude.com/docs/plugins/pre-submission-checklist.
- Steps: source (repository, path, branch or tag), **Validate**, listing details (read
  from `.claude-plugin/plugin.json` and the README), data handling questions,
  compliance (contact email and four acknowledgements), review and submit (push
  webhook or scheduled check, optional auto-publish).
- The repository must be public. It is.
- Withdraw the earlier Console form submission first, or the portal can refuse the
  repository as already submitted by another organisation.
- Copy for every field is in [SUBMISSION.md](../SUBMISSION.md).

## Cursor Marketplace

- Submit at https://cursor.com/marketplace/publish. Every plugin is reviewed by hand
  and must be open source.
- Checklist: kebab-case `name`, a description, valid skill frontmatter, relative
  paths, a README, tested locally. A logo committed to the repository and referenced
  by relative path is optional; token-usage has none yet.
- Manifest: `.cursor-plugin/plugin.json` (MCP server, hooks, report skill).
- After it is published, also add the entry to the Cursor catalogue in
  `Wicked-Sick-Ltd/ai-marketplace` (`.cursor-plugin/marketplace.json`).

## Gemini CLI extensions gallery

- The gallery indexes public repositories automatically, once a day, when they have
  `gemini-extension.json` at the root, the GitHub topic `gemini-cli-extension`, and a
  release tag. Keep the manifest `version` in step with the tag.
- To do: add the topic to `Wicked-Sick-Ltd/token-usage` and publish v0.8.0.

## Grok Build

- Grok Build reads Claude plugins, `.claude-plugin/` manifests and marketplaces
  natively, so no Grok-specific manifest is needed. `grok plugin validate` and
  `grok mcp doctor` both pass on the Claude plugin.
- The official marketplace is `xai-org/plugin-marketplace`; a listing is a pull
  request adding an entry, pinned to a commit SHA, to its
  `.grok-plugin/marketplace.json`.
- Gap: Grok's own sessions are not attributed (no Grok runtime adapter yet).

## GitHub Copilot CLI and VS Code

- Copilot CLI installs `plugin@marketplace`; direct installs are deprecated. The
  Wicked Sick catalogue lives at `.github/plugin/marketplace.json` in
  `Wicked-Sick-Ltd/ai-marketplace`. A wider listing is a pull request to
  `github/awesome-copilot`.
- VS Code agent plugins (`chat.plugins.enabled`) read the same marketplace repositories
  through `chat.plugins.marketplaces`. The Visual Studio Marketplace proper needs a VS
  Code extension, which token-usage is not.

## Codex

- Distribution: `codex plugin marketplace add Wicked-Sick-Ltd/ai-marketplace --sparse
  .agents/plugins`, then `codex plugin add token-usage@wickedsick`.
- OpenAI's public plugin directory currently excludes plugins with lifecycle hooks and
  favours remote HTTPS MCP servers, so token-usage (local stdio, Stop hooks) is not a
  fit today.

## MCP Registry and Smithery

- The Official MCP Registry stores metadata only. It needs a published package (PyPI
  with an `mcp-name:` line in the README, npm, OCI, NuGet, or an MCPB bundle on a
  GitHub release with its SHA-256), a `server.json`, and a GitHub-namespaced name such as
  `io.github.wicked-sick-ltd/token-usage`.
- Smithery lists local stdio servers published as `.mcpb` bundles.
- Both need packaging work first: build an MCPB bundle (and optionally a PyPI package)
  in CI on each release. The same bundle would also serve Claude Desktop extensions.

## After each release

1. Tag and publish the GitHub release; manifests and CHANGELOG must agree.
2. Bump the commit pins in the Wicked Sick catalogues (Claude, Codex, Copilot) and the
   Gemini reference.
3. The Claude directory picks the version up through the push webhook or its
   scheduled check.
