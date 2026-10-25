"""Opt-in llm1 activity bridge: turn extraction, payloads, spool, watermarks."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BRIDGE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "activity_bridge.py"
SYNC_BIN = Path(__file__).resolve().parent.parent / "bin" / "token-usage-activity-sync"

_spec = importlib.util.spec_from_file_location("activity_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class FakePoster:
    def __init__(self, fail_substrings=None):
        self.calls = []
        self.fail_substrings = list(fail_substrings or [])

    def __call__(self, url, body, token, timeout):
        self.calls.append({
            "url": url,
            "body": body,
            "token": token,
            "timeout": timeout,
        })
        for part in self.fail_substrings:
            if part in url:
                raise bridge.DeliveryError("offline")
        return 200


def paths_of(poster):
    return [call["url"].split("://", 1)[1].split("/", 1)[1] for call in poster.calls]


def cfg(tmp_path, **kwargs):
    values = {
        "coord_url": "http://coord.test:8140",
        "coord_token": "coord-token",
        "twin_url": "http://twin.test:8100",
        "twin_token": "twin-token",
        "enabled": True,
        "state_dir": tmp_path / "state",
        "timeout": 0.4,
        "machine": "llm1-test",
        "repo": "Wicked-Sick-Ltd/token-usage",
        "branch": "main",
        "surface": "",
        "vendor": "",
        "sync_dirs": [],
    }
    values.update(kwargs)
    return bridge.Config(**values)


def claude_turn_records():
    return [
        {
            "type": "user",
            "timestamp": "2026-10-25T11:00:00Z",
            "message": {"role": "user", "content": "Ship the bridge"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-25T11:00:01Z",
            "message": {
                "role": "assistant",
                "model": "grok-4",
                "content": [
                    {"type": "text", "text": "I'll read the hook."},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"path": "hooks/hooks-cursor.json"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-25T11:00:02Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "hook json"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-25T11:00:03Z",
            "message": {
                "role": "assistant",
                "model": "grok-4",
                "content": [{"type": "text", "text": "Hook is registered."}],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 6,
                    "cache_read_input_tokens": 3,
                    "cache_creation": {"ephemeral_5m_input_tokens": 2, "ephemeral_1h_input_tokens": 1},
                },
            },
        },
        "not json object",
    ]


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for rec in records:
        if isinstance(rec, str):
            lines.append(rec)
        else:
            lines.append(json.dumps(rec))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_extracts_one_turn_with_tool_result_and_usage():
    turns = bridge.extract_turns([rec for rec in claude_turn_records() if isinstance(rec, dict)])
    assert len(turns) == 1
    turn = turns[0]
    assert turn.prompt == "Ship the bridge"
    assert turn.assistant == "I'll read the hook.\n\nHook is registered."
    assert turn.model == "grok-4"
    assert turn.tools[0]["name"] == "Read"
    assert turn.tools[0]["result"] == "hook json"
    assert turn.usage == {
        "input_tokens": 22,
        "output_tokens": 10,
        "cache_read_tokens": 3,
        "cache_write_tokens": 3,
    }


def test_flat_grok_transcript_and_markdown():
    records = [
        {"role": "user", "content": "hello", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant",
            "content": "hi",
            "model": "grok-4",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "search", "arguments": "{\"q\": \"llm1\"}"}}],
        },
        {"role": "tool", "name": "search", "tool_call_id": "1", "content": "found"},
    ]
    turns = bridge.extract_turns(records)
    assert len(turns) == 1
    assert turns[0].tools[0]["input"] == {"q": "llm1"}
    assert turns[0].tools[0]["result"] == "found"
    text = bridge.format_turn_markdown("grok", "grok-4", turns[0])
    assert text.startswith("# Chat turn — grok/grok-4\n")
    assert "## User\nhello\n" in text
    assert "- `search` {\"q\": \"llm1\"}" in text
    assert "result: found" in text
    assert text.endswith("## Assistant\nhi\n")


def test_payload_shapes_for_coord_and_twin():
    turn = bridge.extract_turns([rec for rec in claude_turn_records() if isinstance(rec, dict)])[0]
    reg = bridge.register_body(
        "sess-1", "box", "/workspace", "Wicked-Sick-Ltd/token-usage", "main",
        "cursor", "grok-4", "unpriced",
    )
    assert set(reg) == {
        "session_id", "machine", "cwd", "repo", "branch", "vendor", "model", "cost_basis",
    }
    assert reg["vendor"] == "cursor"
    beat = bridge.heartbeat_body(
        "sess-1", turn.prompt, turn.assistant, 1, 0.0, "grok-4", "cursor", "ide",
    )
    assert set(beat) == {
        "session_id", "last_prompt", "last_output", "turn_index", "session_cost_usd",
        "model", "vendor", "surface",
    }
    usage = bridge.sum_usage([turn])
    summary = bridge.ai_sessions_body(
        "sess-1", "cursor", "cloud_agent", usage, 1, "Wicked-Sick-Ltd/token-usage", "main",
        turn.timestamp, "2026-10-25T11:00:03Z", "grok-4", "box", 0.0, "unpriced",
    )
    for key in (
        "vendor", "surface", "session_id", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "turns", "repo", "branch",
        "started_at", "updated_at",
    ):
        assert key in summary
    assert summary["vendor"] == "cursor"
    assert summary["input_tokens"] == 22
    assert summary["cache_write_tokens"] == 3
    ingest = bridge.ingest_body(
        "sess-1", 1, "grok", "grok-4", turn, "Wicked-Sick-Ltd/token-usage", "main",
        "box", "grok_bot",
    )
    assert set(ingest) == {"source_type", "source_id", "content", "metadata"}
    assert ingest["source_type"] == "grok_bot"
    assert ingest["source_id"] == "sess-1:turn-1"
    assert set(ingest["metadata"]) == {"repo", "branch", "machine", "surface", "timestamp"}
    assert ingest["content"].startswith("# Chat turn — grok/grok-4\n")


def test_long_fields_are_clipped():
    turn = bridge.Turn()
    turn.prompt = "p" * (bridge.FIELD_LIMIT + 10)
    turn.assistant_parts.append("a" * 5)
    text = bridge.format_turn_markdown("cursor", "m", turn)
    assert "…[truncated]" in text
    assert "p" * (bridge.FIELD_LIMIT + 1) not in text


def test_disabled_without_urls(tmp_path, monkeypatch):
    monkeypatch.delenv("WIZZO_COORD_URL", raising=False)
    monkeypatch.delenv("WIZZO_TWIN_API_URL", raising=False)
    monkeypatch.delenv("WIZZO_ACTIVITY_BRIDGE", raising=False)
    config = bridge.load_config(environ={
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "WIZZO_ACTIVITY_STATE_DIR": str(tmp_path / "state"),
    })
    assert config.enabled is False
    poster = FakePoster()
    assert bridge.process_hook({"hook_event_name": "stop", "conversation_id": "c"}, config, poster) == 0
    assert poster.calls == []
    assert not (tmp_path / "state" / "bridge.sqlite").exists()


def test_env_file_enables_bridge(tmp_path):
    xdg = tmp_path / "xdg"
    env_file = xdg / "wizzo" / "activity-bridge.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "# llm1\nexport WIZZO_COORD_URL=http://100.105.90.34:8140\nWIZZO_COORD_TOKEN=from-file\nWIZZO_TWIN_API_URL='http://100.105.90.34:8100'" + "\n",
        encoding="utf-8",
    )
    config = bridge.load_config(environ={
        "XDG_CONFIG_HOME": str(xdg),
        "WIZZO_ACTIVITY_STATE_DIR": str(tmp_path / "state"),
        "WIZZO_COORD_URL": "http://override.test:8140",
    })
    assert config.enabled is True
    assert config.coord_url == "http://override.test:8140"
    assert config.coord_token == "from-file"
    assert config.twin_url == "http://100.105.90.34:8100"
    forced = bridge.load_config(environ={
        "XDG_CONFIG_HOME": str(xdg),
        "WIZZO_ACTIVITY_BRIDGE": "0",
        "WIZZO_ACTIVITY_STATE_DIR": str(tmp_path / "state"),
    })
    assert forced.enabled is False


def test_cursor_stop_pairs_prompt_and_posts_both_targets(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    prompt = {
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-1",
        "generation_id": "gen-1",
        "model": "cursor-grok",
        "prompt": "Where did the tokens go?",
        "workspace_roots": ["/workspace/token-usage"],
    }
    stop = {
        "hook_event_name": "stop",
        "conversation_id": "conv-1",
        "generation_id": "gen-1",
        "model": "cursor-grok",
        "output": "They went to the review.",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 5,
        "cache_write_tokens": 1,
        "session_cost_usd": 0.25,
    }
    assert bridge.process_hook(prompt, config, poster, runtime="cursor") == 0
    assert bridge.process_hook(stop, config, poster, runtime="cursor") == 0
    paths = paths_of(poster)
    assert "session/register" in paths
    assert "session/heartbeat" in paths
    assert "session/turn" in paths
    assert "api/ai-sessions" in paths
    assert "v1/ingest" in paths
    ingest = [call for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert len(ingest) == 1
    body = ingest[0]["body"]
    assert body["source_type"] == "cursor"
    assert body["source_id"] == "conv-1:turn-1"
    assert "Where did the tokens go?" in body["content"]
    assert "They went to the review." in body["content"]
    assert body["content"].startswith("# Chat turn — cursor/cursor-grok\n")
    assert ingest[0]["token"] == "twin-token"
    register = next(call for call in poster.calls if call["url"].endswith("/session/register"))
    assert register["token"] == "coord-token"
    assert register["body"]["cost_basis"] == "unpriced"
    summary = [call for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["body"]["vendor"] == "cursor"
    assert summary["body"]["surface"] == "ide"
    # Cursor reports input inclusive of cache. 100 - 5 - 1 = 94.
    assert summary["body"]["input_tokens"] == 94
    assert summary["body"]["cache_read_tokens"] == 5
    assert summary["body"]["cache_write_tokens"] == 1
    assert summary["body"]["session_cost_usd"] == 0.25
    assert summary["body"]["cost_basis"] == "payload"
    assert summary["body"]["repo"] == "Wicked-Sick-Ltd/token-usage"


def test_stop_reads_transcript_tool_calls(tmp_path):
    path = write_jsonl(tmp_path / "sess.jsonl", claude_turn_records())
    poster = FakePoster()
    bridge.process_hook(
        {
            "hook_event_name": "Stop",
            "session_id": "sess-9",
            "transcript_path": str(path),
            "cwd": "/workspace/token-usage",
        },
        cfg(tmp_path),
        poster,
        runtime="claude",
    )
    ingest = next(call for call in poster.calls if call["url"].endswith("/v1/ingest"))
    assert ingest["body"]["source_type"] == "claude"
    assert ingest["body"]["source_id"] == "sess-9:turn-1"
    assert "Read" in ingest["body"]["content"]
    assert ingest["body"]["metadata"]["surface"] == "claude_code"
    summary = next(call for call in poster.calls if call["url"].endswith("/api/ai-sessions"))
    assert summary["body"]["vendor"] == "claude"
    assert summary["body"]["output_tokens"] == 10


def test_offline_spool_then_flush_is_idempotent(tmp_path):
    poster = FakePoster(fail_substrings=["coord.test", "twin.test"])
    config = cfg(tmp_path)
    bridge.process_hook(
        {
            "hook_event_name": "stop",
            "conversation_id": "conv-off",
            "generation_id": "g",
            "prompt": "keep me",
            "output": "stored",
            "model": "m",
        },
        config,
        poster,
        runtime="cursor",
    )
    store = bridge.Store(config.state_dir)
    try:
        assert store.pending_count() >= 1
        rows = store.pending(20)
        blob = " ".join(row[5] for row in rows)
        assert "coord-token" not in blob
        assert "twin-token" not in blob
        assert "keep me" in blob
    finally:
        store.close()
    poster.fail_substrings = []
    live = bridge.Bridge(config, poster=poster, store=bridge.Store(config.state_dir))
    try:
        sent = live.flush(20)
        assert sent >= 1
        assert live.store.pending_count() == 0
    finally:
        live.close()
    before = len(poster.calls)
    again = bridge.Bridge(config, poster=poster, store=bridge.Store(config.state_dir))
    try:
        assert again.flush(20) == 0
        assert again.store.pending_count() == 0
    finally:
        again.close()
    assert len(poster.calls) == before


def test_twin_failure_still_keeps_coord_and_retries_ingest(tmp_path):
    poster = FakePoster(fail_substrings=["/v1/ingest"])
    config = cfg(tmp_path)
    records = [rec for rec in claude_turn_records() if isinstance(rec, dict)]
    path = write_jsonl(tmp_path / "cloud-agent-transcripts" / "bc-1.jsonl", records)
    delivered, spooled = bridge.sync_tree(path.parent, config, poster=poster, vendor="cursor")
    assert delivered >= 1
    assert spooled >= 1
    assert any(call["url"].endswith("/session/register") for call in poster.calls)
    assert any(call["url"].endswith("/api/ai-sessions") for call in poster.calls)
    assert any(call["url"].endswith("/v1/ingest") for call in poster.calls)
    poster.fail_substrings = []
    before_ingest = len([c for c in poster.calls if c["url"].endswith("/v1/ingest")])
    bridge.sync_tree(path.parent, config, poster=poster, vendor="cursor")
    ingest = [c for c in poster.calls if c["url"].endswith("/v1/ingest")]
    assert len(ingest) == before_ingest + 1
    assert ingest[-1]["body"]["source_type"] == "cursor"
    assert ingest[-1]["body"]["metadata"]["surface"] == "cloud_agent"
    third = len(poster.calls)
    bridge.sync_tree(path.parent, config, poster=poster, vendor="cursor")
    assert len(poster.calls) == third


def test_sync_watermark_skips_delivered_turns_and_sends_new_ones(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path, surface="grok_bot", vendor="grok")
    root = tmp_path / "grok"
    path = write_jsonl(root / "thread.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {"role": "assistant", "content": "uno", "model": "grok-4", "ts": "2026-10-25T11:00:01Z"},
    ])
    bridge.sync_tree(root, config, poster=poster)
    first_ids = [c["body"]["source_id"] for c in poster.calls if c["url"].endswith("/v1/ingest")]
    assert first_ids == ["thread:turn-1"]
    marked = len(poster.calls)
    bridge.sync_tree(root, config, poster=poster)
    assert len(poster.calls) == marked
    extra = path.read_text(encoding="utf-8")
    extra += json.dumps({"role": "user", "content": "two", "ts": "2026-10-25T11:02:00Z"}) + "\n"
    extra += json.dumps({
        "role": "assistant", "content": "dos", "model": "grok-4", "ts": "2026-10-25T11:02:01Z",
    }) + "\n"
    path.write_text(extra, encoding="utf-8")
    bridge.sync_tree(root, config, poster=poster)
    ids = [c["body"]["source_id"] for c in poster.calls if c["url"].endswith("/v1/ingest")]
    assert ids == ["thread:turn-1", "thread:turn-2"]
    grok_summary = [c for c in poster.calls if c["url"].endswith("/api/ai-sessions")][-1]
    assert grok_summary["body"]["vendor"] == "grok"
    assert grok_summary["body"]["turns"] == 2


def test_only_configured_target_is_called(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path, coord_url="", coord_token="")
    bridge.process_hook(
        {
            "hook_event_name": "stop",
            "conversation_id": "only-twin",
            "generation_id": "g",
            "prompt": "hi",
            "output": "yo",
        },
        config,
        poster,
        runtime="cursor",
    )
    assert paths_of(poster) == ["v1/ingest"]


def test_repo_slug_and_git_lookup(tmp_path):
    assert bridge.repo_slug("git@github.com:Wicked-Sick-Ltd/token-usage.git", "") == \
        "Wicked-Sick-Ltd/token-usage"
    assert bridge.repo_slug("https://github.com/Wicked-Sick-Ltd/token-usage", "") == \
        "Wicked-Sick-Ltd/token-usage"
    assert bridge.repo_slug("", "/tmp/token-usage") == "token-usage"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/demo"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Acme/widgets.git"],
                   cwd=repo, check=True, capture_output=True)
    bridge._GIT_CACHE.clear()
    slug, branch = bridge.lookup_git(repo)
    assert slug == "Acme/widgets"
    assert branch == "demo"


def test_hook_cli_fail_open_stdout_contract(tmp_path):
    env = os.environ.copy()
    env["WIZZO_ACTIVITY_BRIDGE"] = "0"
    env["WIZZO_ACTIVITY_STATE_DIR"] = str(tmp_path / "state")
    cursor = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "hook", "--stdout", "cursor", "--runtime", "cursor"],
        input="not json{",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert cursor.returncode == 0
    assert cursor.stdout.strip() == "{}"
    claude = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "hook", "--stdout", "claude", "--runtime", "claude"],
        input="null",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert claude.returncode == 0
    assert claude.stdout == ""


def test_hook_cli_posts_to_local_server(tmp_path):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            seen.append({
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(raw.decode("utf-8")),
            })
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "WIZZO_COORD_URL": f"http://127.0.0.1:{port}",
            "WIZZO_COORD_TOKEN": "coord-secret",
            "WIZZO_TWIN_API_URL": f"http://127.0.0.1:{port}",
            "WIZZO_TWIN_TOKEN": "twin-secret",
            "WIZZO_ACTIVITY_STATE_DIR": str(tmp_path / "state"),
            "WIZZO_MACHINE": "vm",
            "WIZZO_REPO": "Wicked-Sick-Ltd/token-usage",
            "WIZZO_BRANCH": "main",
            "WIZZO_ACTIVITY_HTTP_TIMEOUT": "2",
        })
        payload = {
            "hook_event_name": "stop",
            "conversation_id": "wire-1",
            "generation_id": "g1",
            "model": "grok-4",
            "prompt": "ping",
            "output": "pong",
        }
        result = subprocess.run(
            [sys.executable, str(BRIDGE_PATH), "hook", "--runtime", "cursor", "--stdout", "cursor"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "{}"
    paths = [item["path"] for item in seen]
    assert "/session/register" in paths
    assert "/session/heartbeat" in paths
    assert "/session/turn" in paths
    assert "/api/ai-sessions" in paths
    assert "/v1/ingest" in paths
    ingest = next(item for item in seen if item["path"] == "/v1/ingest")
    assert ingest["auth"] == "Bearer twin-secret"
    assert ingest["body"]["source_id"] == "wire-1:turn-1"
    assert ingest["body"]["metadata"]["machine"] == "vm"
    register = next(item for item in seen if item["path"] == "/session/register")
    assert register["auth"] == "Bearer coord-secret"


def test_sync_cli_and_wrapper(tmp_path):
    root = tmp_path / "cloud-agent-transcripts"
    write_jsonl(root / "agent.jsonl", [
        {"role": "user", "content": "backfill", "ts": "2026-10-25T12:00:00Z"},
        {"role": "assistant", "content": "done", "model": "grok-4", "ts": "2026-10-25T12:00:01Z"},
    ])
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            seen.append((self.path, json.loads(raw.decode("utf-8"))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env.update({
        "WIZZO_COORD_URL": f"http://127.0.0.1:{port}",
        "WIZZO_TWIN_API_URL": f"http://127.0.0.1:{port}",
        "WIZZO_ACTIVITY_STATE_DIR": str(tmp_path / "state"),
        "WIZZO_REPO": "Wicked-Sick-Ltd/token-usage",
        "WIZZO_BRANCH": "main",
        "WIZZO_ACTIVITY_HTTP_TIMEOUT": "2",
    })
    try:
        result = subprocess.run(
            [sys.executable, str(BRIDGE_PATH), "--sync-dir", str(root), "--vendor", "cursor",
             "--surface", "cloud_agent"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "delivered=" in result.stderr
        second = subprocess.run(
            [str(SYNC_BIN), "--sync-dir", str(root), "--vendor", "cursor", "--surface", "cloud_agent"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert second.returncode == 0, second.stderr
    ingest = [body for path, body in seen if path == "/v1/ingest"]
    assert len(ingest) == 1
    assert ingest[0]["source_id"] == "agent:turn-1"
    assert ingest[0]["metadata"]["surface"] == "cloud_agent"
    db = sqlite3.connect(str(tmp_path / "state" / "bridge.sqlite"))
    try:
        count = db.execute("SELECT COUNT(*) FROM delivered WHERE endpoint='ingest'").fetchone()[0]
    finally:
        db.close()
    assert count == 1


def test_malformed_lines_and_second_user_message_split_turns():
    records = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "done"},
    ]
    turns = bridge.extract_turns(records)
    assert [turn.prompt for turn in turns] == ["first", "second"]
    assert [turn.assistant for turn in turns] == ["ack", "done"]


def test_subagent_stop_source_id(tmp_path):
    poster = FakePoster()
    bridge.process_hook(
        {
            "hook_event_name": "subagentStop",
            "conversation_id": "parent",
            "subagent_id": "child",
            "generation_id": "g",
            "prompt": "search",
            "output": "found it",
            "model": "m",
        },
        cfg(tmp_path),
        poster,
        runtime="cursor",
    )
    ingest = next(call for call in poster.calls if call["url"].endswith("/v1/ingest"))
    assert ingest["body"]["source_id"] == "parent:child:turn-1"


def test_sync_missing_dir_is_a_usage_error():
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("WIZZO_"):
            env.pop(name)
    env["WIZZO_ACTIVITY_BRIDGE"] = "0"
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "sync"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2
    assert "sync-dir" in result.stderr


def test_second_prompt_does_not_zero_the_session_summary(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-sum",
        "generation_id": "g1",
        "model": "cursor-grok",
        "prompt": "first question",
    }, config, poster, runtime="cursor")
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "conv-sum",
        "generation_id": "g1",
        "model": "cursor-grok",
        "output": "first answer",
        "input_tokens": 80,
        "output_tokens": 8,
    }, config, poster, runtime="cursor")
    summaries = [call for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert len(summaries) == 1
    assert summaries[0]["body"]["input_tokens"] == 80
    bridge.process_hook({
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-sum",
        "generation_id": "g2",
        "model": "cursor-grok",
        "prompt": "second question",
    }, config, poster, runtime="cursor")
    summaries = [call for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert len(summaries) == 1
    assert summaries[0]["body"]["input_tokens"] == 80
    assert summaries[0]["body"]["output_tokens"] == 8


def test_subagent_start_does_not_replace_the_parent_prompt(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv",
        "generation_id": "gen-parent",
        "model": "parent-model",
        "prompt": "parent prompt",
    }, config, poster, runtime="cursor")
    bridge.process_hook({
        "hook_event_name": "subagentStart",
        "conversation_id": "conv",
        "generation_id": "gen-parent",
        "subagent_id": "sub-1",
        "model": "child-model",
        "task": "child task",
    }, config, poster, runtime="cursor")
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "conv",
        "generation_id": "gen-parent",
        "output": "parent answer",
    }, config, poster, runtime="cursor")
    ingest = [call for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert len(ingest) == 1
    assert ingest[0]["body"]["source_id"] == "conv:turn-1"
    assert "parent prompt" in ingest[0]["body"]["content"]
    assert "child task" not in ingest[0]["body"]["content"]
    assert "parent-model" in ingest[0]["body"]["content"]


def test_pascal_subagent_stop_uses_the_sidechain_identity(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    sidechain = write_jsonl(tmp_path / "parent" / "subagents" / "agent-abc.jsonl", [
        {"role": "user", "content": "child question", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "child answer", "model": "child-model",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    ])
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "parent",
        "generation_id": "g-parent",
        "model": "parent-model",
        "prompt": "parent prompt",
        "output": "parent answer",
        "input_tokens": 100,
        "output_tokens": 10,
    }, config, poster, runtime="claude")
    bridge.process_hook({
        "hook_event_name": "SubagentStop",
        "session_id": "parent",
        "transcript_path": str(sidechain),
    }, config, poster, runtime="codex")
    ingest_ids = [call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert "parent:turn-1" in ingest_ids
    assert "parent:agent-abc:turn-1" in ingest_ids
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    parent_rows = [body for body in summaries if body["session_id"] == "parent"]
    child_rows = [body for body in summaries if body["session_id"] == "parent:agent-abc"]
    assert len(parent_rows) == 1
    assert parent_rows[0]["input_tokens"] == 100
    assert child_rows
    assert child_rows[-1]["input_tokens"] == 3
    assert all(body["session_id"] != "parent" or body["input_tokens"] == 100 for body in summaries)


def test_sync_publishes_one_stable_session_summary(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path, surface="grok_bot", vendor="grok")
    root = tmp_path / "grok"
    write_jsonl(root / "thread.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {"role": "assistant", "content": "uno", "model": "model-a", "ts": "2026-10-25T11:00:01Z"},
        {"role": "user", "content": "two", "ts": "2026-10-25T11:02:00Z"},
        {"role": "assistant", "content": "dos", "model": "model-b", "ts": "2026-10-25T11:02:01Z"},
    ])
    bridge.sync_tree(root, config, poster=poster)
    summaries = [call for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert len(summaries) == 1
    assert summaries[0]["body"]["turns"] == 2
    assert summaries[0]["body"]["started_at"] == "2026-10-25T11:00:00Z"
    assert summaries[0]["body"]["updated_at"] == "2026-10-25T11:02:01Z"
    assert summaries[0]["body"]["model"] == "model-b"
    ingests = [call["body"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert ingests[0]["content"].startswith("# Chat turn — grok/model-a\n")
    assert ingests[1]["content"].startswith("# Chat turn — grok/model-b\n")
    marked = len(poster.calls)
    bridge.sync_tree(root, config, poster=poster)
    assert len(poster.calls) == marked


def test_timestamp_free_jsonl_does_not_repost(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    root = tmp_path / "logs"
    write_jsonl(root / "flat.jsonl", [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi", "model": "grok-4"},
    ])
    bridge.sync_tree(root, config, poster=poster, vendor="grok")
    ingest = [call for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert len(ingest) == 1
    assert ingest[0]["body"]["metadata"]["timestamp"] == ""
    marked = len(poster.calls)
    bridge.sync_tree(root, config, poster=poster, vendor="grok")
    assert len(poster.calls) == marked


def test_timeout_is_spooled_and_the_hook_exits_zero(tmp_path):
    def boom(url, body, token, timeout):
        raise TimeoutError("timed out")

    config = cfg(tmp_path)
    assert bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "conv-timeout",
        "generation_id": "g",
        "prompt": "keep me",
        "output": "stored",
        "model": "m",
    }, config, boom, runtime="cursor") == 0
    store = bridge.Store(config.state_dir)
    try:
        assert store.pending_count() >= 1
        blob = " ".join(row[5] for row in store.pending(20))
        assert "keep me" in blob
    finally:
        store.close()


def test_stale_spooled_snapshot_does_not_replay_over_a_newer_turn(tmp_path):
    poster = FakePoster(fail_substrings=["coord.test", "twin.test"])
    config = cfg(tmp_path)

    def stop(number, prompt, output):
        return {
            "hook_event_name": "stop",
            "conversation_id": "conv",
            "generation_id": f"g{number}",
            "prompt": prompt,
            "output": output,
            "model": "m",
            "input_tokens": number,
            "output_tokens": number,
        }

    bridge.process_hook(stop(1, "old", "old-out"), config, poster, runtime="cursor")
    poster.fail_substrings = []
    bridge.process_hook(stop(2, "new", "new-out"), config, poster, runtime="cursor")
    live = bridge.Bridge(config, poster=poster, store=bridge.Store(config.state_dir))
    try:
        live.flush(50)
    finally:
        live.close()
    heartbeats = [call for call in poster.calls if call["url"].endswith("/session/heartbeat")]
    assert heartbeats
    assert all(call["body"]["last_prompt"] != "old" for call in heartbeats)
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert summaries
    assert all(body["input_tokens"] != 1 for body in summaries)
    ingest_ids = {call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")}
    assert ingest_ids == {"conv:turn-1", "conv:turn-2"}


def test_codex_response_item_payload_and_usage_dedup():
    records = [
        {"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
         "payload": {"id": "thread-1", "cwd": "/tmp/project"}},
        {"type": "turn_context", "timestamp": "2026-10-01T10:00:00Z",
         "payload": {"model": "gpt-test", "turn_id": "turn-1"}},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:00Z", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "$review check the change"}],
        }},
        {"type": "response_item", "timestamp": "2026-10-01T10:00:01Z", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "checked"}],
        }},
        {"type": "token_usage_record", "timestamp": "2026-10-01T10:00:01Z", "payload": {
            "response_id": "response-1",
            "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20},
        }},
        {"type": "token_usage_record", "timestamp": "2026-10-01T10:00:01Z", "payload": {
            "response_id": "response-1",
            "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20},
        }},
    ]
    turns = bridge.extract_turns(records)
    assert len(turns) == 1
    assert turns[0].prompt == "$review check the change"
    assert turns[0].assistant == "checked"
    assert turns[0].model == "gpt-test"
    assert turns[0].usage == {
        "input_tokens": 60,
        "output_tokens": 20,
        "cache_read_tokens": 40,
        "cache_write_tokens": 0,
    }
    session_id, model, started, cwd = bridge.session_identity("rollout-thread-1.jsonl", records)
    assert session_id == "thread-1"
    assert model == "gpt-test"
    assert started == "2026-10-01T10:00:00Z"
    assert cwd == "/tmp/project"


def test_cache_creation_breakdown_is_not_added_to_the_flat_total():
    usage = bridge._usage({
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 100,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 40,
                "ephemeral_1h_input_tokens": 10,
            },
        },
    })
    assert usage["cache_write_tokens"] == 50
    flat = bridge._usage({"usage": {"cache_creation_input_tokens": 7}})
    assert flat["cache_write_tokens"] == 7


def test_request_id_snapshots_keep_maxima_and_missing_ids_sum():
    deduped = bridge.extract_turns([
        {"type": "user", "timestamp": "2026-06-12T10:00:00Z",
         "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "requestId": "req_1", "timestamp": "2026-06-12T10:00:01Z",
         "message": {"role": "assistant", "model": "claude-fable-5",
                     "content": [{"type": "text", "text": "partial"}],
                     "usage": {"input_tokens": 100, "output_tokens": 10}}},
        {"type": "assistant", "requestId": "req_1", "timestamp": "2026-06-12T10:00:02Z",
         "message": {"role": "assistant", "model": "claude-fable-5",
                     "content": [{"type": "text", "text": "done"}],
                     "usage": {"input_tokens": 100, "output_tokens": 50}}},
    ])
    assert deduped[0].usage["output_tokens"] == 50
    assert deduped[0].usage["input_tokens"] == 100
    summed = bridge.extract_turns([
        {"type": "assistant", "timestamp": "2026-06-12T10:00:01Z",
         "message": {"role": "assistant", "content": "a", "usage": {"output_tokens": 10}}},
        {"type": "assistant", "timestamp": "2026-06-12T10:00:02Z",
         "message": {"role": "assistant", "content": "b", "usage": {"output_tokens": 20}}},
    ])
    assert summed[0].usage["output_tokens"] == 30


def test_oversized_tool_results_are_clipped_at_extraction():
    huge = "x" * (bridge.FIELD_LIMIT + 5000)
    turns = bridge.extract_turns([
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "tool_calls": [
            {"id": "1", "name": "Read", "input": {"path": "p"}},
        ]},
        {"role": "tool", "tool_call_id": "1", "content": huge},
    ])
    result = turns[0].tools[0]["result"]
    assert result.endswith("…[truncated]")
    assert len(result) < bridge.FIELD_LIMIT + 20
    block = bridge.extract_turns([
        {"type": "user", "message": {"role": "user", "content": "q"}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": huge},
        ]}},
    ])
    assert block[0].tools[0]["result"].endswith("…[truncated]")
    assert len(block[0].tools[0]["result"]) < bridge.FIELD_LIMIT + 20


def test_store_migrates_prompts_to_include_the_agent_key(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    db = sqlite3.connect(directory / "bridge.sqlite")
    db.executescript(
        """
        CREATE TABLE delivered (
            source_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            PRIMARY KEY (source_id, endpoint, content_hash)
        );
        CREATE TABLE spool (
            source_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            target TEXT NOT NULL,
            path TEXT NOT NULL,
            body TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source_id, endpoint, content_hash)
        );
        CREATE TABLE prompts (
            session_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            prompt TEXT NOT NULL,
            model TEXT,
            cwd TEXT,
            turn_index INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (session_id, generation_id)
        );
        INSERT INTO prompts VALUES ('s', 'g', 'parent prompt', 'm', '/tmp', 1, 't');
        """
    )
    db.commit()
    db.close()
    store = bridge.Store(directory)
    try:
        store.save_prompt("s", "g", "child task", "m", "/tmp", 1, "child")
        assert store.load_prompt("s", "g")["prompt"] == "parent prompt"
        assert store.load_prompt("s", "g", "child")["prompt"] == "child task"
        assert store.next_turn_index("s") == 2
        assert store.next_turn_index("s", "child") == 2
    finally:
        store.close()


@pytest.mark.parametrize("bad", ("0", "-1"))
def test_interval_must_be_positive(bad):
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("WIZZO_"):
            env.pop(name)
    env["WIZZO_ACTIVITY_BRIDGE"] = "1"
    env["WIZZO_COORD_URL"] = "http://127.0.0.1:9"
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "sync", "--sync-dir", ".", "--interval", bad],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2


def _codex_event(kind, payload, second=0):
    return {"type": kind, "timestamp": f"2026-10-01T10:00:{second:02d}Z", "payload": payload}


def _codex_total(inp=100, cached=40, out=20):
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}


def test_codex_token_count_deltas_are_not_dropped_or_doubled():
    usage = _codex_total()
    rows = [
        _codex_event("session_meta", {"id": "thread-1", "cwd": "/tmp/project"}),
        _codex_event("turn_context", {"model": "gpt-test", "turn_id": "turn-1"}),
        _codex_event("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "$review check the change"},
        ]}),
        _codex_event("event_msg", {"type": "token_count", "info": {"total_token_usage": usage}}, 1),
        _codex_event("event_msg", {"type": "token_count", "info": {"total_token_usage": usage}}, 1),
        _codex_event("event_msg", {"type": "token_count", "info": None}, 1),
    ]
    turns = bridge.extract_turns(rows)
    assert len(turns) == 1
    assert turns[0].model == "gpt-test"
    assert turns[0].usage == {
        "input_tokens": 60,
        "output_tokens": 20,
        "cache_read_tokens": 40,
        "cache_write_tokens": 0,
    }
    rows.extend([
        _codex_event("turn_context", {"model": "gpt-other", "turn_id": "turn-2"}, 2),
        _codex_event("event_msg", {"type": "token_count", "info": {
            "total_token_usage": _codex_total(200, 60, 50),
        }}, 3),
    ])
    turns = bridge.extract_turns(rows)
    assert turns[0].model == "gpt-test"
    assert turns[1].model == "gpt-other"
    assert turns[1].usage["cache_read_tokens"] == 20
    total = bridge.sum_usage(turns)
    assert total["input_tokens"] == 140
    assert total["output_tokens"] == 50
    both = list(rows)
    both.insert(4, _codex_event("token_usage_record", {
        "response_id": "response-1", "turn_id": "turn-1", "usage": usage,
    }, 1))
    recorded = bridge.extract_turns(both)
    assert bridge.sum_usage(recorded)["input_tokens"] == 60
    assert bridge.sum_usage(recorded)["output_tokens"] == 20


def test_cache_write_input_tokens_are_counted_and_netted_from_input():
    usage = bridge._usage({"usage": {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "cache_write_input_tokens": 10,
        "output_tokens": 20,
    }})
    assert usage["cache_write_tokens"] == 10
    assert usage["cache_read_tokens"] == 40
    assert usage["input_tokens"] == 50
    claude = bridge._usage({"usage": {
        "input_tokens": 10,
        "cache_read_input_tokens": 4,
        "output_tokens": 2,
    }})
    assert claude["input_tokens"] == 10
    assert claude["cache_read_tokens"] == 4


def test_turn_context_does_not_relabel_the_open_turn(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    path = write_jsonl(tmp_path / "rollout.jsonl", [
        _codex_event("turn_context", {"model": "model-a", "turn_id": "turn-1"}),
        _codex_event("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "first"},
        ]}),
        _codex_event("response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "done"},
        ]}, 1),
    ])
    bridge.sync_tree(path.parent, config, poster=poster, vendor="codex")
    ingest = [call for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert len(ingest) == 1
    assert "model-a" in ingest[0]["body"]["content"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_codex_event(
            "turn_context", {"model": "model-b", "turn_id": "turn-2"}, 2,
        )) + "\n")
    bridge.sync_tree(path.parent, config, poster=poster, vendor="codex")
    ingest = [call for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert len(ingest) == 1
    assert "model-a" in ingest[0]["body"]["content"]
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert summaries[-1]["model"] == "model-a"
    turns = bridge.extract_turns(bridge.read_jsonl(path))
    assert turns[0].model == "model-a"
    turns = bridge.extract_turns(bridge.read_jsonl(path) + [
        _codex_event("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "second"},
        ]}, 3),
        _codex_event("response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "next"},
        ]}, 4),
    ])
    assert [turn.model for turn in turns] == ["model-a", "model-b"]


def test_stop_summary_uses_the_final_transcript_timestamp(tmp_path):
    path = write_jsonl(tmp_path / "sess.jsonl", [
        {"role": "user", "content": "prompt", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "answer", "model": "m",
            "ts": "2026-10-25T11:05:00Z",
            "usage": {"input_tokens": 4, "output_tokens": 5},
        },
    ])
    poster = FakePoster()
    bridge.process_hook({
        "hook_event_name": "Stop",
        "session_id": "sess-clock",
        "transcript_path": str(path),
        "cwd": "/workspace/token-usage",
    }, cfg(tmp_path), poster, runtime="claude")
    summary = next(call for call in poster.calls if call["url"].endswith("/api/ai-sessions"))
    ingest = next(call for call in poster.calls if call["url"].endswith("/v1/ingest"))
    assert summary["body"]["updated_at"] == "2026-10-25T11:05:00Z"
    assert ingest["body"]["metadata"]["timestamp"] == "2026-10-25T11:00:00Z"


def test_payload_completions_aggregate_per_generation(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)

    def stop(generation, prompt, output, tokens=None):
        payload = {
            "hook_event_name": "stop",
            "conversation_id": "conv-gen",
            "generation_id": generation,
            "model": "cursor-grok",
            "prompt": prompt,
            "output": output,
        }
        if tokens is not None:
            payload["input_tokens"] = tokens[0]
            payload["output_tokens"] = tokens[1]
        return payload

    bridge.process_hook(stop("g1", "one", "uno", (80, 8)), config, poster, runtime="cursor")
    bridge.process_hook(stop("g2", "two", "dos", (20, 2)), config, poster, runtime="cursor")
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert summaries[-1]["input_tokens"] == 100
    assert summaries[-1]["output_tokens"] == 10
    assert summaries[-1]["turns"] == 2
    bridge.process_hook(stop("g3", "three", "tres"), config, poster, runtime="cursor")
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert summaries[-1]["input_tokens"] == 100
    assert summaries[-1]["output_tokens"] == 10
    bridge.process_hook(stop("g2", "two", "dos", (20, 2)), config, poster, runtime="cursor")
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert summaries[-1]["input_tokens"] == 100


def test_transcript_totals_win_over_payload_counters(tmp_path):
    path = write_jsonl(tmp_path / "sess.jsonl", [
        {"role": "user", "content": "prompt", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "answer", "model": "m",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
    ])
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess",
        "generation_id": "g1",
        "transcript_path": str(path),
        "input_tokens": 999,
        "output_tokens": 999,
        "output": "ignored",
    }, config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 10
    assert summary["output_tokens"] == 4
    poster.calls.clear()
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess",
        "generation_id": "g1",
        "output": "retry without the transcript",
        "input_tokens": 999,
        "output_tokens": 999,
    }, config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 10
    assert summary["output_tokens"] == 4
    poster.calls.clear()
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess",
        "generation_id": "g2",
        "output": "later",
        "input_tokens": 5,
        "output_tokens": 1,
    }, config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 15
    assert summary["output_tokens"] == 5


def test_codex_function_call_records_attach_tool_results():
    records = [
        _codex_event("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "list the files"},
        ]}),
        _codex_event("response_item", {
            "type": "function_call",
            "name": "shell",
            "call_id": "call_1",
            "arguments": "{\"command\":\"ls\"}",
        }, 1),
        _codex_event("response_item", {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "README.md",
        }, 2),
        _codex_event("response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "listed"},
        ]}, 3),
    ]
    turns = bridge.extract_turns(records)
    assert len(turns) == 1
    assert turns[0].tools[0]["id"] == "call_1"
    assert turns[0].tools[0]["name"] == "shell"
    assert turns[0].tools[0]["input"] == {"command": "ls"}
    assert turns[0].tools[0]["result"] == "README.md"
    text = bridge.format_turn_markdown("codex", "gpt-test", turns[0])
    assert "shell" in text
    assert "README.md" in text
    assert "(none)" not in text


def test_payload_generation_after_transcript_allocates_past_the_high_water(tmp_path):
    path = write_jsonl(tmp_path / "sess.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "uno", "model": "m",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
        {"role": "user", "content": "two", "ts": "2026-10-25T11:01:00Z"},
        {
            "role": "assistant", "content": "dos", "model": "m",
            "ts": "2026-10-25T11:01:01Z",
            "usage": {"input_tokens": 6, "output_tokens": 2},
        },
    ])
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess-hw",
        "generation_id": "g1",
        "transcript_path": str(path),
        "input_tokens": 999,
        "output_tokens": 999,
        "output": "ignored",
    }, config, poster, runtime="cursor")
    summaries = [call for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert summaries[-1]["body"]["turns"] == 2
    assert summaries[-1]["body"]["input_tokens"] == 16
    marked = len(summaries)
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess-hw",
        "generation_id": "g2",
        "prompt": "three",
        "output": "tres",
        "input_tokens": 5,
        "output_tokens": 1,
    }, config, poster, runtime="cursor")
    summaries = [call for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert len(summaries) == marked + 1
    assert summaries[-1]["body"]["turns"] == 3
    assert summaries[-1]["body"]["input_tokens"] == 21
    assert summaries[-1]["body"]["output_tokens"] == 7
    ingest = [call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert ingest[0].endswith(":turn-2")
    assert ingest[-1].endswith(":turn-3")


def _advancing_clock():
    stamps = []

    def clock():
        stamp = f"2026-10-25T11:00:{len(stamps):02d}Z"
        stamps.append(stamp)
        return stamp

    return stamps, clock


def test_payload_retry_keeps_the_ingest_hash_and_started_at(tmp_path, monkeypatch):
    stamps, clock = _advancing_clock()
    monkeypatch.setattr(bridge, "utc_now", clock)
    poster = FakePoster()
    config = cfg(tmp_path)
    payload = {
        "hook_event_name": "stop",
        "conversation_id": "conv-clock",
        "generation_id": "g1",
        "model": "cursor-grok",
        "prompt": "same prompt",
        "output": "same answer",
        "input_tokens": 4,
        "output_tokens": 2,
    }
    bridge.process_hook(payload, config, poster, runtime="cursor")
    bridge.process_hook(dict(payload), config, poster, runtime="cursor")
    ingests = [call for call in poster.calls if call["url"].endswith("/v1/ingest")]
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    assert len(ingests) == 1
    assert len(summaries) == 2
    assert summaries[0]["started_at"] == summaries[1]["started_at"]
    assert summaries[0]["updated_at"] != summaries[1]["updated_at"]
    assert ingests[0]["body"]["metadata"]["timestamp"] == summaries[0]["started_at"]
    assert stamps


def test_prompt_timestamp_is_reused_when_the_stop_arrives(tmp_path, monkeypatch):
    stamps, clock = _advancing_clock()
    monkeypatch.setattr(bridge, "utc_now", clock)
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-seen",
        "generation_id": "g1",
        "model": "cursor-grok",
        "prompt": "first seen",
    }, config, poster, runtime="cursor")
    first = stamps[0]
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "conv-seen",
        "generation_id": "g1",
        "model": "cursor-grok",
        "output": "answered",
        "input_tokens": 3,
        "output_tokens": 1,
    }, config, poster, runtime="cursor")
    ingest = next(call for call in poster.calls if call["url"].endswith("/v1/ingest"))
    summary = next(call for call in poster.calls if call["url"].endswith("/api/ai-sessions"))
    assert ingest["body"]["metadata"]["timestamp"] == first
    assert summary["body"]["started_at"] == first
    assert summary["body"]["updated_at"] != first


def test_cursor_payload_cache_is_removed_after_generation_dedup(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)

    def stop(generation, incoming, read=0, write=0, output_tokens=1):
        return {
            "hook_event_name": "stop",
            "conversation_id": "conv-cache",
            "generation_id": generation,
            "model": "cursor-grok",
            "prompt": generation,
            "output": "done",
            "input_tokens": incoming,
            "output_tokens": output_tokens,
            "cache_read_tokens": read,
            "cache_write_tokens": write,
        }

    bridge.process_hook(stop("g1", 1000, 0, 0), config, poster, runtime="cursor")
    bridge.process_hook(stop("g1", 1000, 400, 50), config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 550
    assert summary["cache_read_tokens"] == 400
    assert summary["cache_write_tokens"] == 50
    bridge.process_hook(stop("g2", 100), config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 650
    bridge.process_hook(stop("g3", 10, 40, 0), config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 660
    assert summary["cache_read_tokens"] == 440
    claude = FakePoster()
    bridge.process_hook({
        "hook_event_name": "Stop",
        "session_id": "claude-cache",
        "generation_id": "g1",
        "prompt": "exclusive",
        "output": "kept",
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_tokens": 4,
    }, cfg(tmp_path / "claude"), claude, runtime="claude")
    summary = [call["body"] for call in claude.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 10
    assert summary["cache_read_tokens"] == 4


def test_sync_sidechain_uses_the_hook_identity(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "parent",
        "generation_id": "g-parent",
        "model": "parent-model",
        "prompt": "parent prompt",
        "output": "parent answer",
        "input_tokens": 100,
        "output_tokens": 10,
    }, config, poster, runtime="cursor")
    sidechain = write_jsonl(tmp_path / "parent" / "subagents" / "agent-abc.jsonl", [
        {"type": "session_meta", "id": "parent", "timestamp": "2026-10-25T11:00:00Z"},
        {"role": "user", "content": "child question", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "child answer", "model": "child-model",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    ])
    bridge.sync_tree(sidechain, config, poster=poster, vendor="codex")
    ingest_ids = [call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert ingest_ids.count("parent:turn-1") == 1
    assert "parent:agent-abc:turn-1" in ingest_ids
    assert "agent-abc:turn-1" not in ingest_ids
    summaries = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    parent_rows = [body for body in summaries if body["session_id"] == "parent"]
    assert parent_rows
    assert all(body["input_tokens"] == 100 for body in parent_rows)
    child_rows = [body for body in summaries if body["session_id"] == "parent:agent-abc"]
    assert child_rows
    assert child_rows[-1]["input_tokens"] == 3
    assert all(body["session_id"] != "agent-abc" for body in summaries)
    marked = len(ingest_ids)
    bridge.sync_tree(sidechain, config, poster=poster, vendor="codex")
    ingest_ids = [call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert len(ingest_ids) == marked
    bridge.process_hook({
        "hook_event_name": "SubagentStop",
        "session_id": "parent",
        "transcript_path": str(sidechain),
    }, config, poster, runtime="codex")
    ingest_ids = [call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert "parent:agent-abc:turn-1" in ingest_ids
    assert ingest_ids.count("parent:turn-1") == 1


def _heartbeats(poster):
    return [call for call in poster.calls if call["url"].endswith("/session/heartbeat")]


def test_completion_drops_a_spooled_prompt_snapshot_of_the_same_turn(tmp_path):
    poster = FakePoster(fail_substrings=["coord.test", "twin.test"])
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-stage",
        "generation_id": "g1",
        "model": "m",
        "prompt": "question",
    }, config, poster, runtime="cursor")
    poster.calls.clear()
    poster.fail_substrings = []
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "conv-stage",
        "generation_id": "g1",
        "model": "m",
        "output": "answer",
        "input_tokens": 4,
        "output_tokens": 2,
    }, config, poster, runtime="cursor")
    live = bridge.Bridge(config, poster=poster, store=bridge.Store(config.state_dir))
    try:
        live.flush(50)
    finally:
        live.close()
    heartbeats = _heartbeats(poster)
    assert heartbeats
    assert all(call["body"]["last_output"] == "answer" for call in heartbeats)
    assert all(call["body"]["last_prompt"] == "question" for call in heartbeats)
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "conv-stage",
        "generation_id": "g2",
        "model": "m",
        "prompt": "next",
        "output": "done",
        "input_tokens": 3,
        "output_tokens": 1,
    }, config, poster, runtime="cursor")
    heartbeats = _heartbeats(poster)
    assert heartbeats[-1]["body"]["last_output"] == "done"
    ingest_ids = {call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")}
    assert ingest_ids == {"conv-stage:turn-1", "conv-stage:turn-2"}


def test_flush_replays_the_completion_not_the_prompt_when_both_were_offline(tmp_path):
    poster = FakePoster(fail_substrings=["coord.test", "twin.test"])
    config = cfg(tmp_path)
    prompt = {
        "hook_event_name": "beforeSubmitPrompt",
        "conversation_id": "conv-both",
        "generation_id": "g1",
        "model": "m",
        "prompt": "question",
    }
    stop = {
        "hook_event_name": "stop",
        "conversation_id": "conv-both",
        "generation_id": "g1",
        "model": "m",
        "output": "answer",
        "input_tokens": 4,
        "output_tokens": 2,
    }
    bridge.process_hook(prompt, config, poster, runtime="cursor")
    bridge.process_hook(stop, config, poster, runtime="cursor")
    poster.calls.clear()
    poster.fail_substrings = []
    live = bridge.Bridge(config, poster=poster, store=bridge.Store(config.state_dir))
    try:
        assert live.flush(50) >= 1
    finally:
        live.close()
    heartbeats = _heartbeats(poster)
    assert len(heartbeats) == 1
    assert heartbeats[0]["body"]["last_output"] == "answer"
    ingest_ids = [call["body"]["source_id"] for call in poster.calls if call["url"].endswith("/v1/ingest")]
    assert ingest_ids == ["conv-both:turn-1"]


def test_retrying_an_earlier_transcript_does_not_absorb_a_later_generation(tmp_path):
    full = write_jsonl(tmp_path / "full.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "uno", "model": "m",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
        {"role": "user", "content": "two", "ts": "2026-10-25T11:01:00Z"},
        {
            "role": "assistant", "content": "dos", "model": "m",
            "ts": "2026-10-25T11:01:01Z",
            "usage": {"input_tokens": 6, "output_tokens": 2},
        },
    ])
    short = write_jsonl(tmp_path / "short.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "uno", "model": "m",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
    ])
    poster = FakePoster()
    config = cfg(tmp_path)

    def stop(generation, **extra):
        payload = {
            "hook_event_name": "stop",
            "conversation_id": "sess-cover",
            "generation_id": generation,
            "model": "m",
        }
        payload.update(extra)
        return payload

    bridge.process_hook(stop("g1", transcript_path=str(full), input_tokens=999, output_tokens=999),
                        config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 16
    bridge.process_hook(stop("g2", output="later", input_tokens=5, output_tokens=1),
                        config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 21
    bridge.process_hook(stop("g1", transcript_path=str(full), input_tokens=999, output_tokens=999),
                        config, poster, runtime="cursor")
    bridge.process_hook(stop("g1", transcript_path=str(short), input_tokens=999, output_tokens=999),
                        config, poster, runtime="cursor")
    bridge.process_hook(stop("g3", output="after", input_tokens=1, output_tokens=1),
                        config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 22
    assert summary["output_tokens"] == 8


def test_payload_after_sync_adds_to_the_transcript_baseline(tmp_path):
    path = write_jsonl(tmp_path / "sess-sync.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "uno", "model": "m",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
    ])
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.sync_tree(path, config, poster=poster, vendor="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 10
    assert summary["session_id"] == "sess-sync"
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess-sync",
        "generation_id": "g2",
        "model": "m",
        "output": "later",
        "input_tokens": 5,
        "output_tokens": 1,
    }, config, poster, runtime="cursor")
    summary = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")][-1]
    assert summary["input_tokens"] == 15
    assert summary["output_tokens"] == 5
    assert summary["turns"] == 2


def test_flush_limit_does_not_let_a_disabled_target_starve_coord(tmp_path):
    config = cfg(tmp_path, twin_url="")
    store = bridge.Store(config.state_dir)
    try:
        for index in range(3):
            assert store.enqueue(
                f"a-twin-{index}", "ingest", f"hash-twin-{index}", "twin", "/v1/ingest",
                {"n": index}, "offline", session_id="s", turn_index=index,
            )
        assert store.enqueue(
            "z-coord", "register", "hash-coord", "coord", "/session/register",
            {"session_id": "s", "ok": True}, "offline", session_id="s",
        )
    finally:
        store.close()
    poster = FakePoster()
    live = bridge.Bridge(config, poster=poster, store=bridge.Store(config.state_dir))
    try:
        sent = live.flush(2)
    finally:
        live.close()
    assert sent == 1
    assert [call["url"] for call in poster.calls] == ["http://coord.test:8140/session/register"]
    assert poster.calls[0]["body"]["ok"] is True


def _summaries(poster, session_id=None):
    rows = [call["body"] for call in poster.calls if call["url"].endswith("/api/ai-sessions")]
    if session_id is None:
        return rows
    return [body for body in rows if body["session_id"] == session_id]


def test_trailing_prompt_does_not_drop_usage_the_transcript_never_saw(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)

    def stop(generation, prompt, output, incoming, outgoing):
        return {
            "hook_event_name": "stop",
            "conversation_id": "sess-cover",
            "generation_id": generation,
            "model": "m",
            "prompt": prompt,
            "output": output,
            "input_tokens": incoming,
            "output_tokens": outgoing,
        }

    bridge.process_hook(stop("g1", "one", "uno", 10, 4), config, poster, runtime="claude")
    bridge.process_hook(stop("g2", "two", "dos", 5, 2), config, poster, runtime="claude")
    assert _summaries(poster)[-1]["input_tokens"] == 15
    write_jsonl(tmp_path / "sess-cover.jsonl", [
        {"role": "user", "content": "one", "ts": "2026-10-25T11:00:00Z"},
        {
            "role": "assistant", "content": "uno", "model": "m",
            "ts": "2026-10-25T11:00:01Z",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
        {"role": "user", "content": "two", "ts": "2026-10-25T11:01:00Z"},
    ])
    bridge.sync_tree(tmp_path / "sess-cover.jsonl", config, poster=poster, vendor="claude")
    summary = _summaries(poster, "sess-cover")[-1]
    assert summary["input_tokens"] == 15
    assert summary["output_tokens"] == 6
    store = bridge.Store(config.state_dir)
    try:
        flags = dict(store.db.execute(
            "SELECT generation_id, in_transcript FROM generation_usage WHERE session_id=?",
            ("sess-cover",),
        ).fetchall())
        assert flags["g1"] == 1
        assert flags["g2"] == 0
        assert store.combined_usage("sess-cover")["input_tokens"] == 15
    finally:
        store.close()
    bridge.process_hook(stop("g2", "two", "dos", 5, 2), config, poster, runtime="claude")
    summary = _summaries(poster, "sess-cover")[-1]
    assert summary["input_tokens"] == 15
    assert summary["output_tokens"] == 6


def test_turn_allocation_sees_a_concurrent_high_water_update(tmp_path, monkeypatch):
    config = cfg(tmp_path)
    bridge.Store(config.state_dir).close()
    path = config.state_dir / "bridge.sqlite"
    holder_in = threading.Event()
    release = threading.Event()
    alloc_ready = threading.Event()
    result = {}
    seen = {}
    original = bridge.Store.high_water

    def wrapped(self, session_id, agent_key=""):
        seen["in_transaction"] = self.db.in_transaction
        value = original(self, session_id, agent_key)
        seen["value"] = value
        return value

    monkeypatch.setattr(bridge.Store, "high_water", wrapped)

    def hold():
        db = sqlite3.connect(path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO turn_high_water (session_id, agent_key, high_water, started_at) "
                "VALUES ('sess-race', '', 5, '')"
            )
            holder_in.set()
            if not release.wait(3):
                result["error"] = "holder timed out"
                db.rollback()
                return
            db.commit()
        finally:
            db.close()

    def allocate():
        local = bridge.Store(config.state_dir)
        alloc_ready.set()
        try:
            if not holder_in.wait(3):
                result["error"] = "allocation missed the lock"
                return
            index, _stamp = local.ensure_turn(
                "sess-race", "", "g-new", "2026-10-25T11:00:00Z")
            result["index"] = index
        finally:
            local.close()

    allocator = threading.Thread(target=allocate)
    holder = threading.Thread(target=hold)
    allocator.start()
    assert alloc_ready.wait(3)
    holder.start()
    assert holder_in.wait(3)
    threading.Event().wait(0.2)
    release.set()
    allocator.join(5)
    holder.join(5)
    assert result.get("error") is None
    assert seen["in_transaction"] is True
    assert seen["value"] == 5
    assert result["index"] == 6


def test_concurrent_allocations_choose_distinct_indexes(tmp_path):
    config = cfg(tmp_path)
    bridge.Store(config.state_dir).close()
    barrier = threading.Barrier(8)
    found = []
    errors = []
    lock = threading.Lock()

    def allocate(number):
        local = bridge.Store(config.state_dir)
        try:
            barrier.wait(5)
            index, _stamp = local.ensure_turn("sess-many", "", f"g{number}", "t")
        except (sqlite3.Error, OSError, threading.BrokenBarrierError) as exc:
            errors.append(repr(exc))
            return
        finally:
            local.close()
        with lock:
            found.append(index)

    threads = [threading.Thread(target=allocate, args=(number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert sorted(found) == list(range(1, 9))

    same = []

    def allocate_same():
        local = bridge.Store(config.state_dir)
        try:
            barrier.wait(5)
            index, _stamp = local.ensure_turn("sess-same", "", "g-shared", "t")
        except (sqlite3.Error, OSError, threading.BrokenBarrierError) as exc:
            errors.append(repr(exc))
            return
        finally:
            local.close()
        with lock:
            same.append(index)

    barrier = threading.Barrier(6)
    threads = [threading.Thread(target=allocate_same) for _number in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert same == [1, 1, 1, 1, 1, 1]


def test_foreign_codex_usage_records_do_not_inflate_or_disable_local_totals(tmp_path):
    local = _codex_total()
    foreign_usage = {"input_tokens": 900, "cached_input_tokens": 0, "output_tokens": 90}
    cumulative = [
        _codex_event("session_meta", {"id": "thread-1", "cwd": "/tmp/project"}),
        _codex_event("turn_context", {"model": "gpt-test", "turn_id": "turn-1"}),
        _codex_event("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "check the change"},
        ]}),
        _codex_event("token_usage_record", {
            "thread_id": "thread-other", "response_id": "foreign", "usage": foreign_usage,
        }, 1),
        _codex_event("event_msg", {"type": "token_count", "info": {"total_token_usage": local}}, 1),
        _codex_event("response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "checked"},
        ]}, 2),
    ]
    assert bridge._codex_usage_mode(cumulative) == "cumulative"
    total = bridge.sum_usage(bridge.extract_turns(cumulative))
    assert total["input_tokens"] == 60
    assert total["output_tokens"] == 20
    mixed = [
        _codex_event("session_meta", {"id": "thread-1"}),
        _codex_event("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "local record"},
        ]}),
        _codex_event("token_usage_record", {
            "thread_id": "thread-1", "response_id": "local", "usage": local,
        }, 1),
        _codex_event("token_usage_record", {
            "thread_id": "thread-other", "response_id": "foreign", "usage": foreign_usage,
        }, 1),
        _codex_event("event_msg", {"type": "token_count", "info": {
            "total_token_usage": _codex_total(400, 0, 80),
        }}, 2),
        _codex_event("response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "done"},
        ]}, 2),
    ]
    assert bridge._codex_usage_mode(mixed) == "records"
    total = bridge.sum_usage(bridge.extract_turns(mixed))
    assert total["input_tokens"] == 60
    assert total["output_tokens"] == 20
    path = write_jsonl(tmp_path / "thread-1.jsonl", cumulative)
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.sync_tree(path, config, poster=poster, vendor="codex")
    summary = _summaries(poster, "thread-1")[-1]
    assert summary["input_tokens"] == 60
    assert summary["output_tokens"] == 20
    store = bridge.Store(config.state_dir)
    try:
        stored = store.combined_usage("thread-1")
    finally:
        store.close()
    assert stored["input_tokens"] == 60
    assert stored["output_tokens"] == 20


def test_sync_without_counters_preserves_hooked_totals(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    bridge.process_hook({
        "hook_event_name": "stop",
        "conversation_id": "sess-keep",
        "generation_id": "g1",
        "model": "m",
        "prompt": "question",
        "output": "answer",
        "input_tokens": 80,
        "output_tokens": 8,
    }, config, poster, runtime="cursor")
    path = write_jsonl(tmp_path / "sess-keep.jsonl", [
        {"role": "user", "content": "question", "ts": "2026-10-25T11:00:00Z"},
        {"role": "assistant", "content": "answer", "model": "m", "ts": "2026-10-25T11:00:01Z"},
    ])
    bridge.sync_tree(path, config, poster=poster, vendor="cursor")
    summaries = _summaries(poster, "sess-keep")
    assert summaries
    assert all(body["input_tokens"] == 80 and body["output_tokens"] == 8 for body in summaries)
    assert summaries[-1]["turns"] == 1
