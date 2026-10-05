"""Opt-in real Copilot CLI check against a loopback-only synthetic provider.

Set COPILOT_TEST_CLI to the CLI executable; no login, model spend or npm install.
"""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

CLI = os.environ.get("COPILOT_TEST_CLI")
pytestmark = pytest.mark.skipif(not CLI, reason="COPILOT_TEST_CLI is not configured")


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_native_collector_mcp_discovery_and_resume(provider, tmp_path, monkeypatch, tu):
    tools_seen = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            for tool in request.get("tools", []):
                tools_seen.add(tool.get("function", tool).get("name", ""))
            if provider == "openai":
                data = {"id": "synthetic", "object": "chat.completion", "created": 1, "model": "gpt-4.1",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done."},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 110, "completion_tokens": 20, "total_tokens": 130,
                                  "prompt_tokens_details": {"cached_tokens": 40},
                                  "completion_tokens_details": {"reasoning_tokens": 7}}}
                body, content_type = json.dumps(data).encode(), "application/json"
            else:
                message = {"id": "synthetic", "type": "message", "role": "assistant",
                           "model": "claude-sonnet-4", "content": [], "stop_reason": None,
                           "usage": {"input_tokens": 60, "output_tokens": 0,
                                     "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10}}
                events = [
                    {"type": "message_start", "message": message},
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Done."}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                     "usage": {"output_tokens": 20}},
                    {"type": "message_stop"},
                ]
                body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "profile"
    monkeypatch.setenv("TOKEN_USAGE_COPILOT_HOME", str(profile))
    # The host must have no inherited credentials or provider commands.
    env = {k: v for k, v in os.environ.items()
           if not any(s in k.upper() for s in ("TOKEN", "API_KEY", "BEARER", "COPILOT", "GITHUB"))}
    env.update(COPILOT_HOME=str(profile), COPILOT_OFFLINE="true", COPILOT_AUTO_UPDATE="false",
               COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{server.server_port}",
               COPILOT_PROVIDER_TYPE=provider, COPILOT_PROVIDER_API_KEY="synthetic-local-only",
               COPILOT_MODEL="gpt-4.1" if provider == "openai" else "claude-sonnet-4")
    command = [CLI, "--no-auto-update", "--experimental", "--plugin-dir", str(Path(__file__).resolve().parents[1]),
               "--disable-builtin-mcps", "--no-custom-instructions", "--no-ask-user", "--stream", "off",
               "--output-format", "json", "-p", "Say hello. PRIVATE_SYNTHETIC_PROMPT"]
    try:
        adapter = tu.get_runtime_adapter("copilot")
        for count in (1, 2):
            result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                                    text=True, encoding="utf-8", timeout=60, check=False)
            assert result.returncode == 0, result.stderr + result.stdout
            source = next(adapter.iter_sessions())
            ledger = profile / "token-usage" / f"{adapter.session_id(source)}.jsonl"
            assert "PRIVATE_SYNTHETIC_PROMPT" not in ledger.read_text(encoding="utf-8")
            parsed = adapter.parse(source)
            assert parsed["measurement"] == "exact", parsed["warnings"]
            total = tu.aggregate(parsed["segments"], {})["total"]["usage"]
            assert total == {"input": (70 if provider == "openai" else 60) * count,
                             "output": 20 * count, "cache_read": 40 * count,
                             "cache_5m": (0 if provider == "openai" else 10) * count,
                             "cache_1h": 0, "requests": count}
            command += ["--resume", adapter.session_id(source)]
        assert {f"token-usage-{name}" for name in ("session_cost", "history", "diff", "insights", "top_consumers")} <= tools_seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
