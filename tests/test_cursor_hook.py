"""Cursor hook ledger: fail-open CLI, JSONL append, and adapter parse."""
import json
import os
import stat
import subprocess
import sys

from conftest import SCRIPT


def run_cursor_hook(payload, tmp_path, extra_env=None):
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "ledger")}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "cursor-hook"],
        input=json.dumps(payload) if payload is not None else payload,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def ledger_path(tmp_path, conversation_id):
    safe = "".join(c for c in str(conversation_id) if c.isalnum() or c in "_-") or "unknown"
    return tmp_path / "ledger" / "cursor" / f"{safe}.jsonl"


def read_ledger_lines(path):
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        lines.append(json.loads(line))
    return lines


def base_payload(**kwargs):
    data = {
        "conversation_id": "conv-abc",
        "generation_id": "gen-001",
        "hook_event_name": "beforeSubmitPrompt",
        "model": "claude-sonnet-4",
        "workspace_roots": ["/tmp/project"],
    }
    data.update(kwargs)
    return data


def test_cursor_hook_command_exists_red(tmp_path):
    r = run_cursor_hook(base_payload(prompt="hello"), tmp_path)
    assert r.returncode == 0


def test_before_submit_writes_truncated_prompt(tmp_path):
    long_prompt = "x" * 200
    r = run_cursor_hook(
        base_payload(prompt=long_prompt, hook_event_name="beforeSubmitPrompt"),
        tmp_path,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    lines = read_ledger_lines(ledger_path(tmp_path, "conv-abc"))
    assert len(lines) == 1
    assert lines[0]["hook"] == "beforeSubmitPrompt"
    assert lines[0]["generation_id"] == "gen-001"
    assert len(lines[0]["prompt"]) == 120


def test_stop_writes_optional_token_fields(tmp_path):
    r = run_cursor_hook(
        base_payload(
            hook_event_name="stop",
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=400,
            cache_write_tokens=50,
        ),
        tmp_path,
    )
    assert r.returncode == 0
    lines = read_ledger_lines(ledger_path(tmp_path, "conv-abc"))
    assert lines[0]["hook"] == "stop"
    assert lines[0]["usage"]["output"] == 200
    assert lines[0]["usage"]["cache_read"] == 400
    assert lines[0]["usage"]["cache_5m"] == 50
    # input_tokens includes cache; uncached input is normalized
    assert lines[0]["usage"]["input"] == 550


def test_stop_missing_token_fields_still_records(tmp_path):
    r = run_cursor_hook(
        base_payload(hook_event_name="stop", status="completed"),
        tmp_path,
    )
    assert r.returncode == 0
    lines = read_ledger_lines(ledger_path(tmp_path, "conv-abc"))
    assert "usage" not in lines[0]
    assert lines[0]["model"] == "claude-sonnet-4"


def test_unsafe_conversation_id_sanitized(tmp_path):
    r = run_cursor_hook(
        base_payload(
            conversation_id='../evil/id',
            prompt="hi",
            hook_event_name="beforeSubmitPrompt",
        ),
        tmp_path,
    )
    assert r.returncode == 0
    assert ledger_path(tmp_path, "../evil/id").is_file()
    assert not (tmp_path / "ledger" / "cursor" / ".." / "evil").exists()


def test_malformed_stdin_exits_zero(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "cursor-hook"],
        input="not-json",
        capture_output=True,
        text=True,
        env={**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "ledger")},
        check=False,
    )
    assert r.returncode == 0
    assert list((tmp_path / "ledger" / "cursor").glob("*.jsonl")) == []


def test_unwritable_ledger_exits_zero(tmp_path):
    ledger_root = tmp_path / "ledger" / "cursor"
    ledger_root.mkdir(parents=True)
    os.chmod(ledger_root, stat.S_IRUSR | stat.S_IXUSR)
    r = run_cursor_hook(
        base_payload(prompt="x", hook_event_name="beforeSubmitPrompt"),
        tmp_path,
    )
    assert r.returncode == 0
    assert list(ledger_root.glob("*.jsonl")) == []


def test_duplicate_completion_usage_deduped_on_parse(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-dedupe"
    for hook, extra in (
        ("stop", {"input_tokens": 100, "output_tokens": 10}),
        ("afterAgentResponse", {"input_tokens": 100, "output_tokens": 10}),
    ):
        run_cursor_hook(
            base_payload(
                conversation_id=conv,
                generation_id="gen-dup",
                hook_event_name=hook,
                **extra,
            ),
            tmp_path,
        )
    path = ledger_path(tmp_path, conv)
    adapter = tu.get_runtime_adapter("cursor")
    source = tu.CursorSession(conv, "hook_ledger", ledger_path=path)
    result = adapter.parse(source)
    assert result["measurement"] == "exact"
    bucket = result["segments"][0]["by_model"]["claude-sonnet-4"]
    assert bucket["input"] == 100
    assert bucket["output"] == 10
    assert bucket["requests"] == 1


def test_prompt_and_stop_joined_into_segment(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-join"
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-join",
            hook_event_name="beforeSubmitPrompt",
            prompt="  Refactor the parser  ",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-join",
            hook_event_name="stop",
            output_tokens=42,
        ),
        tmp_path,
    )
    path = ledger_path(tmp_path, conv)
    adapter = tu.get_runtime_adapter("cursor")
    result = adapter.parse(tu.CursorSession(conv, "hook_ledger", ledger_path=path))
    assert len(result["segments"]) == 1
    seg = result["segments"][0]
    assert seg["prompt"] == "Refactor the parser"
    assert seg["label"] == "Refactor the parser"
    assert seg["by_model"]["claude-sonnet-4"]["output"] == 42


def test_subagent_events_recorded(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-sub"
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-parent",
            hook_event_name="beforeSubmitPrompt",
            prompt="parent turn",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-parent",
            hook_event_name="subagentStart",
            subagent_id="sub-1",
            subagent_type="explore",
            task="scan auth",
            subagent_model="claude-sonnet-4",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-parent",
            hook_event_name="subagentStop",
            subagent_id="sub-1",
            subagent_type="explore",
            output_tokens=99,
        ),
        tmp_path,
    )
    path = ledger_path(tmp_path, conv)
    lines = read_ledger_lines(path)
    hooks = [ln["hook"] for ln in lines]
    assert hooks == [
        "beforeSubmitPrompt",
        "subagentStart",
        "subagentStop",
    ]
    adapter = tu.get_runtime_adapter("cursor")
    result = adapter.parse(tu.CursorSession(conv, "hook_ledger", ledger_path=path))
    assert len(result["segments"][0]["subagents"]) == 1
    assert result["segments"][0]["subagents"][0]["type"] == "explore"
    assert result["segments"][0]["subagents"][0]["by_model"]["claude-sonnet-4"]["output"] == 99
