---
name: report
description: Report Codex token usage, expensive turns or skills, models, session comparisons, usage history, budget insights, live usage, dashboards and aggregate exports. Use when asked where tokens went, what a session cost, or to compare agent usage.
---

Use the bundled token-usage MCP server with `runtime: "codex"`. Its tools are
`session_cost`, `history`, `insights`, `diff`, and `top_consumers`; discover their
actual names in this session. Prefer an explicit `session_id` from CODEX_THREAD_ID
or a rollout path. Say when discovery picked the latest session instead.

If MCP is unavailable, run the bundled `scripts/token_usage.py` with Python 3.9+
(`python` on Windows, `python3` on macOS/Linux). Resolve the installed plugin root
from this file; do not assume PLUGIN_ROOT is set in an interactive shell.

```text
python <plugin>/scripts/token_usage.py report --runtime codex <rollout.jsonl> --models --agents
python <plugin>/scripts/token_usage.py history --runtime codex --since 7d --by project
python <plugin>/scripts/token_usage.py insights --runtime codex <rollout.jsonl>
python <plugin>/scripts/token_usage.py top_consumers --runtime codex --since 30d
python <plugin>/scripts/token_usage.py json --runtime codex --diff <old.jsonl> <new.jsonl>
python <plugin>/scripts/token_usage.py live --runtime codex <rollout.jsonl>
python <plugin>/scripts/token_usage.py dashboard --runtime codex --output usage.html
python <plugin>/scripts/token_usage.py export --runtime codex --scope history --output usage.jsonl
```

Read-only sources are `$CODEX_HOME/sessions` and `archived_sessions`, defaulting
to `~/.codex`; tests and exports can use `TOKEN_USAGE_CODEX_HOME`. Reports and
ledgers are local. Do not read credentials or send transcripts elsewhere.

Report measurement confidence and warnings. Attribution follows turns and explicit
skill names; it does not infer which skill caused a shell call. Cached input is
part of input, and reasoning is part of output: never add them twice. Missing
usage is unmeasured, not free. Unpriced models display an unknown cost; don't
guess a rate. Costs use API-equivalent estimates and user pricing overlays,
not the user's ChatGPT subscription bill or remaining allowance.

The Stop hook updates a separate `codex-<session-id>.json` ledger and emits budget
nudges when priced usage crosses TOKEN_USAGE_BUDGET_USD. Hooks require the user's
review through `/hooks`; installation does not grant trust. Python must be on PATH.
