// Copilot supplies this SDK; no npm install or network connection is needed.
import { joinSession } from "@github/copilot-sdk/extension";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

try {
  const session = await joinSession();
  if (!/^[A-Za-z0-9_-]+$/.test(session.sessionId)) throw new Error("Invalid session id");
  const root = join(process.env.COPILOT_HOME || join(homedir(), ".copilot"), "token-usage");
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const path = join(root, `${session.sessionId}.jsonl`);
  const write = (event) => {
    try { appendFileSync(path, JSON.stringify(event) + "\n", { mode: 0o600 }); } catch {
      // Fail open: reporting must never prevent a Copilot turn from completing.
    }
  };
  write({ type: "token-usage.capture", timestamp: new Date().toISOString(),
    data: { sessionId: session.sessionId, context: { cwd: process.cwd() } } });
  session.on((event) => {
    const source = event.data || {};
    let data;
    if (event.type === "assistant.usage") {
      data = Object.fromEntries(["model", "inputTokens", "outputTokens", "cacheReadTokens",
        "cacheWriteTokens", "reasoningTokens", "cacheTtlSeconds"].filter(k => source[k] !== undefined)
        .map(k => [k, source[k]]));
    } else if (event.type === "user.message") {
      // Persist only the command label, never the user's prompt or tool output.
      data = { label: typeof source.content === "string"
        ? source.content.match(/^\s*([$\/][A-Za-z0-9][A-Za-z0-9_:-]*)\b/)?.[1] : undefined };
    } else if (event.type === "skill.invoked") {
      data = { name: source.name };
    } else {
      return;
    }
    write({ type: event.type, id: event.id, timestamp: event.timestamp, agentId: event.agentId, data });
  });
} catch {
  // Missing experimental SDK support or an unwritable ledger is non-fatal.
}
