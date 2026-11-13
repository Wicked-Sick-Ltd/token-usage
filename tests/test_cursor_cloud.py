"""cursor-cloud runtime: Cloud Agent usage from the Cursor Admin API, offline."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SCRIPT

FIXTURE = Path(__file__).parent / "fixtures" / "cursor_cloud" / "filtered_usage_events.json"
AGENT_A = "bc-00000000-0000-4000-8000-00000000aaaa"
AGENT_B = "bc-00000000-0000-4000-8000-00000000bbbb"
NOW_MS = 1794600000000  # after every fixture event


def fake_api(pages=None, agent=None, calls=None):
    """Stand-in for _cursor_cloud_request: serves the fixture, records calls."""
    payload = json.loads(FIXTURE.read_text())
    pages = pages or [payload]

    def request(method, path, key, body=None):
        if calls is not None:
            calls.append((method, path, key, body))
        if path.startswith("/v0/agents/"):
            return agent or {}
        assert path == "/teams/filtered-usage-events"
        return pages[(body or {}).get("page", 1) - 1]
    return request


@pytest.fixture
def cloud_dir(tmp_path, monkeypatch):
    root = tmp_path / "cursor-cloud"
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_CLOUD_DIR", str(root))
    monkeypatch.delenv("CURSOR_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    return root


def sync(tu, **kw):
    kw.setdefault("api_key", "test-admin-key")
    kw.setdefault("user_key", "")
    kw.setdefault("now_ms", NOW_MS)
    kw.setdefault("request", fake_api())
    return tu.sync_cursor_cloud(**kw)


def test_sync_writes_one_ledger_per_agent_without_emails(tu, cloud_dir):
    calls = []
    result = sync(tu, request=fake_api(calls=calls), since="2026-11-13")
    assert result["agents"] == [AGENT_A, AGENT_B]
    assert result["events"] == 3  # the event with no cloudAgentId is not a cloud run
    assert sorted(p.name for p in cloud_dir.glob("bc-*.jsonl")) == [f"{AGENT_A}.jsonl", f"{AGENT_B}.jsonl"]
    text = (cloud_dir / f"{AGENT_A}.jsonl").read_text()
    assert "userEmail" not in text and "example.com" not in text
    assert "test-admin-key" not in text
    body = calls[0][3]
    assert body["cloudAgentId"] == "*" and body["pageSize"] == 1000
    assert calls[0][2] == "test-admin-key"


def test_resync_replaces_the_window_instead_of_double_counting(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    sync(tu, since="2026-11-13")
    events = [json.loads(line) for line in (cloud_dir / f"{AGENT_A}.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["type"] == "usage"]) == 2


def test_pagination_and_agent_metadata(tu, cloud_dir):
    payload = json.loads(FIXTURE.read_text())
    first = dict(payload, usageEvents=payload["usageEvents"][:1],
                 pagination={"hasNextPage": True})
    second = dict(payload, usageEvents=payload["usageEvents"][1:2],
                  pagination={"hasNextPage": False})
    agent = {"id": AGENT_A, "name": "PHP 8.4 [Track 1] core", "createdAt": "2026-11-13T17:49:16.563Z",
             "source": {"repository": "https://github.com/example-org/legacy-app", "ref": "main"}}
    sync(tu, since="2026-11-13", user_key="user-key",
         request=fake_api(pages=[first, second], agent=agent))
    adapter = tu.get_runtime_adapter("cursor-cloud")
    path = adapter.locate(AGENT_A)
    assert adapter.project(path) == "example-org/legacy-app"
    parsed = adapter.parse(path)
    assert parsed["segments"][0]["prompt"] == "PHP 8.4 [Track 1] core"
    assert sum(s["by_model"].get("gpt-5.4", {}).get("requests", 0) for s in parsed["segments"]) == 1


def test_session_cost_uses_cursor_charged_cents(tu, mcp, cloud_dir):
    sync(tu, since="2026-11-13")
    data = json.loads(mcp.tool_session_cost({"runtime": "cursor-cloud", "session_id": AGENT_A}))
    assert data["runtime"] == "cursor-cloud"
    assert data["measurement"] == "exact"
    assert data["total"]["usage"] == {"input": 1400, "output": 300, "cache_read": 51000,
                                      "cache_5m": 3000, "cache_1h": 0, "requests": 2}
    # 21.68 + 2.32 cents charged, not a pricing-table estimate
    assert data["total"]["cost_usd"] == pytest.approx(0.24)
    assert data["total"]["unpriced_models"] == []
    models = {r["model"]: r["cost_usd"] for r in data["by_label"]["(cloud agent)"]["models"]}
    assert models == {"claude-4.5-sonnet": pytest.approx(0.2168), "gpt-5.4": pytest.approx(0.0232)}


def test_history_and_top_consumers_attribute_per_agent(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    top = tu.run_top_consumers(by="session", since="2026-11-01", runtime="cursor-cloud")
    rows = {r["session_id"]: r for r in top["rows"]}
    assert set(rows) == {AGENT_A, AGENT_B}
    assert rows[AGENT_A]["cost_usd"] == pytest.approx(0.24)
    assert rows[AGENT_B]["cost_usd"] == pytest.approx(0.001)
    assert top["unpriced_models"] == []
    by_model = tu.run_history(by="model", runtime="cursor-cloud")
    costs = {r["key"]: r["cost_usd"] for r in by_model["rows"]}
    assert costs["claude-4.5-sonnet"] == pytest.approx(0.2168)
    assert costs["composer-2"] == pytest.approx(0.001)


def test_events_without_tokens_are_partial(tu, cloud_dir):
    cloud_dir.mkdir(parents=True)
    rows = [{"type": "usage", "timestamp": "1794592800000", "cloudAgentId": AGENT_A,
             "conversationId": "c", "model": "claude-4-sonnet-thinking",
             "isTokenBasedCall": False, "chargedCents": 8}]
    (cloud_dir / f"{AGENT_A}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    parsed = tu.get_runtime_adapter("cursor-cloud").parse(cloud_dir / f"{AGENT_A}.jsonl")
    assert parsed["measurement"] == "partial"
    agg = tu.aggregate(parsed["segments"], {})
    assert agg["total"]["cost_usd"] == pytest.approx(0.08)
    assert agg["total"]["usage"]["requests"] == 1


def test_sync_requires_an_admin_key_and_never_echoes_it(tu, cloud_dir):
    with pytest.raises(RuntimeError, match="CURSOR_ADMIN_API_KEY"):
        tu.sync_cursor_cloud(api_key=None, request=fake_api())


def test_no_key_means_no_network_and_a_helpful_warning(tu, cloud_dir):
    def boom(*a, **k):
        raise AssertionError("network used without a key")
    adapter = tu.get_runtime_adapter("cursor-cloud")
    original = tu._cursor_cloud_request
    tu._cursor_cloud_request = boom
    try:
        assert list(adapter.iter_sessions()) == []
        warnings = []
        assert tu.check_adapter_root(adapter, warnings) == str(cloud_dir)
        assert "CURSOR_ADMIN_API_KEY" in warnings[0]
        assert adapter.locate(AGENT_A) is None
    finally:
        tu._cursor_cloud_request = original


def test_auto_never_selects_cursor_cloud(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    assert tu.resolve_runtime_corpus("auto")[1] != "cursor-cloud"
    assert "cursor-cloud" in tu.RUNTIME_CHOICES


def test_rejects_non_agent_ids(tu, cloud_dir):
    with pytest.raises(ValueError):
        tu._cursor_cloud_ledger("../../etc/passwd")
    assert tu.get_runtime_adapter("cursor-cloud").locate("not-an-agent") is None


def test_cli_sync_without_key_fails_cleanly(cloud_dir):
    env = {k: v for k, v in os.environ.items() if k != "CURSOR_ADMIN_API_KEY"}
    proc = subprocess.run([sys.executable, str(SCRIPT), "cursor-cloud-sync", "--since", "1d"],
                          capture_output=True, text=True, env=env, check=False)
    assert proc.returncode != 0
    assert "CURSOR_ADMIN_API_KEY" in proc.stderr


def test_cli_report_reads_a_synced_ledger(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    proc = subprocess.run([sys.executable, str(SCRIPT), "json", "--runtime", "cursor-cloud", AGENT_A],
                          capture_output=True, text=True, env=dict(os.environ), check=False)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["runtime"] == "cursor-cloud"
    assert data["total"]["cost_usd"] == pytest.approx(0.24)
