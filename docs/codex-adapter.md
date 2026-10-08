# Codex runtime

The native `.codex-plugin/plugin.json` bundles the report skill, local stdio MCP
server and fail-open Stop/SubagentStop hooks. Use `--runtime codex` on every CLI
reporting command; the Codex MCP configuration sets TOKEN_USAGE_RUNTIME=codex.
Existing Claude and Cursor defaults and manifests remain independent.

Requires Python 3.9+. The MCP launcher uses `python3` on PATH (macOS and most
Linux distributions ship no bare `python`); the hook uses `python3` on macOS/Linux
and `python` on Windows. On Windows, the Python install manager provides `python3`;
with the legacy python.org installer, add a `python3` alias or use a virtual
environment. Review hooks through `/hooks` after
installing; installation never grants hook trust.

The legacy MCP config uses `cwd: "."`, resolved by Codex against the installed
plugin root, and a relative script argument. Unlike hook commands, its arguments
do not expand `${PLUGIN_ROOT}`. An install can succeed while MCP startup fails
if that placeholder is used. Listing descriptions and publisher details come
from the plugin manifest's `interface`, not the marketplace entry's description.

## Accounting

Reads rollout JSONL in `$CODEX_HOME/sessions` and `archived_sessions` (default
`~/.codex`). TOKEN_USAGE_CODEX_HOME is an isolated override. An explicit rollout
path or session ID fails closed; current-thread discovery uses CODEX_THREAD_ID.
Project selection uses the rollout's session_meta.cwd, including on Windows.

Current per-response token_usage_record events are deduplicated by response_id
using counter maxima. When those records exist, cumulative token_count events
are ignored. Older rollouts use differences between cumulative counters; idle
and rate-limit notifications do not count as additional requests. Counter resets
are disclosed as partial and the ambiguous interval is omitted. Unknown or
missing usage is never a fabricated zero-dollar bill.

Input totals include cache reads and cache writes, so the uncached bucket
subtracts both. Reasoning tokens are already included in output. Models follow
turn_context, and labels follow turns and explicit user skill/command names.
Reading a SKILL.md through an arbitrary shell tool is not proof of invocation.
Subagent rollouts with an explicit parent_thread_id (including the thread_spawn
source shape) roll into the parent report once; corpus reports exclude those
children when their parent exists. Orphans remain standalone. Parent summaries
are recomputed because child files can grow independently.

Reports measure recorded local usage. Deleted/missing child rollouts cannot be
reconstructed. Imported or future transcript formats, unknown models and usage
omitted by a host remain limitations. Transcript JSONL is not a stable public
hook interface; compatibility must be rechecked when Codex changes it.

## Prices and privacy

Standard-context, standard-tier API prices were checked against
[OpenAI's price table](https://developers.openai.com/api/docs/pricing) on
2026-10-01 for GPT-6 Astra/Sol/Luna, GPT-6.1 Sol and GPT-5.6 Sol/Terra/Luna.
GPT-5.6 Sol's current rate is promotional. These are API-equivalent estimates;
they do not model subscription allowances, priority/fast/flex/batch multipliers,
long-context uplifts, tools, taxes or enterprise terms. Supply the existing user
pricing overlay for different rates. Unrecognised models stay unpriced.

No credentials, auth files, SQLite databases, remote APIs or telemetry are used.
Codex ledgers use `codex-<session-id>.json` under TOKEN_USAGE_LEDGER_DIR or the
existing local token-usage cache. Transcript content never leaves the machine.
Tests use synthetic data and isolated homes.

## Verification

`python3 -m pytest tests/test_codex.py -q -W error` covers native and older usage
streams, deduplication, cache/reasoning accounting, model changes, child rollups,
archives, selectors, reports, MCP, hooks, CLI, resets and package resources.
Run the complete suite and Ruff before release.

Packaging and hook contracts:
[plugins](https://developers.openai.com/plugins/build/plugins),
[hooks](https://learn.chatgpt.com/docs/hooks).
