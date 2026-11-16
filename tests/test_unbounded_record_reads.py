"""Recordings the scanner opens must not be able to exhaust memory.

These tests are the spec for the missing caps. They fail while a JSONL line or a
ledger file is still read without a bound, and while MemoryError from one
recording still aborts the corpus command.

A line whose on-disk bytes, including the trailing newline, are
MAX_JSONL_LINE_BYTES must still be parsed. A longer line must not contribute
usage. A recording file of MAX_RECORDING_BYTES must still be parsed. A larger
file must not contribute usage that sits past that bound. Bytes already read
have to stay bounded: the process VmPeak of an over-cap recording must stay
within PEAK_SLACK_KB of the VmPeak of a recording that is exactly at the cap.
Catching MemoryError after the whole line is already in memory does not pass
that comparison.

Not covered here: CursorAdapter.locate and _cursor_parse_cloud_export load an
explicit Cloud Agent export with Path.read_text. That path runs only when the
caller passes the file; a planted scan-directory recording does not reach it.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import SCRIPT

MAX_JSONL_LINE_BYTES = 1 << 20
MAX_RECORDING_BYTES = 8 << 20
OVER_LINE_BYTES = MAX_JSONL_LINE_BYTES + 4096
OVER_FILE_BYTES = MAX_RECORDING_BYTES + (64 << 10)
PEAK_LINE_BYTES = 6 << 20
PEAK_FILE_BYTES = 16 << 20
PEAK_SLACK_KB = 6 << 10
SIBLING = 42
MARKER = 777_000_333
THIS_FILE = Path(__file__).resolve()


def sized_json(obj, size, newline=True):
    """One JSON value of exactly `size` bytes. ASCII padding only."""
    body = dict(obj)
    body["pad"] = ""
    raw = json.dumps(body, separators=(",", ":")).encode()
    if newline:
        raw += b"\n"
    missing = size - len(raw)
    if missing < 0:
        raise AssertionError(f"JSON is already {len(raw)} bytes")
    body["pad"] = "x" * missing
    raw = json.dumps(body, separators=(",", ":")).encode()
    if newline:
        raw += b"\n"
    if len(raw) != size:
        raise AssertionError(f"sized JSON is {len(raw)} bytes, wanted {size}")
    return raw


def dumps_line(obj):
    return json.dumps(obj, separators=(",", ":")).encode() + b"\n"


def claude_obj(output):
    return {
        "type": "assistant",
        "timestamp": "2026-11-01T00:00:00Z",
        "requestId": "r1",
        "message": {
            "role": "assistant",
            "model": "claude-fable-5",
            "content": "ok",
            "usage": {"input_tokens": 1, "output_tokens": output},
        },
    }


def cursor_obj(conv, gen, output):
    return {
        "hook": "stop",
        "generation_id": gen,
        "conversation_id": conv,
        "ts": "2026-11-01T00:00:00Z",
        "workspace_roots": ["/tmp/project-alpha"],
        "model": "gpt-test",
        "tokens": {
            "input_tokens": 1,
            "output_tokens": output,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
    }


def cloud_obj(agent, output):
    return {
        "type": "usage",
        "timestamp": "1794592800000",
        "cloudAgentId": agent,
        "conversationId": "conv",
        "model": "gpt-test",
        "tokenUsage": {
            "inputTokens": 1,
            "outputTokens": output,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
        },
        "chargedCents": 0,
    }


def codex_meta(sid):
    return {
        "type": "session_meta",
        "timestamp": "2026-11-01T00:00:00Z",
        "payload": {"id": sid, "cwd": "/tmp/project-alpha"},
    }


def codex_usage(sid, output):
    return {
        "type": "token_usage_record",
        "timestamp": "2026-11-01T00:00:01Z",
        "payload": {
            "response_id": sid + "-response",
            "turn_id": "turn-1",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "output_tokens": output,
                "reasoning_output_tokens": 0,
                "total_tokens": 10 + output,
            },
        },
    }


def gemini_message(sid, output):
    return {
        "sessionId": sid,
        "projectHash": "ab" * 32,
        "id": sid + "-a",
        "type": "gemini",
        "timestamp": "2026-11-01T00:00:00Z",
        "model": "gemini-test",
        "tokens": {"input": 1, "output": output, "cached": 0, "thoughts": 0, "tool": 0},
    }


def gemini_doc(sid, output):
    return {
        "sessionId": sid,
        "projectHash": "ab" * 32,
        "messages": [{
            "id": sid + "-a",
            "type": "gemini",
            "timestamp": "2026-11-01T00:00:00Z",
            "model": "gemini-test",
            "tokens": {"input": 1, "output": output, "cached": 0, "thoughts": 0, "tool": 0},
        }],
    }


def copilot_start(sid):
    return {
        "type": "session.start",
        "id": sid + "-start",
        "timestamp": "2026-11-01T00:00:00Z",
        "data": {"sessionId": sid, "context": {"cwd": "/tmp/project-alpha"}},
    }


def copilot_usage(sid, output):
    return {
        "type": "assistant.usage",
        "id": sid + "-usage",
        "timestamp": "2026-11-01T00:00:01Z",
        "data": {
            "model": "gpt-test",
            "inputTokens": 10,
            "outputTokens": output,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
        },
    }


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def filler_line(kind, n):
    if kind == "cursor":
        obj = cursor_obj("bomb-conv", f"pad-{n}", 0)
    elif kind == "cloud":
        obj = cloud_obj("bc-bombfile01", 0)
    else:
        raise AssertionError(kind)
    obj["pad"] = "z" * 1800
    raw = dumps_line(obj)
    if len(raw) > MAX_JSONL_LINE_BYTES:
        raise AssertionError("filler line exceeds the line cap")
    return raw


def exact_filler(kind, size):
    if kind == "cursor":
        obj = cursor_obj("bomb-conv", "pad-exact", 0)
    else:
        obj = cloud_obj("bc-bombfile01", 0)
    return sized_json(obj, size, newline=True)


def write_bounded_file(path, kind, marker, size):
    """File of exactly `size` bytes. `marker` is the final line and sits at the end."""
    chunks = []
    total = 0
    n = 0
    while True:
        nxt = filler_line(kind, n)
        if total + len(nxt) + len(marker) > size:
            break
        chunks.append(nxt)
        total += len(nxt)
        n += 1
    gap = size - total - len(marker)
    base_obj = (cursor_obj("bomb-conv", "pad-exact", 0) if kind == "cursor"
                else cloud_obj("bc-bombfile01", 0))
    min_line = len(json.dumps(dict(base_obj, pad=""), separators=(",", ":")).encode() + b"\n")
    while gap < min_line and chunks:
        total -= len(chunks.pop())
        gap = size - total - len(marker)
    if gap > MAX_JSONL_LINE_BYTES or gap < min_line:
        raise AssertionError(f"cannot pad file to {size} (gap {gap})")
    chunks.append(exact_filler(kind, gap))
    chunks.append(marker)
    data = b"".join(chunks)
    if len(data) != size:
        raise AssertionError(f"file is {len(data)} bytes, wanted {size}")
    return write_bytes(path, data)


def history_output(data):
    return sum(row["usage"]["output"] for row in data["rows"])


def bind_runtime(monkeypatch, root, runtime):
    ledger = root / "ledger"
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(ledger))
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(root / "projects"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(root / "cursor-user"))
    monkeypatch.setenv("TOKEN_USAGE_CODEX_HOME", str(root / "codex"))
    monkeypatch.setenv("TOKEN_USAGE_GEMINI_HOME", str(root / "gemini"))
    monkeypatch.setenv("TOKEN_USAGE_COPILOT_HOME", str(root / "copilot"))
    monkeypatch.delenv("CURSOR_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("TOKEN_USAGE_RUNTIME", raising=False)
    return ledger


def claude_sibling(root):
    return write_bytes(
        root / "projects" / "proj" / "z-ok.jsonl", dumps_line(claude_obj(SIBLING)))


def claude_bomb_line(root, size, output):
    return write_bytes(
        root / "projects" / "proj" / "a-bomb.jsonl",
        sized_json(claude_obj(output), size, newline=True))


def cursor_sibling(root):
    return write_bytes(
        root / "ledger" / "cursor" / "sib.jsonl",
        dumps_line(cursor_obj("sib-conv", "g-sib", SIBLING)))


def cursor_bomb_line(root, size, output):
    return write_bytes(
        root / "ledger" / "cursor" / "bomb.jsonl",
        sized_json(cursor_obj("bomb-conv", "g-bomb", output), size, newline=True))


def cloud_sibling(root):
    return write_bytes(
        root / "ledger" / "cursor-cloud" / "bc-sibling01.jsonl",
        dumps_line(cloud_obj("bc-sibling01", SIBLING)))


def cloud_bomb_line(root, size, output):
    return write_bytes(
        root / "ledger" / "cursor-cloud" / "bc-bombfile01.jsonl",
        sized_json(cloud_obj("bc-bombfile01", output), size, newline=True))


def codex_file(root, sid, usage_line):
    meta = dumps_line(codex_meta(sid))
    return write_bytes(
        root / "codex" / "sessions" / "2026" / "11" / "01" / f"rollout-{sid}.jsonl",
        meta + usage_line)


def codex_sibling(root):
    return codex_file(root, "sib", dumps_line(codex_usage("sib", SIBLING)))


def gemini_jsonl(root, sid, message_line):
    return write_bytes(
        root / "gemini" / "tmp" / "p" / "chats" / f"{sid}.jsonl", message_line)


def gemini_sibling(root):
    return gemini_jsonl(root, "sib", dumps_line(gemini_message("sib", SIBLING)))


def gemini_json_sibling(root):
    return write_bytes(
        root / "gemini" / "tmp" / "p" / "chats" / "sib.json",
        dumps_line(gemini_doc("sib", SIBLING)).rstrip(b"\n"))


def copilot_file(root, sid, extra_lines):
    data = dumps_line(copilot_start(sid)) + b"".join(extra_lines)
    return write_bytes(root / "copilot" / "session-state" / sid / "events.jsonl", data)


def copilot_sibling(root):
    return copilot_file(root, "sib", [dumps_line(copilot_usage("sib", SIBLING))])


def scan(tu, runtime):
    return history_output(tu.run_history(by="project", runtime=runtime))


def test_line_at_cap_is_parsed_for_every_jsonl_reader(tu, tmp_path, monkeypatch):
    """A line of exactly the cap is ordinary input and its usage counts."""
    root = tmp_path / "at-cap"
    bind_runtime(monkeypatch, root, "claude")
    claude_bomb_line(root, MAX_JSONL_LINE_BYTES, SIBLING)
    assert scan(tu, "claude") == SIBLING
    cursor_bomb_line(root, MAX_JSONL_LINE_BYTES, SIBLING)
    assert scan(tu, "cursor") == SIBLING
    cloud_bomb_line(root, MAX_JSONL_LINE_BYTES, SIBLING)
    assert scan(tu, "cursor-cloud") == SIBLING
    usage = sized_json(codex_usage("atcap", SIBLING), MAX_JSONL_LINE_BYTES)
    codex_file(root, "atcap", usage)
    assert scan(tu, "codex") == SIBLING
    gemini_jsonl(root, "atcap", sized_json(gemini_message("atcap", SIBLING), MAX_JSONL_LINE_BYTES))
    assert scan(tu, "gemini") == SIBLING
    usage = sized_json(copilot_usage("atcap", SIBLING), MAX_JSONL_LINE_BYTES)
    copilot_file(root, "atcap", [usage])
    assert scan(tu, "copilot") == SIBLING


def test_oversize_jsonl_line_does_not_count_and_sibling_does(tu, mcp, tmp_path, monkeypatch):
    root = tmp_path / "over-line"
    bind_runtime(monkeypatch, root, "claude")
    sibling = claude_sibling(root)
    expected = scan(tu, "claude")
    assert expected == SIBLING
    bomb = claude_bomb_line(root, OVER_LINE_BYTES, MARKER)
    assert scan(tu, "claude") == expected
    dash = tu.dashboard_data(runtime="claude")
    assert dash["summary"]["usage"]["output"] == expected
    exported, counts = tu.history_export_data(by="project", runtime="claude")
    records = tu.history_export_records(exported, measurement_counts=counts)
    total = next(row for row in records if row["key"] == "total")
    assert total["metrics"]["gen_ai.usage.output_tokens"] == expected
    insights = tu.run_insights(since="2020-01-01", runtime="claude")
    assert insights["baseline"]["sessions"] == 1
    top = tu.run_top_consumers(since="2020-01-01", runtime="claude")
    assert history_output(top) == expected
    hist = json.loads(mcp.tool_history({"by": "project", "runtime": "claude"}))
    assert history_output(hist) == expected
    ins = json.loads(mcp.tool_insights({"since": "2020-01-01", "runtime": "claude"}))
    assert ins["baseline"]["sessions"] == 1
    tops = json.loads(mcp.tool_top_consumers({"since": "2020-01-01", "runtime": "claude"}))
    assert history_output(tops) == expected
    try:
        cost = json.loads(mcp.tool_session_cost({"transcript": str(bomb), "runtime": "claude"}))
    except mcp.ToolError:
        cost = None
    if cost is not None:
        assert cost["total"]["usage"]["output"] == 0
    diff = json.loads(mcp.tool_diff({
        "old": str(sibling), "new": str(bomb), "runtime": "claude", "format": "json"}))
    assert diff["b_total"]["usage"]["output"] == 0
    assert diff["a_total"]["usage"]["output"] == expected

    cursor_sibling(root)
    cursor_expected = scan(tu, "cursor")
    assert cursor_expected == SIBLING
    cursor_bomb_line(root, OVER_LINE_BYTES, MARKER)
    assert scan(tu, "cursor") == cursor_expected
    dash = tu.dashboard_data(runtime="cursor")
    assert dash["summary"]["usage"]["output"] == cursor_expected
    insights = tu.run_insights(since="2020-01-01", runtime="cursor")
    assert insights["baseline"]["sessions"] == 1
    hist = json.loads(mcp.tool_history({"by": "project", "runtime": "cursor"}))
    assert history_output(hist) == cursor_expected

    cloud_sibling(root)
    cloud_expected = scan(tu, "cursor-cloud")
    assert cloud_expected == SIBLING
    cloud_bomb_line(root, OVER_LINE_BYTES, MARKER)
    assert scan(tu, "cursor-cloud") == cloud_expected

    codex_sibling(root)
    codex_expected = scan(tu, "codex")
    assert codex_expected > 0
    codex_file(root, "bomb", sized_json(codex_usage("bomb", MARKER), OVER_LINE_BYTES))
    assert scan(tu, "codex") == codex_expected

    gemini_sibling(root)
    gemini_expected = scan(tu, "gemini")
    assert gemini_expected > 0
    gemini_jsonl(root, "bomb", sized_json(gemini_message("bomb", MARKER), OVER_LINE_BYTES))
    assert scan(tu, "gemini") == gemini_expected

    copilot_sibling(root)
    copilot_expected = scan(tu, "copilot")
    assert copilot_expected > 0
    copilot_file(root, "bomb", [sized_json(copilot_usage("bomb", MARKER), OVER_LINE_BYTES)])
    assert scan(tu, "copilot") == copilot_expected


def test_recording_file_at_cap_is_parsed(tu, tmp_path, monkeypatch):
    root = tmp_path / "file-cap"
    bind_runtime(monkeypatch, root, "cursor")
    marker = dumps_line(cursor_obj("bomb-conv", "g-marker", SIBLING))
    write_bounded_file(
        root / "ledger" / "cursor" / "bomb.jsonl", "cursor", marker, MAX_RECORDING_BYTES)
    assert scan(tu, "cursor") == SIBLING
    cloud_marker = dumps_line(cloud_obj("bc-bombfile01", SIBLING))
    write_bounded_file(
        root / "ledger" / "cursor-cloud" / "bc-bombfile01.jsonl",
        "cloud", cloud_marker, MAX_RECORDING_BYTES)
    assert scan(tu, "cursor-cloud") == SIBLING
    doc = sized_json(gemini_doc("atcap", SIBLING), MAX_RECORDING_BYTES, newline=False)
    write_bytes(root / "gemini" / "tmp" / "p" / "chats" / "atcap.json", doc)
    assert scan(tu, "gemini") == SIBLING


def test_recording_past_file_cap_does_not_contribute_usage(tu, tmp_path, monkeypatch):
    root = tmp_path / "file-over"
    bind_runtime(monkeypatch, root, "cursor")
    cursor_sibling(root)
    assert scan(tu, "cursor") == SIBLING
    marker = dumps_line(cursor_obj("bomb-conv", "g-marker", MARKER))
    write_bounded_file(
        root / "ledger" / "cursor" / "bomb.jsonl", "cursor", marker, OVER_FILE_BYTES)
    assert scan(tu, "cursor") == SIBLING

    cloud_sibling(root)
    assert scan(tu, "cursor-cloud") == SIBLING
    cloud_marker = dumps_line(cloud_obj("bc-bombfile01", MARKER))
    write_bounded_file(
        root / "ledger" / "cursor-cloud" / "bc-bombfile01.jsonl",
        "cloud", cloud_marker, OVER_FILE_BYTES)
    assert scan(tu, "cursor-cloud") == SIBLING

    gemini_json_sibling(root)
    expected = scan(tu, "gemini")
    assert expected > 0
    doc = sized_json(gemini_doc("bomb", MARKER), OVER_FILE_BYTES, newline=False)
    write_bytes(root / "gemini" / "tmp" / "p" / "chats" / "bomb.json", doc)
    assert scan(tu, "gemini") == expected


def test_stop_hook_parses_a_line_at_the_cap_and_drops_a_longer_one(tmp_path):
    at_cap = tmp_path / "at.jsonl"
    at_cap.write_bytes(sized_json(claude_obj(SIBLING), MAX_JSONL_LINE_BYTES))
    kept = _run_hook(at_cap, tmp_path / "ledger-at", "at-cap")
    assert kept["total"]["usage"]["output"] == SIBLING

    over = tmp_path / "over.jsonl"
    over.write_bytes(sized_json(claude_obj(MARKER), OVER_LINE_BYTES))
    dropped = _run_hook(over, tmp_path / "ledger-over", "over-cap")
    assert dropped["total"]["usage"]["output"] == 0


def _run_hook(transcript, ledger, session_id):
    env = os.environ.copy()
    env["TOKEN_USAGE_LEDGER_DIR"] = str(ledger)
    env.pop("TOKEN_USAGE_BUDGET_USD", None)
    payload = json.dumps({"session_id": session_id, "transcript_path": str(transcript)})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "hook"],
        input=payload, capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads((ledger / f"{session_id}.json").read_text(encoding="utf-8"))


def _load_bridge():
    import importlib.util
    path = SCRIPT.parent / "activity_bridge.py"
    spec = importlib.util.spec_from_file_location("activity_bridge_unbounded", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_activity_bridge_keeps_a_capped_line_and_drops_a_longer_one(tmp_path):
    bridge = _load_bridge()
    at_cap = tmp_path / "at.jsonl"
    at_cap.write_bytes(sized_json(claude_obj(SIBLING), MAX_JSONL_LINE_BYTES))
    kept = bridge._stream_transcript(at_cap)[1]["output_tokens"]
    assert kept == SIBLING
    over = tmp_path / "over.jsonl"
    over.write_bytes(sized_json(claude_obj(MARKER), OVER_LINE_BYTES))
    assert bridge._stream_transcript(over)[1]["output_tokens"] == 0

    cfg = bridge.Config(
        coord_url="http://coord.test:8140", coord_token="coord-token",
        twin_url="http://twin.test:8100", twin_token="twin-token",
        enabled=True, state_dir=tmp_path / "state", timeout=0.4, machine="test",
        repo="", branch="", surface="", vendor="", sync_dirs=[])
    posted = []

    def poster(url, body, token, timeout):
        posted.append(body)
        return 200

    assert bridge.process_hook(
        {"hook_event_name": "Stop", "session_id": "over", "transcript_path": str(over),
         "cwd": "/tmp/project-alpha"},
        cfg, poster=poster, runtime="claude") == 0
    outputs = [body.get("output_tokens") for body in posted
               if isinstance(body, dict) and "output_tokens" in body]
    assert MARKER not in outputs


def _run_child(mode, root):
    proc = subprocess.run(
        [sys.executable, str(THIS_FILE), mode, str(root)],
        capture_output=True, text=True, check=False)
    return proc


def _peak_kb(mode, root):
    proc = _run_child(mode, root)
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(item for item in proc.stdout.splitlines() if item.startswith("PEAK "))
    return int(line.split()[1])


def _assert_peak(mode_at_cap, mode_over, tmp_path):
    at_cap = _peak_kb(mode_at_cap, tmp_path / "at")
    over = _peak_kb(mode_over, tmp_path / "over")
    assert over - at_cap <= PEAK_SLACK_KB, (over, at_cap, over - at_cap)


def test_oversize_line_does_not_grow_address_space(tmp_path):
    _assert_peak("peak-claude-line-cap", "peak-claude-line-over", tmp_path)
    _assert_peak("peak-cursor-line-cap", "peak-cursor-line-over", tmp_path)
    _assert_peak("peak-hook-line-cap", "peak-hook-line-over", tmp_path)
    _assert_peak("peak-bridge-line-cap", "peak-bridge-line-over", tmp_path)


def test_oversize_recording_file_does_not_grow_address_space(tmp_path):
    _assert_peak("peak-cursor-file-cap", "peak-cursor-file-over", tmp_path)
    _assert_peak("peak-cloud-file-cap", "peak-cloud-file-over", tmp_path)
    _assert_peak("peak-gemini-file-cap", "peak-gemini-file-over", tmp_path)


# --- child process ---------------------------------------------------------

def _bind_child(root):
    os.environ["TOKEN_USAGE_LEDGER_DIR"] = str(root / "ledger")
    os.environ["TOKEN_USAGE_PROJECTS_DIR"] = str(root / "projects")
    os.environ["TOKEN_USAGE_CURSOR_DIR"] = str(root / "cursor-user")
    os.environ["TOKEN_USAGE_CODEX_HOME"] = str(root / "codex")
    os.environ["TOKEN_USAGE_GEMINI_HOME"] = str(root / "gemini")
    os.environ["TOKEN_USAGE_COPILOT_HOME"] = str(root / "copilot")
    os.environ.pop("CURSOR_ADMIN_API_KEY", None)
    os.environ.pop("CURSOR_API_KEY", None)
    os.environ.pop("TOKEN_USAGE_RUNTIME", None)
    os.environ.pop("TOKEN_USAGE_BUDGET_USD", None)


def _import_tu():
    sys.path.insert(0, str(SCRIPT.parent))
    import token_usage as tu
    return tu


def _print_peak():
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VmPeak:"):
                print("PEAK", line.split()[1])
                return
    raise SystemExit("VmPeak missing")


def _peak_scan(root, runtime, build):
    _bind_child(root)
    tu = _import_tu()
    build(root)
    tu.run_history(by="project", runtime=runtime)
    _print_peak()


def _hook_peak(root, size):
    _bind_child(root)
    tu = _import_tu()
    path = write_bytes(root / "t.jsonl", sized_json(claude_obj(SIBLING), size))
    payload = json.dumps({"session_id": "peak", "transcript_path": str(path)}).encode()

    class _Stdin:
        def __init__(self, data):
            self.buffer = io.BytesIO(data)

    sys.stdin = _Stdin(payload)
    tu.run_hook()
    _print_peak()


def _bridge_peak(root, size):
    bridge = _load_bridge()
    path = write_bytes(root / "t.jsonl", sized_json(claude_obj(SIBLING), size))
    cfg = bridge.Config(
        coord_url="http://127.0.0.1:9", coord_token="", twin_url="", twin_token="",
        enabled=True, state_dir=str(root / "state"), timeout=1, machine="test",
        repo="", branch="", surface="", vendor="", sync_dirs=())

    def poster(url, body, token, timeout):
        return 200

    bridge.process_hook(
        {"hook_event_name": "Stop", "session_id": "peak", "transcript_path": str(path)},
        cfg, poster=poster, runtime="claude")
    _print_peak()


def _child(argv):
    mode, root = argv[1], Path(argv[2])
    root.mkdir(parents=True, exist_ok=True)
    if mode == "peak-claude-line-cap":
        _peak_scan(root, "claude", lambda p: claude_bomb_line(p, MAX_JSONL_LINE_BYTES, SIBLING))
    elif mode == "peak-claude-line-over":
        _peak_scan(root, "claude", lambda p: claude_bomb_line(p, PEAK_LINE_BYTES, MARKER))
    elif mode == "peak-cursor-line-cap":
        _peak_scan(root, "cursor", lambda p: cursor_bomb_line(p, MAX_JSONL_LINE_BYTES, SIBLING))
    elif mode == "peak-cursor-line-over":
        _peak_scan(root, "cursor", lambda p: cursor_bomb_line(p, PEAK_LINE_BYTES, MARKER))
    elif mode == "peak-cursor-file-cap":
        def build_cursor_cap(p):
            marker = dumps_line(cursor_obj("bomb-conv", "g-marker", SIBLING))
            write_bounded_file(p / "ledger" / "cursor" / "bomb.jsonl", "cursor", marker,
                               MAX_RECORDING_BYTES)
        _peak_scan(root, "cursor", build_cursor_cap)
    elif mode == "peak-cursor-file-over":
        def build_cursor_over(p):
            marker = dumps_line(cursor_obj("bomb-conv", "g-marker", MARKER))
            write_bounded_file(p / "ledger" / "cursor" / "bomb.jsonl", "cursor", marker,
                               PEAK_FILE_BYTES)
        _peak_scan(root, "cursor", build_cursor_over)
    elif mode == "peak-cloud-file-cap":
        def build_cloud_cap(p):
            marker = dumps_line(cloud_obj("bc-bombfile01", SIBLING))
            write_bounded_file(
                p / "ledger" / "cursor-cloud" / "bc-bombfile01.jsonl",
                "cloud", marker, MAX_RECORDING_BYTES)
        _peak_scan(root, "cursor-cloud", build_cloud_cap)
    elif mode == "peak-cloud-file-over":
        def build_cloud_over(p):
            marker = dumps_line(cloud_obj("bc-bombfile01", MARKER))
            write_bounded_file(
                p / "ledger" / "cursor-cloud" / "bc-bombfile01.jsonl",
                "cloud", marker, PEAK_FILE_BYTES)
        _peak_scan(root, "cursor-cloud", build_cloud_over)
    elif mode == "peak-gemini-file-cap":
        def build_gem_cap(p):
            doc = sized_json(gemini_doc("atcap", SIBLING), MAX_RECORDING_BYTES, newline=False)
            write_bytes(p / "gemini" / "tmp" / "p" / "chats" / "atcap.json", doc)
        _peak_scan(root, "gemini", build_gem_cap)
    elif mode == "peak-gemini-file-over":
        def build_gem_over(p):
            doc = sized_json(gemini_doc("bomb", MARKER), PEAK_FILE_BYTES, newline=False)
            write_bytes(p / "gemini" / "tmp" / "p" / "chats" / "bomb.json", doc)
        _peak_scan(root, "gemini", build_gem_over)
    elif mode == "peak-hook-line-cap":
        _hook_peak(root, MAX_JSONL_LINE_BYTES)
    elif mode == "peak-hook-line-over":
        _hook_peak(root, PEAK_LINE_BYTES)
    elif mode == "peak-bridge-line-cap":
        _bridge_peak(root, MAX_JSONL_LINE_BYTES)
    elif mode == "peak-bridge-line-over":
        _bridge_peak(root, PEAK_LINE_BYTES)
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    _child(sys.argv)
