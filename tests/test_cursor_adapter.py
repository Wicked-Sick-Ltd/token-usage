"""Cursor runtime adapter: synthetic SQLite, discovery, and parsing."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cursor"


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def build_cursor_tree(
    root,
    *,
    composer_id="comp-usage-001",
    composer=None,
    bubble_headers=None,
    bubbles=None,
    workspace_id="ws-project-alpha",
    project_folder="/tmp/workspace/alpha",
):
    """Lay out a minimal Cursor User tree with one global vscdb."""
    composer = composer or load_json("composer.json")
    bubbles = bubbles or load_json("bubbles.json")
    if bubble_headers is None:
        bubble_headers = composer["fullConversationHeadersOnly"]

    user_dir = Path(root)
    ws_dir = user_dir / "workspaceStorage" / workspace_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "workspace.json").write_text(
        json.dumps({"folder": Path(project_folder).as_uri()}),
        encoding="utf-8",
    )

    db_path = user_dir / "globalStorage" / "state.vscdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cursorDiskKV "
        "(key TEXT PRIMARY KEY, value BLOB NOT NULL)"
    )
    composer = dict(composer)
    composer["composerId"] = composer_id
    composer["workspaceStorageId"] = workspace_id
    composer["fullConversationHeadersOnly"] = bubble_headers
    conn.execute(
        "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (f"composerData:{composer_id}", json.dumps(composer).encode("utf-8")),
    )
    for header in bubble_headers:
        bid = header["bubbleId"]
        if bid not in bubbles:
            continue
        blob = bubbles[bid]
        conn.execute(
            "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:{bid}",
                json.dumps(blob).encode("utf-8"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def session_for(composer_id, source="sqlite", path=None):
    return {
        "composer_id": composer_id,
        "source": source,
        "path": path,
    }


def ledger_record(hook, conversation_id="conv-1", generation_id="gen-1",
                  ts="2026-06-12T10:00:00Z", **extra):
    record = {"hook": hook, "generation_id": generation_id, "ts": ts,
              "conversation_id": conversation_id}
    record.update(extra)
    return record


def write_ledger(tu, ledger_root, conversation_id, records):
    """Write one Cursor hook ledger the way the hook command would name it."""
    path = (Path(ledger_root) / "cursor"
            / f"{tu._cursor_ledger_filename(conversation_id)}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_cursor_adapter_registered(tu):
    adapter = tu.get_runtime_adapter("cursor")
    assert adapter.name == "cursor"


def test_cursor_root_is_isolated_from_developer_data(tu):
    # Cursor's User directory doubles as a session corpus, so any test that
    # reaches discovery without pinning TOKEN_USAGE_CURSOR_DIR reads the
    # developer's own ~/.config/Cursor/User. An autouse fixture pins it for
    # the whole suite; a test that wants its own root sets one afterwards.
    assert list(tu.get_runtime_adapter("cursor").iter_sessions()) == []
    assert os.environ.get("TOKEN_USAGE_CURSOR_DIR")
    assert Path.home() not in tu.cursor_user_dir().parents


def test_cursor_root_isolation_reaches_subprocesses(tu):
    # Subprocess CLI tests build their env from os.environ, so the same pin
    # has to be an environment variable rather than a monkeypatched attribute.
    pinned = Path(os.environ["TOKEN_USAGE_CURSOR_DIR"])
    assert any(p.name.startswith("cursor-isolated") for p in pinned.parents)
    assert not pinned.exists()


def test_locate_explicit_bogus_id_does_not_fall_through_to_latest(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    adapter = tu.get_runtime_adapter("cursor")
    with pytest.raises(tu.CursorExplicitSelectorError):
        adapter.locate("comp-not-a-file-on-disk")
    latest = adapter.locate()
    assert latest.composer_id == "comp-usage-001"


def test_cursor_user_dir_override(tu, monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    assert tu.cursor_user_dir() == (tmp_path / "cursor-user").resolve()


def test_cursor_user_dir_linux_default(tu, monkeypatch):
    monkeypatch.delenv("TOKEN_USAGE_CURSOR_DIR", raising=False)
    monkeypatch.setattr(tu.sys, "platform", "linux")
    assert tu.cursor_user_dir() == (Path.home() / ".config/Cursor/User").resolve()


def test_project_aware_session_discovery(tu, tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root, project_folder=str(project.resolve()))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))

    adapter = tu.get_runtime_adapter("cursor")
    sessions = list(adapter.iter_sessions(project_dir=str(project)))
    assert len(sessions) == 1
    assert sessions[0].composer_id == "comp-usage-001"
    assert sessions[0].source == "sqlite"
    assert sessions[0].project_path == project.resolve()


def test_adapter_project_is_workspace_slug_not_composer_id(tu, tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root, project_folder=str(project.resolve()))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    adapter = tu.get_runtime_adapter("cursor")
    session = next(adapter.iter_sessions())
    assert adapter.project(session) == tu.project_slug(str(project.resolve()))
    assert adapter.session_id(session) == "comp-usage-001"


def test_global_recent_sessions_without_project(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(
        cursor_root,
        composer_id="comp-recent-a",
        project_folder=str((tmp_path / "a").resolve()),
    )
    comp_b = load_json("composer.json")
    comp_b["lastUpdatedAt"] = 1
    build_cursor_tree(
        cursor_root,
        composer_id="comp-recent-b",
        composer=comp_b,
        workspace_id="ws-other",
        project_folder=str((tmp_path / "b").resolve()),
    )
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))

    adapter = tu.get_runtime_adapter("cursor")
    sessions = list(adapter.iter_sessions(project_dir=None))
    assert [s.composer_id for s in sessions[:2]] == ["comp-recent-a", "comp-recent-b"]


def test_parse_extracts_title_model_and_partial_tokens(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    db_path = build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))

    adapter = tu.get_runtime_adapter("cursor")
    source = tu.CursorSession(
        composer_id="comp-usage-001",
        source="sqlite",
        db_path=db_path,
    )
    result = adapter.parse(source)
    assert result["measurement"] == "partial"
    segs = result["segments"]
    assert len(segs) == 2
    assert segs[0]["label"] == "Refactor token parser"
    assert segs[0]["prompt"].startswith("Add Cursor adapter")
    m0 = segs[0]["by_model"]["claude-sonnet-4"]
    assert m0["input"] == 1200
    assert m0["output"] == 180
    assert m0["cache_read"] == 400
    assert m0["requests"] == 1
    m1 = segs[1]["by_model"]["claude-sonnet-4"]
    assert m1["input"] == 800
    assert m1["output"] == 90
    assert m1["requests"] == 1


def test_legacy_bubble_token_fields(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    headers = [
        {"bubbleId": "legacy-user", "type": 1},
        {"bubbleId": "legacy-asst", "type": 2},
    ]
    db_path = build_cursor_tree(cursor_root, bubble_headers=headers)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))

    adapter = tu.get_runtime_adapter("cursor")
    source = tu.CursorSession("comp-usage-001", "sqlite", db_path)
    result = adapter.parse(source)
    bucket = result["segments"][0]["by_model"]["gpt-4o"]
    assert bucket["input"] == 50
    assert bucket["output"] == 25
    assert result["measurement"] == "partial"


def test_zero_and_missing_tokens_are_activity_only(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    zero_headers = [
        {"bubbleId": "zero-user", "type": 1},
        {"bubbleId": "zero-asst", "type": 2},
    ]
    build_cursor_tree(
        cursor_root,
        composer_id="comp-zero",
        bubble_headers=zero_headers,
    )
    missing_headers = [
        {"bubbleId": "missing-user", "type": 1},
        {"bubbleId": "missing-asst", "type": 2},
    ]
    build_cursor_tree(
        cursor_root,
        composer_id="comp-missing",
        bubble_headers=missing_headers,
        workspace_id="ws-missing",
        project_folder=str((tmp_path / "missing").resolve()),
    )
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))

    adapter = tu.get_runtime_adapter("cursor")
    for cid in ("comp-zero", "comp-missing"):
        db_path = cursor_root / "globalStorage" / "state.vscdb"
        result = adapter.parse(tu.CursorSession(cid, "sqlite", db_path))
        assert result["measurement"] == "activity_only"
        total = tu.sum_buckets(result["segments"][0]["by_model"])
        assert total["input"] == 0
        assert total["output"] == 0
        assert total["requests"] == 1


def test_cloud_export_activity_only_without_guessed_tokens(tu, tmp_path):
    export_path = tmp_path / "cloud-export.json"
    export_path.write_text(
        (FIXTURES / "cloud-export.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    adapter = tu.get_runtime_adapter("cursor")
    source = tu.CursorSession(
        composer_id="cloud-run-001",
        source="cloud_export",
        export_path=export_path,
    )
    result = adapter.parse(source)
    assert result["measurement"] == "activity_only"
    assert len(result["segments"]) == 2
    assert all(
        tu.sum_buckets(s["by_model"])["input"] == 0
        and tu.sum_buckets(s["by_model"])["output"] == 0
        and tu.sum_buckets(s["by_model"])["requests"] >= 1
        for s in result["segments"]
    )
    assert result["segments"][0]["label"] == "Cloud agent documentation pass"


def cloud_export_source(tu, tmp_path, fixture, composer_id):
    export_path = tmp_path / fixture
    export_path.write_text((FIXTURES / fixture).read_text(encoding="utf-8"),
                           encoding="utf-8")
    return tu.CursorSession(composer_id=composer_id, source="cloud_export",
                            export_path=export_path)


def test_cloud_export_with_explicit_usage_is_partial_not_activity_only(tu, tmp_path):
    # An export that states usage for some turns HAS measured tokens: labelling
    # the whole session activity_only nulls the cost and contradicts the very
    # token counts the report prints.
    source = cloud_export_source(tu, tmp_path, "cloud-export-usage.json",
                                 "cloud-run-002")
    result = tu.get_runtime_adapter("cursor").parse(source)
    assert result["measurement"] == "partial"
    total = dict(tu.empty_usage())
    for seg in result["segments"]:
        for k, v in tu.sum_buckets(seg["by_model"]).items():
            total[k] += v
    assert total["output"] == 120
    assert total["input"] == 400
    assert total["cache_read"] == 900


def test_cloud_export_parser_returns_saw_tokens_signal(tu, tmp_path):
    measured = cloud_export_source(tu, tmp_path, "cloud-export-usage.json",
                                   "cloud-run-002")
    unmeasured = cloud_export_source(tu, tmp_path, "cloud-export.json",
                                     "cloud-run-001")
    segments, saw_tokens = tu._cursor_parse_cloud_export(measured, [])
    assert saw_tokens is True
    assert len(segments) == 2
    segments, saw_tokens = tu._cursor_parse_cloud_export(unmeasured, [])
    assert saw_tokens is False
    assert len(segments) == 2


def test_cloud_export_partial_session_keeps_a_cost(tu, tmp_path):
    source = cloud_export_source(tu, tmp_path, "cloud-export-usage.json",
                                 "cloud-run-002")
    adapter = tu.get_runtime_adapter("cursor")
    parsed = adapter.parse(source)
    data = tu.aggregate(parsed["segments"], tu.load_pricing())
    data = tu.apply_measurement_costs(data, parsed["measurement"])
    assert data["total"]["cost_usd"] is not None
    assert data["total"]["cost_usd"] > 0


def test_cloud_export_unreadable_still_returns_the_signal_pair(tu, tmp_path):
    missing = tu.CursorSession(composer_id="cloud-run-404", source="cloud_export",
                               export_path=tmp_path / "absent.json")
    warnings = []
    segments, saw_tokens = tu._cursor_parse_cloud_export(missing, warnings)
    assert segments == []
    assert saw_tokens is False
    assert warnings


def test_sqlite_opened_read_only(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    db_path = build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    conn = tu.open_cursor_db(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO cursorDiskKV VALUES ('x', 'y')")
    conn.close()


def test_missing_bubble_degrades_with_warning(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    headers = [
        {"bubbleId": "user-1", "type": 1},
        {"bubbleId": "ghost-bubble", "type": 2},
    ]
    db_path = build_cursor_tree(cursor_root, bubble_headers=headers)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))

    adapter = tu.get_runtime_adapter("cursor")
    result = adapter.parse(tu.CursorSession("comp-usage-001", "sqlite", db_path))
    assert any("ghost-bubble" in w for w in result["warnings"])
    assert result["measurement"] in ("partial", "activity_only")


def test_ledger_dir_env_is_honored_in_process(tu, tmp_path, monkeypatch):
    # LEDGER_DIR used to bind at import, so an in-process test read the
    # developer's own ~/.cache/token-usage/cursor instead of its fixture.
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    path = write_ledger(tu, tmp_path / "cache", "conv-env", [
        ledger_record("beforeSubmitPrompt", conversation_id="conv-env",
                      prompt="env ledger"),
    ])
    sessions = list(tu.get_runtime_adapter("cursor").iter_sessions())
    assert [(s.source, s.ledger_path) for s in sessions] == [("hook_ledger", path)]


def test_external_home_ledger_cannot_alter_fixture_expectations(tu, tmp_path, monkeypatch):
    # A real user's ledger under $HOME must never join a fixture-scoped scan:
    # the suite pins TOKEN_USAGE_LEDGER_DIR at a tmp dir for every test.
    home = tmp_path / "home"
    write_ledger(tu, home / ".cache" / "token-usage", "real-user-conversation", [
        ledger_record("beforeSubmitPrompt", conversation_id="real-user-conversation",
                      prompt="private work"),
        ledger_record("stop", conversation_id="real-user-conversation",
                      model="claude-sonnet-4",
                      tokens={"input_tokens": 999999, "output_tokens": 999999}),
    ])
    monkeypatch.setenv("HOME", str(home))
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    sessions = list(tu.get_runtime_adapter("cursor").iter_sessions())
    assert [s.composer_id for s in sessions] == ["comp-usage-001"]


def test_ledger_and_sqlite_for_one_conversation_yield_one_session(tu, tmp_path, monkeypatch):
    # A hook-captured conversation and Cursor's own composer row for it are
    # the same session; counting both doubled its cost in every corpus scan.
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    write_ledger(tu, tmp_path / "cache", "comp-usage-001", [
        ledger_record("beforeSubmitPrompt", conversation_id="comp-usage-001",
                      prompt="hook-captured turn"),
        ledger_record("stop", conversation_id="comp-usage-001",
                      model="claude-sonnet-4", tokens={"output_tokens": 10}),
    ])
    sessions = list(tu.get_runtime_adapter("cursor").iter_sessions())
    assert [(s.composer_id, s.source) for s in sessions] == [
        ("comp-usage-001", "hook_ledger")]


def test_legacy_ledger_without_conversation_id_is_still_discovered(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    path = write_ledger(tu, tmp_path / "cache", "conv-old", [
        {"hook": "beforeSubmitPrompt", "generation_id": "g1", "prompt": "old shape"},
    ])
    sessions = list(tu.get_runtime_adapter("cursor").iter_sessions())
    assert [(s.composer_id, s.ledger_path) for s in sessions] == [(path.stem, path)]


def test_ledgers_are_ordered_by_recency_not_filename(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    old = write_ledger(tu, tmp_path / "cache", "aaa-newest", [
        ledger_record("beforeSubmitPrompt", conversation_id="aaa-newest", prompt="a")])
    new = write_ledger(tu, tmp_path / "cache", "zzz-older", [
        ledger_record("beforeSubmitPrompt", conversation_id="zzz-older", prompt="z")])
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))
    adapter = tu.get_runtime_adapter("cursor")
    assert [s.composer_id for s in adapter.iter_sessions()] == ["zzz-older", "aaa-newest"]
    assert adapter.locate().composer_id == "zzz-older"


def test_ledger_session_takes_project_identity_from_workspace_roots(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    project = tmp_path / "repo"
    project.mkdir()
    write_ledger(tu, tmp_path / "cache", "conv-proj", [
        ledger_record("beforeSubmitPrompt", conversation_id="conv-proj",
                      workspace_roots=[str(project)], prompt="work"),
    ])
    adapter = tu.get_runtime_adapter("cursor")
    session = next(iter(adapter.iter_sessions()))
    assert session.project_path == project.resolve()
    assert adapter.project(session) == tu.project_slug(str(project.resolve()))
    assert [s.composer_id for s in adapter.iter_sessions(project_dir=str(project))] \
        == ["conv-proj"]
    assert list(adapter.iter_sessions(project_dir=str(tmp_path / "elsewhere"))) == []


def test_ledger_without_workspace_roots_keeps_the_hooks_project(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    write_ledger(tu, tmp_path / "cache", "conv-rootless", [
        ledger_record("beforeSubmitPrompt", conversation_id="conv-rootless", prompt="x"),
    ])
    adapter = tu.get_runtime_adapter("cursor")
    assert adapter.project(next(iter(adapter.iter_sessions()))) == "cursor-hooks"


def drift_db(cursor_root):
    """A state.vscdb whose cursorDiskKV table is absent (Cursor schema drift)."""
    db_path = cursor_root / "globalStorage" / "state.vscdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    conn.commit()
    conn.close()
    return db_path


def test_schema_drift_warns_and_degrades_instead_of_raising(tu, tmp_path, monkeypatch, capsys):
    cursor_root = tmp_path / "cursor-user"
    db_path = drift_db(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    adapter = tu.get_runtime_adapter("cursor")
    assert list(adapter.iter_sessions()) == []
    assert "cursorDiskKV" in capsys.readouterr().err
    result = adapter.parse(tu.CursorSession("comp-usage-001", "sqlite", db_path))
    assert result["measurement"] == "activity_only"
    assert result["segments"] == []
    assert result["warnings"]


def test_discovery_reads_only_composer_rows(tu, tmp_path, monkeypatch):
    # Discovery used to SELECT every cursorDiskKV row — i.e. pull every bubble
    # blob in the database through memory — and then re-query each key.
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    statements = []
    real_open = tu.open_cursor_db

    class Recording:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            statements.append(sql)
            return self._conn.execute(sql, *args)

        def close(self):
            self._conn.close()

    monkeypatch.setattr(tu, "open_cursor_db", lambda p: Recording(real_open(p)))
    sessions = list(tu.get_runtime_adapter("cursor").iter_sessions())
    assert [s.composer_id for s in sessions] == ["comp-usage-001"]
    assert len(statements) == 1, statements
    assert "composerData:%" in statements[0]


def test_session_id_probe_fails_closed_for_an_unknown_composer(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    adapter = tu.get_runtime_adapter("cursor")
    assert adapter.locate(session_id="comp-not-in-this-database") is None
    assert adapter.locate(session_id="comp-usage-001").composer_id == "comp-usage-001"


def test_session_id_resolves_a_hook_ledger_conversation(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    write_ledger(tu, tmp_path / "cache", "conv-by-id", [
        ledger_record("beforeSubmitPrompt", conversation_id="conv-by-id", prompt="x"),
    ])
    source = tu.get_runtime_adapter("cursor").locate(session_id="conv-by-id")
    assert source is not None
    assert (source.source, source.composer_id) == ("hook_ledger", "conv-by-id")


def test_bubble_type_used_when_header_type_is_null(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    headers = [{"bubbleId": "user-1", "type": None}, {"bubbleId": "asst-1", "type": None}]
    db_path = build_cursor_tree(cursor_root, bubble_headers=headers)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    result = tu.get_runtime_adapter("cursor").parse(
        tu.CursorSession("comp-usage-001", "sqlite", db_path))
    assert result["measurement"] == "partial"
    assert result["segments"][0]["by_model"]["claude-sonnet-4"]["output"] == 180
