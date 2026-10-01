"""Synthetic native Codex rollouts: accounting, discovery, tools and hooks."""
import json
import os
import subprocess
import sys

import pytest
from conftest import SCRIPT, write_jsonl


def event(kind, payload, second=0):
    return {"type": kind, "timestamp": f"2026-10-01T10:00:{second:02}Z", "payload": payload}


def usage(inp=100, cached=40, out=20):
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "reasoning_output_tokens": 7, "total_tokens": inp + out}


def make_rollout(tmp_path, monkeypatch, records=True, sid="thread-1"):
    root = tmp_path / "codex"
    monkeypatch.setenv("TOKEN_USAGE_CODEX_HOME", str(root))
    rows = [event("session_meta", {"id": sid, "cwd": str(tmp_path / "project")}),
            event("event_msg", {"type": "task_started", "turn_id": "turn-1"}),
            event("turn_context", {"model": "gpt-test", "turn_id": "turn-1"}),
            event("response_item", {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "$review check the change"}]})]
    count = event("event_msg", {"type": "token_count", "info": {
        "total_token_usage": usage(), "last_token_usage": usage()}}, 1)
    if records:
        record = event("token_usage_record", {"response_id": "response-1", "turn_id": "turn-1", "usage": usage()}, 1)
        rows += [record, record]
    rows += [count, count, event("event_msg", {"type": "token_count", "info": None})]
    return write_jsonl(root / "sessions/2026/10/01" / f"rollout-{sid}.jsonl", rows)


@pytest.mark.parametrize("records", [True, False])
def test_totals_dedup_cache_reasoning_and_skill(tu, tmp_path, monkeypatch, records):
    path = make_rollout(tmp_path, monkeypatch, records)
    parsed = tu.parse_codex_session(path)
    data = tu.aggregate(parsed["segments"], {"gpt-test": {"input": 2, "output": 10, "cache_read": .2}})
    assert parsed["measurement"] == "exact"
    assert data["total"]["usage"] == {"input": 60, "output": 20, "cache_read": 40, "cache_5m": 0, "cache_1h": 0, "requests": 1}
    assert data["by_label"]["$review"]["usage"] == data["total"]["usage"]
    assert data["total"]["cost_usd"] == pytest.approx(.000328)


def test_cumulative_deltas_across_turns_models_and_midnight(tu, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch, records=False)
    with p.open("a", encoding="utf-8") as f:
        for e in [event("event_msg", {"type": "task_started", "turn_id": "turn-2"}, 2),
                  event("turn_context", {"model": "gpt-other", "turn_id": "turn-2"}),
                  event("event_msg", {"type": "token_count", "info": {"total_token_usage": usage(200, 60, 50)}}, 3)]:
            f.write(json.dumps(e) + "\n")
    data = tu.aggregate(tu.parse_codex_session(p)["segments"], {})
    assert data["total"]["usage"]["input"] == 140
    assert data["total"]["usage"]["output"] == 50
    assert data["total"]["by_model"]["gpt-other"]["cache_read"] == 20


def test_discovery_is_authoritative_and_archives_deduplicate(tu, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    adapter = tu.get_runtime_adapter("codex")
    assert adapter.locate(session_id="thread-1") == p
    assert adapter.locate(session_id="missing") is None
    assert adapter.locate("missing.jsonl") is None
    assert adapter.locate(project_dir=tmp_path / "other") is None
    archive = tu.codex_home() / "archived_sessions/copy.jsonl"
    archive.parent.mkdir()
    archive.write_bytes(p.read_bytes())
    assert list(adapter.iter_sessions()) == [p]
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-1")
    assert adapter.locate() == p


def test_auto_detects_content_not_jsonl_suffix(tu, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    assert tu.resolve_runtime("auto", str(p))[1] == "codex"


def test_all_reports_and_mcp_use_codex(tu, mcp, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    monkeypatch.setenv("TOKEN_USAGE_RUNTIME", "codex")
    data = json.loads(mcp.tool_session_cost({"session_id": "thread-1", "format": "json"}))
    assert data["runtime"] == "codex"
    assert data["total"]["usage"]["output"] == 20
    for fn, args in [(mcp.tool_history, {}), (mcp.tool_top_consumers, {"since": "36500d"}),
                     (mcp.tool_insights, {"transcript": str(p)}),
                     (mcp.tool_diff, {"old": str(p), "new": str(p)})]:
        result = json.loads(fn(dict(args, runtime="codex", format="json")))
        assert result["runtime"] == "codex"
    assert tu.dashboard_data(runtime="codex", since="36500d")["runtime"] == "codex"


def test_codex_hook_ledger_and_fail_open(tu, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    assert tu._run_hook({"session_id": "thread-1", "transcript_path": str(p)}, runtime="codex") == 0
    ledger = json.loads((tu.ledger_dir() / "codex-thread-1.json").read_text())
    assert ledger["runtime"] == "codex"
    assert ledger["total"]["usage"]["output"] == 20
    proc = subprocess.run([sys.executable, str(SCRIPT), "codex-hook"], input="bad json",
                          text=True, capture_output=True, check=False)
    assert proc.returncode == 0


@pytest.mark.parametrize("command", ["json", "report", "live", "export"])
def test_cli_codex(command, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    args = [sys.executable, str(SCRIPT), command, "--runtime", "codex", str(p)]
    if command == "live":
        args += ["--iterations", "1"]
    if command == "export":
        args += ["--scope", "session"]
    proc = subprocess.run(args, capture_output=True, text=True, check=False,
                          env=dict(os.environ, PYTHONUTF8="1"), encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert "$review" in proc.stdout


def test_no_usage_is_unmeasured(tu, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    rows = [e for e in tu.iter_jsonl(p) if e["type"] != "token_usage_record"
            and e.get("payload", {}).get("type") != "token_count"]
    write_jsonl(p, rows)
    parsed = tu.parse_codex_session(p)
    assert parsed["measurement"] == "activity_only"


def test_native_manifest_targets_exist():
    root = SCRIPT.parent.parent
    manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
    for field in ("skills", "hooks", "mcpServers"):
        assert (root / manifest[field]).exists()


def test_children_roll_up_once_and_invalidate_parent_cache(tu, tmp_path, monkeypatch):
    parent = make_rollout(tmp_path, monkeypatch)
    child = make_rollout(tmp_path, monkeypatch, sid="child")
    rows = list(tu.iter_jsonl(child))
    rows[0]["payload"]["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": "thread-1"}}}
    write_jsonl(child, rows)
    adapter = tu.get_runtime_adapter("codex")
    assert list(adapter.iter_sessions()) == [parent]
    data = tu.aggregate(adapter.parse(parent)["segments"], {})
    assert data["total"]["usage"]["output"] == 40
    assert data["by_label"]["$review"]["subagents"] == 1
    first, _ = tu.cached_adapter_summary(adapter, parent, {})
    rows.append(event("token_usage_record", {"response_id": "child-response-2", "usage": usage(out=10)}, 4))
    write_jsonl(child, rows)
    second, _ = tu.cached_adapter_summary(adapter, parent, {})
    assert first["total"]["usage"]["output"] == 40
    assert second["total"]["usage"]["output"] == 50
    history = tu.run_history(runtime="codex")
    assert history["rows"][0]["usage"]["output"] == 50


def test_cache_writes_and_invalid_counters(tu):
    flat = tu._codex_flat(dict(usage(), cache_write_input_tokens=10))
    assert flat["input"] == 50
    assert flat["cache_5m"] == 10
    for bad in (True, -1, "100"):
        with pytest.raises(ValueError):
            tu._codex_flat(dict(usage(), input_tokens=bad))
    with pytest.raises(ValueError):
        tu._codex_flat(usage(inp=20, cached=40))


def test_bogus_selector_and_diff_cli(tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch)
    args = [sys.executable, str(SCRIPT), "json", "--runtime", "codex", "--diff", str(p), str(p)]
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["rows"][0]["delta_output"] == 0


def test_cumulative_reset_is_disclosed(tu, tmp_path, monkeypatch):
    p = make_rollout(tmp_path, monkeypatch, records=False)
    rows = list(tu.iter_jsonl(p))
    rows.append(event("event_msg", {"type": "token_count", "info": {"total_token_usage": usage(50, 10, 5)}}))
    write_jsonl(p, rows)
    parsed = tu.parse_codex_session(p)
    assert parsed["measurement"] == "partial"
    assert any("decreased" in warning for warning in parsed["warnings"])


@pytest.mark.parametrize("counters", [{}, None, {"input_tokens": 0}])
def test_missing_native_counters_never_become_exact_zero(tu, tmp_path, monkeypatch, counters):
    path = make_rollout(tmp_path, monkeypatch)
    rows = [e for e in tu.iter_jsonl(path) if e["type"] != "token_usage_record"]
    rows.append(event("token_usage_record", {"response_id": "incomplete", "usage": counters}))
    write_jsonl(path, rows)
    parsed = tu.parse_codex_session(path)
    assert parsed["measurement"] == "activity_only"
    assert any("counters" in warning for warning in parsed["warnings"])
    rows.append(event("token_usage_record", {"response_id": "valid-zero", "usage": usage(0, 0, 0)}))
    write_jsonl(path, rows)
    assert tu.parse_codex_session(path)["measurement"] == "partial"
