"""Copilot's transient per-call ledger and cumulative native shutdown totals."""
import json
import os
import subprocess
import sys

import pytest
from conftest import SCRIPT, write_jsonl


def event(kind, eid, **data):
    return {"type": kind, "id": str(eid), "timestamp": f"2026-10-05T10:00:{eid:02}Z", "data": data}


def usage():
    return {"inputTokens": 110, "outputTokens": 20, "cacheReadTokens": 40,
            "cacheWriteTokens": 10, "reasoningTokens": 7}


def recording(tmp_path, monkeypatch):
    root = tmp_path / "copilot"
    monkeypatch.setenv("TOKEN_USAGE_COPILOT_HOME", str(root))
    monkeypatch.delenv("TOKEN_USAGE_TRANSCRIPT", raising=False)
    path = root / "session-state/session/events.jsonl"
    write_jsonl(path, [event("session.start", 0, sessionId="session", context={"cwd": str(tmp_path)}),
                       event("user.message", 1, content="/review private prompt")])
    ledger = root / "token-usage/session.jsonl"
    write_jsonl(ledger, [event("token-usage.capture", 0, sessionId="session", context={"cwd": str(tmp_path)}),
                        event("user.message", 1, label="/review"),
                        event("assistant.usage", 2, model="test-model", **usage())])
    return path, ledger


def test_copilot_discovery_dedup_accounting_and_mcp(tu, mcp, tmp_path, monkeypatch):
    path, ledger = recording(tmp_path, monkeypatch)
    adapter = tu.get_runtime_adapter("copilot")
    assert adapter.locate(session_id="session") == path
    assert adapter.locate(session_id="missing") is None
    assert adapter.locate("missing.json") is None
    assert list(adapter.iter_sessions(tmp_path)) == [path]
    assert list(adapter.iter_sessions(tmp_path / "other")) == []
    assert tu.resolve_runtime("auto", ledger)[1] == "copilot"
    data = json.loads(mcp.tool_session_cost({"runtime": "copilot", "session_id": "session"}))
    assert data["runtime"] == "copilot"
    assert data["total"]["usage"] == {"input": 60, "output": 20, "cache_read": 40,
                                      "cache_5m": 10, "cache_1h": 0, "requests": 1}
    assert data["total"]["cost_usd"] is None
    assert "/review" in data["by_label"]
    rows = list(tu.iter_jsonl(ledger))
    rows.append(rows[-1])
    child = event("assistant.usage", 3, model="test-model", **usage())
    child["agentId"] = "child"
    rows.append(child)
    write_jsonl(ledger, rows)
    parsed = adapter.parse(path)
    assert tu.aggregate(parsed["segments"], {})["total"]["usage"]["output"] == 40
    assert parsed["segments"][0]["subagents"][0]["output_tokens"] == 20
    assert tu.run_history(runtime="copilot")["rows"][0]["usage"]["requests"] == 2


def test_copilot_shutdown_resume_and_partial_capture(tu, tmp_path, monkeypatch):
    path, ledger = recording(tmp_path, monkeypatch)
    rows = list(tu.iter_jsonl(path))
    metric = {"usage": usage(), "requests": {"count": 1, "cost": 999}}
    rows += [event("session.shutdown", 3, modelMetrics={"test-model": metric})]
    write_jsonl(path, rows)
    adapter = tu.get_runtime_adapter("copilot")
    first, _ = tu.cached_adapter_summary(adapter, path, {})
    assert first["total"]["usage"]["requests"] == 1
    assert first["measurement"] == "exact"
    calls = list(tu.iter_jsonl(ledger))
    calls[-1]["data"]["cacheTtlSeconds"] = 3600
    write_jsonl(ledger, calls)
    with_ttl = adapter.parse(path)
    assert tu.aggregate(with_ttl["segments"], {})["total"]["usage"]["cache_1h"] == 10
    assert tu.aggregate(with_ttl["segments"], {})["total"]["usage"]["cache_5m"] == 0
    metric2 = {"usage": {k: v * 2 for k, v in usage().items()}, "requests": {"count": 2}}
    rows += [event("session.shutdown", 5, modelMetrics={"test-model": metric2})]
    write_jsonl(path, rows)
    second, _ = tu.cached_adapter_summary(adapter, path, {})
    assert second["total"]["usage"]["output"] == 40
    assert second["measurement"] == "partial"
    ledger.unlink()
    historical = adapter.parse(path)
    assert tu.aggregate(historical["segments"], {})["total"]["usage"]["output"] == 40
    assert any("attribution" in w for w in historical["warnings"])


def test_copilot_activity_and_invalid_counters(tu):
    activity = [event("user.message", 1, content="hello")]
    assert tu.parse_copilot_records(activity)["measurement"] == "activity_only"
    for bad in (True, -1, "110", 1):
        counters = dict(usage(), inputTokens=bad)
        parsed = tu.parse_copilot_records(activity + [event("assistant.usage", 2, model="test", **counters)])
        assert parsed["measurement"] == "activity_only"


@pytest.mark.parametrize("command", ["report", "json", "insights", "live", "export", "dashboard", "history", "top_consumers"])
def test_copilot_cli(command, tmp_path, monkeypatch):
    path, _ = recording(tmp_path, monkeypatch)
    args = [sys.executable, str(SCRIPT), command, "--runtime", "copilot"]
    if command not in ("dashboard", "history", "top_consumers"):
        args.append(str(path))
    if command == "live":
        args += ["--iterations", "1"]
    if command == "export":
        args += ["--scope", "session"]
    if command == "dashboard":
        args += ["--output", str(tmp_path / "dashboard.html")]
    result = subprocess.run(args, env=dict(os.environ, PYTHONUTF8="1"), capture_output=True,
                            text=True, encoding="utf-8", timeout=20, check=False)
    assert result.returncode == 0, result.stderr
