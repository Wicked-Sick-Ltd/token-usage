# Use cases

## The problem it solves

Claude Code tells you session totals (`/cost`, OTel metrics), and community tools
aggregate by day or model, but nothing answers *"the PR review cost 120k tokens, the
refactor cost 800k"*. token-usage parses the session transcript and attributes every
token to the slash command or skill (and its subagents) that consumed it:

```
| Activity                      | Calls | Output | Input | Cache read | Cache write | Est. cost |
|-------------------------------|------:|-------:|------:|-----------:|------------:|----------:|
| `/code-review` (+5 agents)    |     1 | 180.2k |  3.1k |      42.3M |        1.2M |    $29.40 |
| (no command)                  |     4 |  31.7k |  9.9k |       3.5M |      244.5k |     $4.20 |
| `/commit`                     |     2 |   2.4k |  0.8k |     310.0k |       18.0k |     $0.27 |
| **Total**                     |       |  214k  | 13.8k |      46.1M |        1.5M |    $33.87 |
```

Totals always reconcile: every turn belongs to exactly one row, and `(no command)` holds
only the turns before the session's first command.

## Questions it answers

1. **"Where did my tokens go this session?"** At the end of a long session, run
   `/token-usage:report` (or just ask the question) and get the per-command table above:
   you can see at once that the code review consumed six times everything else.
2. **Is a heavy workflow worth it?** Multi-agent commands (deep code reviews, research
   fan-outs) are powerful but expensive. token-usage shows their true deduplicated,
   cache-aware cost, so you can decide between "run on every PR" and "reserve for
   releases" with real numbers.
3. **What does a command or skill I wrote cost?** Plugin and skill authors see what each
   invocation costs, including cache-write amplification and subagent fan-out. Break it
   down with `report --agents` or `report --models`, and compare before and after with
   `report --diff OLD NEW` when optimising prompts.
4. **Live cost awareness while you work.** The optional statusline
   (`examples/statusline.sh`, or `examples/statusline.ps1` on Windows) shows a running
   figure such as `⏶ 214k out · $33.87 · top: /code-review`, updated after every turn
   from the live ledger. Set `TOKEN_USAGE_BUDGET_USD` for a nudge when a session crosses
   your budget.
5. **Auditing a past or headless session.** The standalone CLI
   (`python3 scripts/token_usage.py report <transcript.jsonl>`) analyses any transcript
   outside Claude Code, and `json` output feeds dashboards or CI cost tracking of
   `claude -p` automation.
6. **Sanity-checking subagent-heavy sessions.** Sessions with dozens of agents are where
   naive counting fails: Claude Code writes the same request's usage to several
   transcript entries while streaming, so a plain sum overcounts by about 2.5×.
   token-usage deduplicates by `requestId` and rolls each subagent into the command that
   spawned it.
7. **Which project ate the tokens this week?** `history --by project --since 7d` (or
   `--by day`, `command` or `model`, filtered with `--project`) rolls up every session on
   the machine, with a burn-rate footer and `--csv` export for spreadsheets.
8. **What were the costliest sessions or commands this month?**
   `top_consumers --by session|command --since 30d` ranks the window.
9. **Asking without a shell.** With the plugin enabled, the bundled MCP server answers
   "where did my tokens go?" or "which sessions cost the most this month?" through the
   `session_cost`, `insights` and `top_consumers` tools.
10. **The same questions in Codex and Cursor.** `--runtime codex` and `--runtime cursor`
    run the same reports over Codex rollouts and Cursor sessions; see the
    [Codex](codex-adapter.md) and [Cursor](cursor-adapter.md) notes for what each can
    measure.
