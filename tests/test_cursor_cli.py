"""CLI routing for --runtime cursor and runtime validation."""
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import SCRIPT
from test_cursor_adapter import build_cursor_tree


def _env(tmp_path, cursor_root=None, **extra):
    env = {
        **os.environ,
        "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", str(tmp_path / "xdg")),
    }
    if cursor_root is not None:
        env["TOKEN_USAGE_CURSOR_DIR"] = str(cursor_root)
    env.update(extra)
    return env


def test_report_runtime_cursor_subprocess(tmp_path):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "report", "--runtime", "cursor"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "Refactor token parser" in r.stdout
    assert "Total" in r.stdout


def test_report_runtime_cursor_cloud_export_positional(tmp_path):
    export = tmp_path / "cloud-export.json"
    export.write_text(
        (Path(__file__).resolve().parent / "fixtures" / "cursor" / "cloud-export.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "report", "--runtime", "cursor", str(export)],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "Cloud agent documentation pass" in r.stdout


def test_json_runtime_cursor_includes_runtime_fields(tmp_path):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "json", "--runtime", "cursor"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["runtime"] == "cursor"
    assert data["measurement"] in ("partial", "exact", "activity_only")
    assert isinstance(data["warnings"], list)
    assert "by_label" in data


def test_explicit_bogus_selector_fails_closed_not_latest_session(tmp_path):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    bogus_selector = "comp-not-a-file-on-disk"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "report",
            "--runtime",
            "cursor",
            bogus_selector,
        ],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root),
        check=False,
    )
    assert r.returncode != 0, r.stdout
    assert "Refactor token parser" not in r.stdout
    assert ".json" in r.stderr.lower()


def test_cursor_no_session_error_documents_json_or_discovery_not_composer_positional(
    tmp_path,
):
    cursor_root = tmp_path / "cursor-empty"
    cursor_root.mkdir()
    bogus_selector = "comp-not-a-file-on-disk"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "report",
            "--runtime",
            "cursor",
            bogus_selector,
        ],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root),
        check=False,
    )
    assert r.returncode != 0
    err = r.stderr.lower()
    assert "export not found" in err
    assert ".json" in err
    assert "session_id" in err


def test_invalid_runtime_rejected(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "report", "--runtime", "gemini"],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        check=False,
    )
    assert r.returncode != 0
    assert "runtime" in r.stderr.lower() or "gemini" in r.stderr


def test_auto_session_ambiguous_when_both_corpora_match(tmp_path):
    from conftest import assistant, usage, user, write_jsonl

    proj = tmp_path / "projects"
    t = write_jsonl(proj / "p" / "s.jsonl", [
        user("2026-06-10T10:00:00Z", command="/go"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    env = _env(
        tmp_path,
        cursor_root,
        TOKEN_USAGE_PROJECTS_DIR=str(proj),
        TOKEN_USAGE_TRANSCRIPT=str(t),
    )
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "json", "--runtime", "auto"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode != 0
    assert "ambiguous" in r.stderr.lower()


def test_auto_corpus_ambiguous_subprocess(tmp_path):
    from conftest import assistant, usage, user, write_jsonl

    proj = tmp_path / "projects"
    write_jsonl(proj / "p" / "s.jsonl", [
        user("2026-06-10T10:00:00Z", command="/go"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    env = _env(tmp_path, cursor_root, TOKEN_USAGE_PROJECTS_DIR=str(proj))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "history", "--runtime", "auto", "--json"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode != 0
    assert "ambiguous" in r.stderr.lower()


def test_auto_resolves_cursor_only_history(tmp_path):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    env = _env(
        tmp_path,
        cursor_root,
        TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "no-claude"),
    )
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "history", "--runtime", "auto", "--json"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data.get("runtime") == "cursor"


def test_default_runtime_is_claude(tu, tmp_path, monkeypatch):
    from conftest import assistant, usage, user, write_jsonl

    proj = tmp_path / "projects"
    t = write_jsonl(proj / "p" / "s.jsonl", [
        user("2026-06-10T10:00:00Z", command="/go"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "json", str(t)],
        capture_output=True,
        text=True,
        env={**os.environ, "TOKEN_USAGE_PROJECTS_DIR": str(proj),
             "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "cache"),
             "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", str(tmp_path / "xdg"))},
        check=False,
    )
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data.get("runtime", "claude") == "claude"
