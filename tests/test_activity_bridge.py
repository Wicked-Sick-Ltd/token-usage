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
    assert summary["body"]["input_tokens"] == 100
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
