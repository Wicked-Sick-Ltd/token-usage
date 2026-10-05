# Token Usage for Gemini CLI

For usage questions, use the bundled token-usage MCP tools with `runtime: "gemini"`.
Prefer an explicit transcript or session ID when known. The shared report skill's
Claude examples require `--runtime gemini` when run here; use `python` on Windows
and `python3` on macOS/Linux. Resolve bundled scripts relative to this extension.

Read only local Gemini session recordings. Never inspect credentials or send
transcripts elsewhere. Preserve measurement warnings and unknown prices in the
answer. Reasoning tokens count as output; cached input counts once. API-equivalent
cost estimates do not represent subscription charges or remaining quota.
