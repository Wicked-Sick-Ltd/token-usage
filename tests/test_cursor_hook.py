"""Cursor hook ledger: fail-open CLI, JSONL append, and adapter parse."""
import json
import os
import re
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


def ledger_path(tmp_path, conversation_id, tu=None):
    if tu is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("token_usage", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        basename = mod._cursor_ledger_filename(conversation_id)
    else:
        basename = tu._cursor_ledger_filename(conversation_id)
    return tmp_path / "ledger" / "cursor" / f"{basename}.jsonl"


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
    assert json.loads(r.stdout) == {}
    lines = read_ledger_lines(ledger_path(tmp_path, "conv-abc"))
    assert len(lines) == 1
    assert lines[0]["hook"] == "beforeSubmitPrompt"
    assert lines[0]["generation_id"] == "gen-001"
    assert len(lines[0]["prompt"]) == 120


def test_stop_writes_raw_token_fields(tmp_path):
    # Capture is lossless: normalization (subtracting cache from a cumulative
    # input) happens at parse time, where a nonsensical combination can still
    # be disclosed instead of silently clamped into the ledger.
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
    assert lines[0]["tokens"] == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 400,
        "cache_write_tokens": 50,
    }
    assert "usage" not in lines[0]


def test_stop_missing_token_fields_still_records(tmp_path):
    r = run_cursor_hook(
        base_payload(hook_event_name="stop", status="completed"),
        tmp_path,
    )
    assert r.returncode == 0
    lines = read_ledger_lines(ledger_path(tmp_path, "conv-abc"))
    assert "usage" not in lines[0]
    assert "tokens" not in lines[0]
    assert lines[0]["model"] == "claude-sonnet-4"


def test_records_carry_utc_timestamp_and_raw_conversation_id(tmp_path):
    raw_id = "acme/foo"
    r = run_cursor_hook(
        base_payload(conversation_id=raw_id, prompt="hi",
                     hook_event_name="beforeSubmitPrompt"),
        tmp_path,
    )
    assert r.returncode == 0
    record = read_ledger_lines(ledger_path(tmp_path, raw_id))[0]
    # The filename is hashed for path safety, so the raw id has to travel
    # inside the record for dedupe against Cursor's own composer ids.
    assert record["conversation_id"] == raw_id
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record["ts"])


def test_records_carry_workspace_roots(tmp_path):
    r = run_cursor_hook(
        base_payload(prompt="hi", hook_event_name="beforeSubmitPrompt",
                     workspace_roots=["/tmp/project", 7, "  "]),
        tmp_path,
    )
    assert r.returncode == 0
    record = read_ledger_lines(ledger_path(tmp_path, "conv-abc"))[0]
    assert record["workspace_roots"] == ["/tmp/project"]


def test_ledger_directory_is_private(tmp_path):
    r = run_cursor_hook(
        base_payload(prompt="hi", hook_event_name="beforeSubmitPrompt"),
        tmp_path,
    )
    assert r.returncode == 0
    mode = stat.S_IMODE((tmp_path / "ledger" / "cursor").stat().st_mode)
    assert mode == 0o700


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
    assert json.loads(r.stdout) == {}
    path = ledger_path(tmp_path, "../evil/id")
    assert path.is_file()
    assert ".." not in path.name
    assert not (tmp_path / "ledger" / "cursor" / ".." / "evil").exists()


def test_conversation_ids_that_sanitize_same_stay_distinct(tmp_path):
    a, b = "acme/foo", "acme@foo"
    run_cursor_hook(
        base_payload(conversation_id=a, prompt="a", hook_event_name="beforeSubmitPrompt"),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(conversation_id=b, prompt="b", hook_event_name="beforeSubmitPrompt"),
        tmp_path,
    )
    path_a = ledger_path(tmp_path, a)
    path_b = ledger_path(tmp_path, b)
    assert path_a != path_b
    assert path_a.is_file() and path_b.is_file()
    assert read_ledger_lines(path_a)[0]["prompt"] == "a"
    assert read_ledger_lines(path_b)[0]["prompt"] == "b"


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
    assert json.loads(r.stdout) == {}
    assert list((tmp_path / "ledger" / "cursor").glob("*.jsonl")) == []


def test_cursor_hook_always_emits_empty_json_object(tmp_path):
    r = run_cursor_hook(base_payload(prompt="x"), tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {}


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


def test_completion_tokenless_then_token_bearing_uses_later(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-upgrade"
    for hook, extra in (
        ("stop", {"status": "completed"}),
        ("afterAgentResponse", {"input_tokens": 50, "output_tokens": 5}),
    ):
        run_cursor_hook(
            base_payload(
                conversation_id=conv,
                generation_id="gen-up",
                hook_event_name=hook,
                **extra,
            ),
            tmp_path,
        )
    path = ledger_path(tmp_path, conv)
    result = tu.get_runtime_adapter("cursor").parse(
        tu.CursorSession(conv, "hook_ledger", ledger_path=path)
    )
    bucket = result["segments"][0]["by_model"]["claude-sonnet-4"]
    assert bucket["input"] == 50
    assert bucket["output"] == 5
    assert bucket["requests"] == 1


def test_completion_token_bearing_then_tokenless_does_not_double(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-first-tokens"
    for hook, extra in (
        ("afterAgentResponse", {"input_tokens": 50, "output_tokens": 5}),
        ("stop", {"status": "completed"}),
    ):
        run_cursor_hook(
            base_payload(
                conversation_id=conv,
                generation_id="gen-ft",
                hook_event_name=hook,
                **extra,
            ),
            tmp_path,
        )
    path = ledger_path(tmp_path, conv)
    bucket = (
        tu.get_runtime_adapter("cursor")
        .parse(tu.CursorSession(conv, "hook_ledger", ledger_path=path))["segments"][0]
        ["by_model"]["claude-sonnet-4"]
    )
    assert bucket["input"] == 50
    assert bucket["output"] == 5
    assert bucket["requests"] == 1


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


def test_subagent_id_reused_across_generations(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-reuse-sub"
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-one",
            hook_event_name="beforeSubmitPrompt",
            prompt="turn one",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-one",
            hook_event_name="subagentStart",
            subagent_id="sub-1",
            subagent_type="explore",
            task="first task",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-one",
            hook_event_name="subagentStop",
            subagent_id="sub-1",
            output_tokens=11,
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-two",
            hook_event_name="beforeSubmitPrompt",
            prompt="turn two",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-two",
            hook_event_name="subagentStart",
            subagent_id="sub-1",
            subagent_type="shell",
            task="second task",
        ),
        tmp_path,
    )
    run_cursor_hook(
        base_payload(
            conversation_id=conv,
            generation_id="gen-two",
            hook_event_name="subagentStop",
            subagent_id="sub-1",
            output_tokens=22,
        ),
        tmp_path,
    )
    path = ledger_path(tmp_path, conv)
    segs = (
        tu.get_runtime_adapter("cursor")
        .parse(tu.CursorSession(conv, "hook_ledger", ledger_path=path))["segments"]
    )
    assert len(segs) == 2
    assert segs[0]["subagents"][0]["type"] == "explore"
    assert segs[0]["subagents"][0]["by_model"]["claude-sonnet-4"]["output"] == 11
    assert segs[1]["subagents"][0]["type"] == "shell"
    assert segs[1]["subagents"][0]["by_model"]["claude-sonnet-4"]["output"] == 22


def parse_ledger(tu, tmp_path, conversation_id):
    path = ledger_path(tmp_path, conversation_id, tu)
    return tu.get_runtime_adapter("cursor").parse(
        tu.CursorSession(conversation_id, "hook_ledger", ledger_path=path)
    )


def test_parse_subtracts_cache_from_cumulative_input(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-cumulative"
    run_cursor_hook(
        base_payload(conversation_id=conv, generation_id="gen-c",
                     hook_event_name="stop", input_tokens=1000, output_tokens=200,
                     cache_read_tokens=400, cache_write_tokens=50),
        tmp_path,
    )
    result = parse_ledger(tu, tmp_path, conv)
    assert result["measurement"] == "exact"
    bucket = result["segments"][0]["by_model"]["claude-sonnet-4"]
    assert bucket["input"] == 550
    assert bucket["cache_read"] == 400
    assert bucket["cache_5m"] == 50


def test_parse_preserves_input_when_cache_exceeds_it(tu, tmp_path, monkeypatch):
    # Subtracting here used to clamp a measured 300-token input to zero.
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-impossible"
    run_cursor_hook(
        base_payload(conversation_id=conv, generation_id="gen-i",
                     hook_event_name="stop", input_tokens=300, output_tokens=20,
                     cache_read_tokens=400, cache_write_tokens=50),
        tmp_path,
    )
    result = parse_ledger(tu, tmp_path, conv)
    assert result["measurement"] == "partial"
    bucket = result["segments"][0]["by_model"]["claude-sonnet-4"]
    assert bucket["input"] == 300
    assert any("gen-i" in w and "below" in w for w in result["warnings"]), result["warnings"]


def test_parse_reads_legacy_normalized_usage_records(tu, tmp_path, monkeypatch):
    # Ledgers written before capture stopped normalizing keep working.
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-legacy"
    path = ledger_path(tmp_path, conv, tu)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"hook": "beforeSubmitPrompt", "generation_id": "gen-l",
                    "prompt": "legacy turn"}) + "\n"
        + json.dumps({"hook": "stop", "generation_id": "gen-l",
                      "model": "claude-sonnet-4",
                      "usage": {"input": 550, "output": 200, "cache_read": 400,
                                "cache_5m": 50, "cache_1h": 0}}) + "\n",
        encoding="utf-8",
    )
    result = parse_ledger(tu, tmp_path, conv)
    assert result["measurement"] == "exact"
    bucket = result["segments"][0]["by_model"]["claude-sonnet-4"]
    assert bucket["input"] == 550
    assert bucket["cache_read"] == 400


def synthetic_ledger(tu, tmp_path, conversation_id, records):
    from test_cursor_adapter import write_ledger

    return write_ledger(tu, tmp_path / "ledger", conversation_id, records)


def test_segment_start_ts_is_the_generations_first_event(tu, tmp_path, monkeypatch):
    from test_cursor_adapter import ledger_record

    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-timed"
    path = synthetic_ledger(tu, tmp_path, conv, [
        ledger_record("beforeSubmitPrompt", conversation_id=conv, generation_id="g1",
                      ts="2026-06-12T10:00:00Z", prompt="first turn"),
        ledger_record("stop", conversation_id=conv, generation_id="g1",
                      ts="2026-06-12T10:00:30Z", model="claude-sonnet-4",
                      tokens={"output_tokens": 10}),
        ledger_record("beforeSubmitPrompt", conversation_id=conv, generation_id="g2",
                      ts="2026-06-12T11:00:00Z", prompt="second turn"),
        ledger_record("stop", conversation_id=conv, generation_id="g2",
                      ts="2026-06-12T11:00:20Z", model="claude-sonnet-4",
                      tokens={"output_tokens": 20}),
    ])
    segs = tu.get_runtime_adapter("cursor").parse(
        tu.CursorSession(conv, "hook_ledger", ledger_path=path)
    )["segments"]
    assert [s["start_ts"] for s in segs] == ["2026-06-12T10:00:00Z",
                                             "2026-06-12T11:00:00Z"]


def test_subagent_usage_rolls_into_parent_segment_exactly_once(tu, tmp_path, monkeypatch):
    # Cursor parent hook counts exclude subagents, so the child's tokens are
    # merged into the parent total and its own row stays a subset of it.
    from test_cursor_adapter import ledger_record

    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-rollup"
    path = synthetic_ledger(tu, tmp_path, conv, [
        ledger_record("beforeSubmitPrompt", conversation_id=conv, generation_id="g1",
                      prompt="parent turn"),
        ledger_record("subagentStart", conversation_id=conv, generation_id="g1",
                      subagent_id="s1", subagent_type="explore",
                      subagent_model="claude-sonnet-4", task="scan auth"),
        ledger_record("subagentStop", conversation_id=conv, generation_id="g1",
                      subagent_id="s1", subagent_type="explore",
                      model="claude-sonnet-4", tokens={"output_tokens": 99}),
        ledger_record("stop", conversation_id=conv, generation_id="g1",
                      model="claude-sonnet-4",
                      tokens={"input_tokens": 500, "output_tokens": 100}),
    ])
    seg = tu.get_runtime_adapter("cursor").parse(
        tu.CursorSession(conv, "hook_ledger", ledger_path=path)
    )["segments"][0]
    assert tu.sum_buckets(seg["by_model"])["output"] == 199
    assert seg["subagents"][0]["by_model"]["claude-sonnet-4"]["output"] == 99
    assert seg["subagents"][0]["output_tokens"] == 99


def test_child_only_turn_keeps_its_usage(tu, tmp_path, monkeypatch):
    # A turn whose only recorded usage is its subagent's used to render as an
    # empty row and be skipped entirely by the report table.
    from test_cursor_adapter import ledger_record

    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "ledger"))
    conv = "conv-child-only"
    path = synthetic_ledger(tu, tmp_path, conv, [
        ledger_record("beforeSubmitPrompt", conversation_id=conv, generation_id="g1",
                      prompt="delegate everything"),
        ledger_record("subagentStart", conversation_id=conv, generation_id="g1",
                      subagent_id="s1", subagent_type="shell",
                      subagent_model="claude-sonnet-4", task="run the suite"),
        ledger_record("subagentStop", conversation_id=conv, generation_id="g1",
                      subagent_id="s1", subagent_type="shell",
                      model="claude-sonnet-4", tokens={"output_tokens": 77}),
    ])
    result = tu.get_runtime_adapter("cursor").parse(
        tu.CursorSession(conv, "hook_ledger", ledger_path=path)
    )
    seg = result["segments"][0]
    assert tu.sum_buckets(seg["by_model"])["output"] == 77
    data = tu.aggregate(result["segments"], tu.load_pricing())
    assert "delegate everything" in tu.render_report(data, show_agents=True)
    assert "shell ×1" in tu.render_report(data, show_agents=True)
