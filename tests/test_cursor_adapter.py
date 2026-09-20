"""Cursor runtime adapter: synthetic SQLite, discovery, and parsing."""
import json
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


def test_cursor_adapter_registered(tu):
    adapter = tu.get_runtime_adapter("cursor")
    assert adapter.name == "cursor"


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
