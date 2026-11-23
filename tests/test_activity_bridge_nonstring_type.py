"""Non-string JSON types in transcripts must not abort activity-bridge sync.

A JSON array or object is unhashable. Membership tests against sets of type
names raise TypeError. These tests are the spec for that bug: they fail while
the process dies on the planted record, and they pass once only strings are
treated as record and block types and one transcript cannot end a sync.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRIDGE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "activity_bridge.py"
_spec = importlib.util.spec_from_file_location("activity_bridge_nonstring_under_test", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


class FakePoster:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body, token, timeout):
        self.calls.append({
            "url": url,
            "body": body,
            "token": token,
            "timeout": timeout,
        })
        return 200


def cfg(tmp_path, **kwargs):
    values = {
        "coord_url": "http://coord.test:8140",
        "coord_token": "test-coord-token",
        "twin_url": "http://twin.test:8100",
        "twin_token": "test-twin-token",
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


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(rec) + "\n" for rec in records),
        encoding="utf-8",
    )
    return path


def _ingest_text(calls):
    parts = []
    for call in calls:
        url = call["url"] if isinstance(call, dict) else ""
        body = call["body"] if isinstance(call, dict) else call
        if isinstance(call, dict) and not str(url).endswith("/v1/ingest"):
            continue
        if isinstance(body, dict):
            parts.append(str(body.get("content") or ""))
    return "\n".join(parts)


def _turn(prompt, reply, prompt_stamp="2026-10-25T12:00:00Z", reply_stamp="2026-10-25T12:00:01Z"):
    return [
        {
            "type": "user",
            "timestamp": prompt_stamp,
            "message": {"role": "user", "content": prompt},
        },
        {
            "type": "assistant",
            "timestamp": reply_stamp,
            "message": {"role": "assistant", "content": reply, "model": "grok-4"},
        },
    ]


def _planted_tree(root):
    """Files sort a-to-z. A crash on an earlier file skips every later file."""
    write_jsonl(root / "a-array.jsonl", [
        {"type": ["user"], "message": {"role": "user", "content": "array-type"}},
        *_turn("after-array-prompt", "after-array-reply"),
    ])
    write_jsonl(root / "b-object.jsonl", [
        {"type": {"kind": "assistant"}, "message": {"role": "assistant", "content": "object-type"}},
        *_turn("after-object-prompt", "after-object-reply"),
    ])
    write_jsonl(root / "c-blocks.jsonl", [
        {
            "type": "user",
            "timestamp": "2026-10-25T12:10:00Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": ["tool_result"], "tool_use_id": "bad", "content": "not-a-result"},
                    {"type": {"name": "tool_result"}, "content": "also-not"},
                    {"type": "text", "text": "prompt-kept"},
                ],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-25T12:10:01Z",
            "message": {
                "role": "assistant",
                "model": "grok-4",
                "content": [
                    {"type": [], "content": "nested-array-type"},
                    {"type": {"t": "text"}, "text": "object-block"},
                    {"type": [], "name": "Bad", "input": {"path": "a"}},
                    {
                        "type": "tool_use",
                        "id": "t-real",
                        "name": "Read",
                        "input": {"path": "real.py"},
                    },
                    {"type": "text", "text": "block-kept"},
                ],
            },
        },
    ])
    write_jsonl(root / "d-payload.jsonl", [
        {
            "type": "response_item",
            "payload": {
                "type": ["function_call"],
                "name": "Bad",
                "call_id": "c1",
                "arguments": {},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": {"name": "function_call_output"},
                "call_id": "c1",
                "output": "nope",
            },
        },
        *_turn("after-payload-prompt", "after-payload-reply"),
    ])
    write_jsonl(root / "e-codex-norm.jsonl", [
        {"_codex_norm": True, "type": ["assistant"]},
        {"_codex_norm": True, "type": {"kind": "tool"}},
        *_turn("after-codex-norm-prompt", "after-codex-norm-reply"),
    ])
    write_jsonl(root / "z-later.jsonl", _turn("later-prompt", "later-answer"))
    return root


_REQUIRED_TEXT = (
    "after-array-reply",
    "after-object-reply",
    "prompt-kept",
    "block-kept",
    "after-payload-reply",
    "after-codex-norm-reply",
    "later-answer",
)


def test_normalize_record_accepts_only_string_types():
    array = {"type": ["user"], "message": {"role": "user", "content": "kept"}}
    assert bridge._normalize_record(array)["message"]["content"] == "kept"

    obj = {"type": {"kind": "user"}, "message": {"role": "user", "content": "kept"}}
    normalized = bridge._normalize_record(obj)
    assert normalized["message"]["content"] == "kept"

    payload_array = {
        "type": "response_item",
        "payload": {"type": ["function_call"], "name": "Bad", "call_id": "c", "arguments": {}},
    }
    assert isinstance(bridge._normalize_record(payload_array), dict)

    payload_object = {
        "type": "response_item",
        "payload": {"type": {"name": "function_call_output"}, "call_id": "c", "output": "x"},
    }
    assert isinstance(bridge._normalize_record(payload_object), dict)

    shaped = bridge._normalize_record({
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "Read",
            "call_id": "c",
            "arguments": {"path": "real.py"},
        },
    })
    assert shaped["role"] == "assistant"
    assert shaped["tool_calls"][0]["name"] == "Read"


def test_role_ignores_non_string_type_and_keeps_message_role():
    assert bridge._role({"type": ["tool"]}) == ""
    assert bridge._role({"type": {"kind": "assistant"}}) == ""
    assert bridge._role({"_codex_norm": True, "type": ["user"]}) == ""
    assert bridge._role({
        "type": ["tool_result"],
        "message": {"role": "User"},
    }) == "user"
    assert bridge._role({"type": "assistant", "message": {"role": "assistant"}}) == "assistant"


def test_text_from_content_skips_unhashable_block_types():
    text = bridge._text_from_content([
        {"type": [], "content": "nested-array-type"},
        {"type": {"t": "text"}, "text": "object-block"},
        {"type": "text", "text": "yes"},
    ])
    assert "yes" in text

    assert "plain" in bridge._text_from_content([
        {"type": ["tool_use"], "text": "array-block"},
        "plain",
    ])


def test_tool_calls_keep_string_typed_call_beside_unhashable_types():
    calls = bridge._tool_calls({}, [
        {"type": {}},
        {"type": [], "name": "Bad", "input": {"path": "a"}},
        {"type": {"name": "function"}, "name": "AlsoBad", "arguments": {"q": "x"}},
        {"type": "tool_use", "id": "t-real", "name": "Read", "input": {"path": "real.py"}},
    ])
    assert any(call["name"] == "Read" and call["id"] == "t-real" for call in calls)


def test_tool_results_and_tool_only_ignore_non_string_types():
    results = bridge._tool_results([
        {"type": ["tool_result"], "tool_use_id": "bad", "content": "not-a-result"},
        {"type": {"name": "tool_result_error"}, "tool_use_id": "bad2", "content": "also-not"},
        {"type": "tool_result", "tool_use_id": "t-real", "content": "ok"},
    ])
    assert [item["id"] for item in results] == ["t-real"]
    assert results[0]["text"] == "ok"

    assert bridge._tool_only([{"type": ["tool_result"]}]) is False
    assert bridge._tool_only([{"type": {"name": "tool_result_error"}}]) is False
    assert bridge._tool_only([
        {"type": "tool_result", "content": "ok"},
        {"type": [], "content": "no"},
    ]) is False
    assert bridge._tool_only([{"type": "tool_result", "content": "ok"}]) is True


def test_observe_identity_ignores_non_string_record_type(tmp_path):
    path = tmp_path / "session.jsonl"
    state = {"session_id": path.stem, "model": "", "started": "", "updated": "", "cwd": ""}
    bridge._observe_identity(path, state, {"type": ["session_meta"], "id": "from-array"})
    bridge._observe_identity(path, state, {"type": {"kind": "session"}, "id": "from-object"})
    assert state["session_id"] == path.stem
    bridge._observe_identity(path, state, {"type": "session_meta", "id": "from-string"})
    assert state["session_id"] == "from-string"


def test_extract_turns_keeps_string_typed_text_around_non_string_types():
    turns = bridge.extract_turns([
        {"type": ["user"], "message": {"role": "user", "content": "array-type"}},
        {"type": {"kind": "assistant"}},
        {"_codex_norm": True, "type": ["tool"]},
        {
            "type": "response_item",
            "payload": {"type": ["function_call"], "name": "Bad", "call_id": "c", "arguments": {}},
        },
        {
            "type": "response_item",
            "payload": {"type": {"name": "function_call_output"}, "call_id": "c", "output": "x"},
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": ["tool_result"], "content": "not-a-result"},
                    {"type": {"name": "text"}, "text": "object-block"},
                    {"type": "text", "text": "prompt-kept"},
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": [], "name": "Bad", "input": {"path": "a"}},
                    {"type": "tool_use", "id": "t-real", "name": "Read", "input": {"path": "real.py"}},
                    {"type": "text", "text": "block-kept"},
                ],
            },
        },
    ])
    matched = [
        turn for turn in turns
        if "prompt-kept" in turn.prompt and "block-kept" in turn.assistant
    ]
    assert len(matched) == 1
    assert any(call["name"] == "Read" for call in matched[0].tools)


def test_sync_tree_publishes_later_files_and_later_turns(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    root = _planted_tree(tmp_path / "transcripts")
    delivered, spooled = bridge.sync_tree(root, config, poster=poster, vendor="cursor")
    assert spooled == 0
    assert delivered > 0
    text = _ingest_text(poster.calls)
    for marker in _REQUIRED_TEXT:
        assert marker in text
    assert "Read" in text
    assert "real.py" in text


def test_sync_command_continues_across_directories_and_watch(tmp_path, monkeypatch):
    planted = _planted_tree(tmp_path / "first")
    second = tmp_path / "second"
    write_jsonl(second / "only-later.jsonl", _turn("dir-two-prompt", "dir-two-answer"))
    posts = []

    def fake_post(url, body, token, timeout):
        posts.append({"url": url, "body": body, "token": token, "timeout": timeout})
        return 200

    monkeypatch.setattr(bridge, "post_json", fake_post)
    config = cfg(tmp_path, sync_dirs=[str(planted), str(second)])
    parser = bridge.build_parser()
    one_shot = parser.parse_args([
        "sync",
        "--sync-dir", str(planted),
        "--sync-dir", str(second),
        "--vendor", "cursor",
        "--interval", "1",
    ])
    assert bridge.cmd_sync(one_shot, config) == 0
    text = _ingest_text(posts)
    for marker in _REQUIRED_TEXT:
        assert marker in text
    assert "dir-two-answer" in text

    watched = []

    def watch_post(url, body, token, timeout):
        watched.append({"url": url, "body": body})
        return 200

    def stop_after_pass(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(bridge, "post_json", watch_post)
    monkeypatch.setattr(bridge.time, "sleep", stop_after_pass)
    config = cfg(tmp_path / "watch-state", sync_dirs=[str(planted)])
    watch = parser.parse_args([
        "sync",
        "--watch",
        "--interval", "15",
        "--sync-dir", str(planted),
        "--vendor", "cursor",
    ])
    assert bridge.cmd_sync(watch, config) == 0
    watched_text = _ingest_text(watched)
    assert "later-answer" in watched_text
    assert "after-array-reply" in watched_text


def test_stop_hook_still_ingests_turn_after_non_string_record(tmp_path):
    poster = FakePoster()
    config = cfg(tmp_path)
    path = tmp_path / "hook.jsonl"
    write_jsonl(path, [
        {"type": ["assistant"], "message": {"role": "assistant", "content": "array-type"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": {"t": "text"}, "text": "object-block"},
                    {"type": "text", "text": "hook-answer"},
                ],
            },
        },
    ])
    code = bridge.process_hook({
        "hook_event_name": "stop",
        "session_id": "hook-session",
        "transcript_path": str(path),
    }, config, poster, runtime="cursor")
    assert code == 0
    assert "hook-answer" in _ingest_text(poster.calls)


def test_sync_process_posts_file_after_planted_transcript(tmp_path):
    root = tmp_path / "cloud-agent-transcripts"
    write_jsonl(root / "a-planted.jsonl", [
        {"type": ["user"], "message": {"role": "user", "content": "array-type"}},
        {"type": {"kind": "assistant"}, "message": {"role": "assistant", "content": [
            {"type": [], "text": "array-block"},
        ]}},
    ])
    write_jsonl(root / "z-later.jsonl", _turn("later-prompt", "later-answer"))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            seen.append({
                "path": self.path,
                "body": json.loads(raw.decode("utf-8")),
            })
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("WIZZO_"):
            env.pop(name)
    env.update({
        "WIZZO_COORD_URL": f"http://127.0.0.1:{port}",
        "WIZZO_COORD_TOKEN": "test-coord-token",
        "WIZZO_TWIN_API_URL": f"http://127.0.0.1:{port}",
        "WIZZO_TWIN_TOKEN": "test-twin-token",
        "WIZZO_ACTIVITY_STATE_DIR": str(tmp_path / "state"),
        "WIZZO_ACTIVITY_HTTP_TIMEOUT": "2",
    })
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(BRIDGE_PATH),
                "sync",
                "--sync-dir", str(root),
                "--vendor", "cursor",
                "--surface", "cloud_agent",
                "--interval", "1",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stderr
    ingested = "\n".join(
        item["body"].get("content", "")
        for item in seen
        if item["path"] == "/v1/ingest"
    )
    assert "later-answer" in ingested
    assert "TypeError" not in result.stderr

