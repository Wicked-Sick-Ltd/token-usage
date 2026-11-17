"""cursor-cloud runtime: Cloud Agent usage from the Cursor Admin API, offline."""
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
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


@pytest.mark.parametrize("odd", ["bc-x.jsonl", "bc-not_an_id!.jsonl"])
def test_sync_skips_ledger_names_that_are_not_agent_ids(tu, cloud_dir, odd):
    cloud_dir.mkdir(parents=True)
    (cloud_dir / odd).write_text("")
    warnings = []
    result = sync(tu, warnings=warnings, now_ms=NOW_MS)
    assert result["agents"] == [AGENT_A, AGENT_B]
    assert (cloud_dir / f"{AGENT_A}.jsonl").is_file()
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] == NOW_MS
    assert any(repr(odd) in w for w in warnings)
    adapter = tu.get_runtime_adapter("cursor-cloud")
    assert sorted(p.name for p in adapter.iter_sessions()) == [f"{AGENT_A}.jsonl", f"{AGENT_B}.jsonl"]


def test_sync_skips_a_ledger_name_that_is_not_a_regular_file(tu, cloud_dir):
    stray = "bc-00000000-0000-4000-8000-00000000cccc"
    (cloud_dir / f"{stray}.jsonl").mkdir(parents=True)
    warnings = []
    result = sync(tu, warnings=warnings, now_ms=NOW_MS)
    assert result["agents"] == [AGENT_A, AGENT_B]
    assert (cloud_dir / f"{AGENT_B}.jsonl").is_file()
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] == NOW_MS
    assert any(f"{stray}.jsonl" in w for w in warnings)


def test_one_unwritable_fetched_ledger_does_not_stop_the_others(tu, cloud_dir):
    # A non-file squatting on a real agent's ledger name: that agent is
    # skipped, every other agent still refreshes, and the cursor holds so
    # the skipped agent's events are fetched again next time.
    (cloud_dir / f"{AGENT_A}.jsonl").mkdir(parents=True)
    warnings = []
    result = sync(tu, warnings=warnings, since="2026-11-13", now_ms=NOW_MS)
    assert (cloud_dir / f"{AGENT_B}.jsonl").is_file()
    assert result["events"] == 1
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert "high_water_ms" not in state and state["last_sync_ms"] == NOW_MS
    assert any(AGENT_A in w for w in warnings)
    (cloud_dir / f"{AGENT_A}.jsonl").rmdir()
    sync(tu, since="2026-11-13", now_ms=NOW_MS + 1)
    assert (cloud_dir / f"{AGENT_A}.jsonl").is_file()
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] == NOW_MS + 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_at_a_ledger_name_does_not_hang_the_sync(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    fifo = cloud_dir / f"{AGENT_A}.jsonl"
    fifo.unlink()
    os.mkfifo(fifo)
    warnings, done = [], []
    worker = threading.Thread(daemon=True, target=lambda: done.append(
        sync(tu, warnings=warnings, since="2026-11-13", now_ms=NOW_MS + 1)))
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive():
        # Release the blocked reader so the thread can finish.
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(timeout=5)
        pytest.fail("sync blocked opening a FIFO named like a ledger")
    assert done and (cloud_dir / f"{AGENT_B}.jsonl").is_file()
    assert any(f"{AGENT_A}.jsonl" in w for w in warnings)
    assert tu.get_runtime_adapter("cursor-cloud").locate(AGENT_A) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_at_the_ledger_temp_name_does_not_hang_the_sync(tu, cloud_dir):
    cloud_dir.mkdir(parents=True)
    fifo = cloud_dir / f"{AGENT_A}.jsonl.{os.getpid()}.tmp"
    os.mkfifo(fifo)
    warnings, done = [], []
    worker = threading.Thread(daemon=True, target=lambda: done.append(
        sync(tu, warnings=warnings, since="2026-11-13")))
    worker.start()
    worker.join(timeout=5)
    if worker.is_alive():
        os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
        worker.join(timeout=5)
        pytest.fail("sync blocked writing through a FIFO at the temp name")
    assert done and (cloud_dir / f"{AGENT_B}.jsonl").is_file()
    assert not fifo.exists()
    assert any(f"{AGENT_A}.jsonl" in w for w in warnings)
    sync(tu, since="2026-11-13", now_ms=NOW_MS + 1)
    assert (cloud_dir / f"{AGENT_A}.jsonl").is_file()


def _open_fd_count():
    return len(list(Path("/proc/self/fd").iterdir()))


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
def test_ledger_helpers_release_descriptors(tu, tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    before = _open_fd_count()
    tu._write_regular_file(path, "alpha\n")
    assert tu._cursor_cloud_read_ledger(path) == (None, [])
    assert path.read_text(encoding="utf-8") == "alpha\n"
    assert _open_fd_count() == before

    def fdopen_fails(*_a, **_k):
        raise OSError("fdopen failed")

    monkeypatch.setattr(os, "fdopen", fdopen_fails)
    with pytest.raises(OSError, match="fdopen failed"):
        tu._write_regular_file(path, "beta\n")
    with pytest.raises(OSError, match="fdopen failed"):
        tu._cursor_cloud_read_ledger(path)
    assert _open_fd_count() == before


def test_symlink_ledger_lookup_reports_not_found(tu, mcp, cloud_dir, tmp_path):
    cloud_dir.mkdir(parents=True)
    target = tmp_path / "real.jsonl"
    target.write_text(json.dumps({
        "type": "usage", "timestamp": "1794592800000", "cloudAgentId": AGENT_A,
        "model": "m", "chargedCents": 1,
    }) + "\n")
    link = cloud_dir / f"{AGENT_A}.jsonl"
    link.symlink_to(target)
    assert link.is_file()
    adapter = tu.get_runtime_adapter("cursor-cloud")
    assert adapter.locate(AGENT_A) is None
    assert adapter.locate(str(link)) is None
    with pytest.raises(mcp.ToolError, match="session found"):
        mcp.tool_session_cost({"runtime": "cursor-cloud", "session_id": AGENT_A})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "json", "--runtime", "cursor-cloud", AGENT_A],
        capture_output=True, text=True, env=dict(os.environ), check=False)
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "session found" in proc.stderr
    # A real ledger at the same name is still found.
    link.unlink()
    target.replace(link)
    assert adapter.locate(AGENT_A) == link


def test_cli_sync_survives_an_odd_ledger_name(tu, cloud_dir, monkeypatch, capsys):
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "bc-x.jsonl").write_text("")
    monkeypatch.setenv("CURSOR_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(tu, "_cursor_cloud_request", fake_api())
    monkeypatch.setattr(sys, "argv", ["token_usage.py", "cursor-cloud-sync", "--json"])
    tu.main()
    out = json.loads(capsys.readouterr().out)
    assert out["agents"] == [AGENT_A, AGENT_B]
    assert any("bc-x.jsonl" in w for w in out["warnings"])


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


def test_empty_answer_never_erases_ledgers(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    empty = {"usageEvents": [], "pagination": {"hasNextPage": False}}
    sync(tu, since="2026-11-13", request=fake_api(pages=[empty]))
    sync(tu, since="2026-11-13", agent_id=AGENT_A, request=fake_api(pages=[empty]))
    lines = (cloud_dir / f"{AGENT_A}.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_high_water_and_last_sync_only_move_on_full_team_syncs(tu, cloud_dir):
    sync(tu, now_ms=NOW_MS)
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] == NOW_MS
    later = NOW_MS + 10 * 86400 * 1000
    sync(tu, since="2026-11-22", now_ms=later)  # starts after the previous high water
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] == NOW_MS
    sync(tu, agent_id=AGENT_A, now_ms=later + 1)
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] == NOW_MS and state["last_sync_ms"] == later


def test_conversation_spanning_days_bills_each_day(tu, cloud_dir):
    cloud_dir.mkdir(parents=True)
    rows = [{"type": "usage", "timestamp": str(ts), "cloudAgentId": AGENT_A,
             "conversationId": "c", "model": "m",
             "tokenUsage": {"inputTokens": 1, "outputTokens": 1}, "chargedCents": 100}
            for ts in (1794592800000, 1794592800000 + 2 * 86400 * 1000)]
    (cloud_dir / f"{AGENT_A}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    adapter = tu.get_runtime_adapter("cursor-cloud")
    summary, _ = tu.cached_adapter_summary(adapter, cloud_dir / f"{AGENT_A}.jsonl", {})
    assert sorted(v["cost_usd"] for v in summary["by_day"].values()) == [1.0, 1.0]


def test_project_from_any_repository_host(tu, cloud_dir):
    adapter = tu.get_runtime_adapter("cursor-cloud")
    cloud_dir.mkdir(parents=True)
    path = cloud_dir / f"{AGENT_A}.jsonl"
    for repo, want in (("https://gitlab.com/group/sub/app.git", "group/sub/app"),
                       ("github.com/example-org/legacy-app", "example-org/legacy-app"),
                       ("", "cursor-cloud")):
        path.write_text(json.dumps({"type": "agent", "id": AGENT_A, "repository": repo}) + "\n")
        assert adapter.project(path) == want


def test_failed_autosync_is_throttled_and_autosync_off_means_no_network(tu, cloud_dir, monkeypatch):
    calls = []

    def failing(method, path, key, body=None):
        calls.append(path)
        raise RuntimeError("Cursor API returned HTTP 401")
    monkeypatch.setattr(tu, "_cursor_cloud_request", failing)
    monkeypatch.setenv("CURSOR_ADMIN_API_KEY", "test-admin-key")
    adapter = tu.get_runtime_adapter("cursor-cloud")
    warnings = []
    list(adapter.iter_sessions(warnings=warnings))
    list(adapter.iter_sessions(warnings=warnings))
    assert len(calls) == 1
    assert any("sync failed" in w for w in warnings)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_CLOUD_AUTOSYNC", "0")
    assert adapter.locate(AGENT_B) is None
    assert len(calls) == 1


def test_metadata_backfills_for_unchanged_ledgers(tu, cloud_dir):
    sync(tu, since="2026-11-13")
    agent = {"name": "Track 2", "source": {"repository": "github.com/example-org/legacy-app"}}
    empty = {"usageEvents": [], "pagination": {"hasNextPage": False}}
    sync(tu, user_key="user-key", request=fake_api(pages=[empty], agent=agent))
    adapter = tu.get_runtime_adapter("cursor-cloud")
    assert adapter.project(cloud_dir / f"{AGENT_A}.jsonl") == "example-org/legacy-app"
    assert len((cloud_dir / f"{AGENT_A}.jsonl").read_text().splitlines()) == 3


def test_autosync_state_access_holds_the_lock(tu, cloud_dir, monkeypatch):
    depth = {"n": 0}
    real_lock = tu._cursor_cloud_lock
    real_state = tu._cursor_cloud_state
    real_save = tu._cursor_cloud_save_state

    @contextlib.contextmanager
    def wrapped_lock():
        depth["n"] += 1
        try:
            with real_lock():
                yield
        finally:
            depth["n"] -= 1

    def state():
        assert depth["n"] > 0, "autosync read state outside _cursor_cloud_lock"
        return real_state()

    def save(state, warnings=None):
        assert depth["n"] > 0, "autosync wrote state outside _cursor_cloud_lock"
        return real_save(state, warnings)

    monkeypatch.setattr(tu, "_cursor_cloud_lock", wrapped_lock)
    monkeypatch.setattr(tu, "_cursor_cloud_state", state)
    monkeypatch.setattr(tu, "_cursor_cloud_save_state", save)
    monkeypatch.setattr(tu, "_cursor_cloud_request", fake_api())
    monkeypatch.setenv("CURSOR_ADMIN_API_KEY", "test-admin-key")
    tu._cursor_cloud_maybe_autosync()
    written = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert "last_attempt_ms" in written and "high_water_ms" in written


def test_concurrent_autosync_does_not_clobber_high_water(tu, cloud_dir, monkeypatch):
    cloud_dir.mkdir(parents=True)
    # Use the wall clock: maybe_autosync stamps high_water_ms with time.time().
    seeded = int(time.time() * 1000) - 3600 * 1000
    (cloud_dir / ".sync-state.json").write_text(json.dumps({
        "high_water_ms": seeded, "last_sync_ms": 0,
    }))
    monkeypatch.setenv("CURSOR_ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(tu, "_cursor_cloud_request", fake_api())

    about_to_save = threading.Event()
    other_done = threading.Event()
    real_save = tu._cursor_cloud_save_state
    first_tid = {"id": None, "paused": False}
    observed = []

    def save(state, warnings=None):
        tid = threading.get_ident()
        if first_tid["id"] is None:
            first_tid["id"] = tid
        if tid == first_tid["id"] and not first_tid["paused"]:
            first_tid["paused"] = True
            about_to_save.set()
            other_done.wait(timeout=1.0)
            before = None
            path = cloud_dir / ".sync-state.json"
            if path.is_file():
                before = json.loads(path.read_text()).get("high_water_ms")
            result = real_save(state, warnings)
            after = json.loads(path.read_text()).get("high_water_ms")
            observed.append((before, after))
            return result
        return real_save(state, warnings)

    monkeypatch.setattr(tu, "_cursor_cloud_save_state", save)
    finished = []

    def run_first():
        tu._cursor_cloud_maybe_autosync()
        finished.append("first")

    def run_second():
        try:
            assert about_to_save.wait(timeout=5)
            tu._cursor_cloud_maybe_autosync()
            finished.append("second")
        finally:
            other_done.set()

    threads = [threading.Thread(target=run_first), threading.Thread(target=run_second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert sorted(finished) == ["first", "second"]
    assert observed
    before, after = observed[0]
    # A completed peer sync must not have its high_water rolled back by a
    # stale last_attempt_ms write that raced outside the lock.
    if before is not None:
        assert after >= before
    state = json.loads((cloud_dir / ".sync-state.json").read_text())
    assert state["high_water_ms"] >= seeded
    assert "last_attempt_ms" in state


def test_cache_write_tokens_use_shared_write_buckets(tu, cloud_dir):
    # Admin API cacheWriteTokens have no TTL. Other runtimes put the same
    # kind of counter in cache_5m (Codex cache_write_input_tokens, Cursor
    # IDE cacheWriteTokens); cache_1h is only used when a 1-hour TTL is known.
    cloud_dir.mkdir(parents=True)
    row = {"type": "usage", "timestamp": "1794592800000", "cloudAgentId": AGENT_A,
           "conversationId": "c", "model": "claude-4.5-sonnet",
           "tokenUsage": {"inputTokens": 7, "outputTokens": 10,
                          "cacheWriteTokens": 39331, "cacheReadTokens": 147695},
           "chargedCents": 21.68}
    (cloud_dir / f"{AGENT_A}.jsonl").write_text(json.dumps(row) + "\n")
    parsed = tu.get_runtime_adapter("cursor-cloud").parse(cloud_dir / f"{AGENT_A}.jsonl")
    usage = tu.aggregate(parsed["segments"], {})["total"]["usage"]
    assert usage["input"] == 7
    assert usage["output"] == 10
    assert usage["cache_read"] == 147695
    assert usage["cache_5m"] == 39331
    assert usage["cache_1h"] == 0
    assert tu.aggregate(parsed["segments"], {})["total"]["cost_usd"] == pytest.approx(0.2168)

