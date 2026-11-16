"""Deep JSON on the stdio boundaries must be a parse failure, not a dead process.

`json.loads` raises RecursionError, not ValueError, once nesting reaches
CPython's C recursion budget (Modules/_json.c calls _Py_EnterRecursiveCall;
on this non-debug build that budget is C_RECURSION_LIMIT, 10000). serve()
and the activity-bridge stdin/JSONL readers only catch ValueError, and the
MCP entry point only swallows KeyboardInterrupt and BrokenPipeError.

These tests fail while that gap is open. They pass when a line that
json.loads cannot decode because of RecursionError is reported or skipped
the same way malformed JSON already is, and the next message still runs.

Not covered here:
- MemoryError from a huge line. Forcing it would exhaust the test process.
- token_usage.iter_jsonl and the other ValueError-only loads in
  scripts/token_usage.py. Through the MCP tools they run inside
  call_tool's Exception handler, so a nested transcript line becomes a
  tool error and the read loop stays up. The CLI has no such handler.
- plugin_version() reads the local manifest, not a client line.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SERVER

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "activity_bridge.py"


def _nested(kind, depth):
    if kind == "array":
        return "[" * depth + "0" + "]" * depth
    if kind == "object":
        return '{"k":' * depth + "0" + "}" * depth
    raise AssertionError(kind)


def _loads_raises(kind, depth):
    """True when a fresh interpreter cannot decode this nesting.

    Measured out of process. A RecursionError inside this process can leave
    the C decoder's recursion budget short, so a depth that fails here may
    still parse in the server process the test is about to start.
    """
    script = (
        "import json,sys\n"
        "kind,depth=sys.argv[1],int(sys.argv[2])\n"
        "if kind=='array':\n"
        "    text='['*depth+'0'+']'*depth\n"
        "else:\n"
        "    text='{\"k\":'*depth+'0'+'}'*depth\n"
        "try:\n"
        "    json.loads(text)\n"
        "except RecursionError:\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, kind, str(depth)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(proc.stderr.decode("utf-8", "replace"))
    return proc.returncode == 0


def _killing_depth(kind):
    """Smallest nesting depth at which a fresh interpreter's json.loads raises.

    Built by repeating brackets. That is the byte string a stdio client
    writes; it does not depend on a recursive JSON.stringify of a live object.
    """
    cached = _killing_depth.cache
    if kind in cached:
        return cached[kind]
    lo, hi = 1, 2
    while not _loads_raises(kind, hi):
        lo = hi
        hi *= 2
        if hi > 1_000_000:
            pytest.fail(f"json.loads accepted {kind} nesting past {lo}")
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if _loads_raises(kind, mid):
            hi = mid
        else:
            lo = mid
    cached[kind] = hi
    return hi


_killing_depth.cache = {}


def _rpc_tools_call(kind, depth, id_=1):
    # Nesting sits in a tool argument, which is where a host places model output.
    nested = _nested(kind, depth)
    return (
        '{"jsonrpc":"2.0","id":' + str(id_) + ',"method":"tools/call","params":'
        '{"name":"session_cost","arguments":{"transcript":' + nested + "}}}"
    )


def _replies(stdout):
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def test_killing_depth_is_recursion_error_not_value_error():
    for kind in ("array", "object"):
        depth = _killing_depth(kind)
        with pytest.raises(RecursionError):
            json.loads(_nested(kind, depth))
        assert not issubclass(RecursionError, ValueError)


def test_serve_turns_deep_json_into_parse_error_and_reads_on(mcp):
    # A bare nested value and a tools/call that carries one both die in
    # json.loads, before handle_message. Each must become -32700, and a
    # later ping must still be answered.
    lines = []
    for kind in ("array", "object"):
        depth = _killing_depth(kind)
        lines.append(_nested(kind, depth))
        lines.append(_rpc_tools_call(kind, depth, id_=1))
    lines.append('{"jsonrpc":"2.0","id":2,"method":"ping"}')
    stdout = io.StringIO()
    mcp.serve(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=stdout)
    replies = _replies(stdout.getvalue())
    assert len(replies) == 5
    for reply in replies[:4]:
        assert reply["id"] is None
        assert reply["error"]["code"] == -32700
        assert reply["error"]["message"] == "parse error"
    assert replies[4] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_server_process_survives_a_deeply_nested_tools_call(tmp_path):
    # The real entry point, not serve() alone. RecursionError leaves serve()
    # and is not one of the two exceptions __main__ catches, so the process
    # exits and the host's next tool call has nobody to answer it.
    depth = _killing_depth("array")
    payload = "\n".join([
        '{"jsonrpc":"2.0","id":7,"method":"ping"}',
        _rpc_tools_call("array", depth, id_=8),
        _rpc_tools_call("object", _killing_depth("object"), id_=9),
        '{"jsonrpc":"2.0","id":10,"method":"ping"}',
    ]) + "\n"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TOKEN_USAGE_PROJECTS_DIR": str(tmp_path / "projects"),
        "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "ledger"),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload.encode(),
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    replies = _replies(proc.stdout.decode())
    assert [m.get("id") for m in replies] == [7, None, None, 10]
    assert replies[1]["error"]["code"] == -32700
    assert replies[2]["error"]["code"] == -32700
    assert replies[3]["result"] == {}


def _load_bridge():
    import importlib.util

    spec = importlib.util.spec_from_file_location("activity_bridge_nested", BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_activity_bridge_hook_survives_deeply_nested_stdin(tmp_path):
    # hooks/hooks.json runs this command on Stop. _read_payload() catches
    # ValueError only, and cmd_hook calls it outside the Exception handler,
    # so the hook process dies instead of exiting 0.
    depth = _killing_depth("array")
    payload = (_nested("array", depth) + "\n").encode()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "hook", "--runtime", "claude", "--stdout", "claude"],
        input=payload,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in proc.stderr


def test_activity_bridge_record_readers_skip_deep_json(tmp_path):
    # sync walks transcript lines with the same ValueError-only loads.
    # A nested line, or a shallow line whose tool input is a nested JSON
    # string, must be skipped rather than ending the scan.
    bridge = _load_bridge()
    depth = _killing_depth("array")
    obj_depth = _killing_depth("object")
    path = tmp_path / "session.jsonl"
    path.write_text(
        _nested("array", depth) + "\n"
        + _nested("object", obj_depth) + "\n"
        + '{"ok": true}\n',
        encoding="utf-8",
    )
    records = list(bridge._iter_records(path))
    assert records == [{"ok": True}]
    # Tool arguments are often a JSON string. loads() of that string raises
    # RecursionError, which _coerce_input does not catch, so it must keep the
    # original string the way a ValueError already does.
    assert bridge._coerce_input(_nested("array", depth)) == _nested("array", depth)
    assert bridge._coerce_input(_nested("object", obj_depth)) == _nested("object", obj_depth)
