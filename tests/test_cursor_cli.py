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
    # build_cursor_tree's bubbles carry usable tokenCount, so this corpus is
    # deterministically partial. Accepting any of the three levels asserted
    # nothing at all.
    assert data["measurement"] == "partial"
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
    assert "--runtime" in r.stderr
    assert "gemini" in r.stderr


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


def test_default_claude_json_keeps_its_pre_runtime_shape(tmp_path):
    # Routing Claude through the adapter seam added runtime/measurement to a
    # payload that never carried them. Cursor discloses both (it has to); a
    # default Claude run must still look like it did before runtimes existed.
    from conftest import assistant, usage, user, write_jsonl

    proj = tmp_path / "projects"
    t = write_jsonl(proj / "p" / "s.jsonl", [
        user("2026-06-10T10:00:00Z", command="/go"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "json", str(t)],
        capture_output=True, text=True, check=False,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(proj)),
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert "runtime" not in data
    assert "measurement" not in data
    # README: MCP JSON is "the CLI's JSON shapes plus transcript, resolved_via
    # and warnings" — so warnings belongs to the MCP envelope, not here.
    assert "warnings" not in data
    assert data["transcript_path"] == str(t)
    assert data["by_label"]


def zero_token_tree(tmp_path, monkeypatch, project_folder=None):
    """A Cursor corpus whose only session has no measurable tokens."""
    cursor_root = tmp_path / "cursor-user"
    headers = [{"bubbleId": "zero-user", "type": 1}, {"bubbleId": "zero-asst", "type": 2}]
    build_cursor_tree(
        cursor_root,
        composer_id="comp-zero",
        bubble_headers=headers,
        project_folder=project_folder or str((tmp_path / "alpha-repo").resolve()),
    )
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    return cursor_root


def test_cursor_project_filter_is_a_slug_substring_not_a_path(tu, tmp_path, monkeypatch):
    # --project took one value as both a filesystem discovery path and a slug
    # filter, so every Cursor corpus query with --project came back empty.
    project = tmp_path / "alpha-repo"
    project.mkdir()
    zero_token_tree(tmp_path, monkeypatch, project_folder=str(project))
    data = tu.run_history(by="project", project="alpha-repo", runtime="cursor")
    assert [r["key"] for r in data["rows"]] == [tu.project_slug(str(project.resolve()))]
    assert tu.run_history(by="project", project="other-repo", runtime="cursor")["rows"] == []


def test_cursor_corpus_project_dir_hint_still_scopes_discovery(tu, tmp_path, monkeypatch):
    project = tmp_path / "alpha-repo"
    project.mkdir()
    zero_token_tree(tmp_path, monkeypatch, project_folder=str(project))
    scoped = tu.run_history(by="project", runtime="cursor",
                            corpus_project_dir=str(project))
    assert len(scoped["rows"]) == 1
    elsewhere = tu.run_history(by="project", runtime="cursor",
                               corpus_project_dir=str(tmp_path / "beta-repo"))
    assert elsewhere["rows"] == []


def test_cursor_corpus_json_discloses_measurement(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    data = tu.run_history(by="project", runtime="cursor")
    assert data["measurements"] == {"activity_only": 1}
    top = tu.run_top_consumers(since="2020-01-01", runtime="cursor")
    assert top["measurements"] == {"activity_only": 1}
    window = tu.run_insights(since="2020-01-01", runtime="cursor")
    assert window["measurements"] == {"activity_only": 1}


def test_cursor_history_markdown_discloses_activity_only(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    out = tu.render_history(tu.run_history(by="project", runtime="cursor"))
    assert "activity-only" in out
    assert "unmeasured" in out


def test_cursor_top_consumers_markdown_discloses_activity_only(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    out = tu.render_top_consumers(tu.run_top_consumers(since="2020-01-01",
                                                       runtime="cursor"))
    assert "activity-only" in out


def test_cursor_insights_markdown_discloses_activity_only(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    out = tu.render_insights(tu.run_insights(since="2020-01-01", runtime="cursor"))
    assert "activity-only" in out


def test_cursor_report_markdown_discloses_measurement_and_warnings(tu):
    segments = [{"label": "Refactor token parser", "start_ts": "2026-06-12T10:00:00Z",
                 "by_model": {"claude-sonnet-4": dict(tu.empty_usage(), requests=1)},
                 "prompt": "hi", "subagents": []}]
    data = tu.aggregate(segments, tu.load_pricing())
    data = tu.apply_measurement_costs(data, "activity_only")
    data["transcript_path"] = "composer:comp-usage-001"
    data["measurement"] = "activity_only"
    data["warnings"] = ["missing Cursor bubble 'ghost' for composer 'comp-usage-001'"]
    out = tu.render_report(data)
    assert "activity-only" in out
    assert "ghost" in out
    # A composer name is not a path: it must not be rendered as "/composer:...".
    assert "Session: `composer:comp-usage-001`" in out
    assert "/composer:" not in out


def test_cursor_partial_report_markdown_names_partial(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    adapter = tu.get_runtime_adapter("cursor")
    source = adapter.locate()
    parsed = adapter.parse(source)
    data = tu.aggregate(parsed["segments"], tu.load_pricing())
    data["measurement"] = parsed["measurement"]
    out = tu.render_report(data)
    assert "Measurement: partial" in out


def test_cursor_insights_session_markdown_names_the_composer_not_a_path(
    tu, tmp_path, monkeypatch,
):
    # render_report was taught that an adapter label is not a path; insights
    # still split it with Path(), so the same session read as "/composer:...".
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    adapter = tu.get_runtime_adapter("cursor")
    out = tu.render_insights(tu.run_insights(transcript=adapter.locate(),
                                             runtime="cursor"))
    assert "(session: composer:comp-usage-001)" in out
    assert "/composer:" not in out


def test_claude_insights_still_names_project_and_transcript(tu, tmp_path, monkeypatch):
    from conftest import assistant, usage, user, write_jsonl

    proj = tmp_path / "projects"
    t = write_jsonl(proj / "alpha" / "s.jsonl", [
        user("2026-06-10T10:00:00Z", command="/go"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    out = tu.render_insights(tu.run_insights(transcript=t))
    assert "(session: alpha/s.jsonl)" in out


def test_missing_cursor_root_footnote_names_cursor_not_claude(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "no-cursor"))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    data = tu.run_history(by="project", runtime="cursor")
    out = tu.render_history(data)
    assert data["projects_dir_missing"] == str((tmp_path / "no-cursor").resolve())
    assert "Claude Code projects directory" not in out
    assert "Cursor" in out and "nothing was scanned" in out


def test_auto_runtime_bogus_selector_is_a_clean_error_not_a_traceback(tmp_path):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "report", "--runtime", "auto",
         "comp-not-a-file-on-disk"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root,
                 TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "no-claude")),
        check=False,
    )
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
    assert "CursorExplicitSelectorError" not in r.stderr
    assert ".json" in r.stderr.lower()


def corrupt_cursor_db(cursor_root):
    """A state.vscdb that is not a SQLite database at all."""
    db = cursor_root / "globalStorage" / "state.vscdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is not a database\n")
    return db


def test_auto_corpus_probe_survives_a_corrupt_cursor_database(tmp_path):
    from conftest import assistant, usage, user, write_jsonl

    proj = tmp_path / "projects"
    write_jsonl(proj / "p" / "s.jsonl", [
        user("2026-06-10T10:00:00Z", command="/go"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    cursor_root = tmp_path / "cursor-user"
    corrupt_cursor_db(cursor_root)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "history", "--runtime", "auto", "--json"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root, TOKEN_USAGE_PROJECTS_DIR=str(proj)),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    data = json.loads(r.stdout)
    assert "runtime" not in data
    assert len(data["rows"]) == 1


def hook_ledger_corpus(tu, tmp_path, monkeypatch, project=None):
    """A Cursor corpus whose only session is a dated hook ledger."""
    from test_cursor_adapter import ledger_record, write_ledger

    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(tmp_path / "cursor-user"))
    roots = [str(project)] if project else None
    extra = {"workspace_roots": roots} if roots else {}
    return write_ledger(tu, tmp_path / "cache", "conv-window", [
        ledger_record("beforeSubmitPrompt", conversation_id="conv-window",
                      ts="2026-06-12T10:00:00Z", prompt="ship the adapter", **extra),
        ledger_record("stop", conversation_id="conv-window", ts="2026-06-12T10:00:30Z",
                      model="claude-sonnet-4",
                      tokens={"input_tokens": 500, "output_tokens": 100}, **extra),
    ])


def test_hook_ledger_session_respects_since_and_day_windows(tu, tmp_path, monkeypatch):
    hook_ledger_corpus(tu, tmp_path, monkeypatch)
    inside = tu.run_history(by="day", since="2026-01-01", runtime="cursor")
    assert [r["key"] for r in inside["rows"]] == [tu._local_day("2026-06-12T10:00:00Z")]
    assert inside["rows"][0]["usage"]["output"] == 100
    outside = tu.run_history(by="day", since="2026-07-01", runtime="cursor")
    assert outside["rows"] == []


def test_hook_ledger_top_consumer_uses_the_conversation_id(tu, tmp_path, monkeypatch):
    # The row used to be keyed by the hashed ledger filename stem, which is
    # not an id anything else in the tool accepts.
    hook_ledger_corpus(tu, tmp_path, monkeypatch)
    data = tu.run_top_consumers(by="session", since="2026-01-01", runtime="cursor")
    assert [r["session_id"] for r in data["rows"]] == ["conv-window"]
    assert data["measurements"] == {"exact": 1}


def test_hook_ledger_history_rolls_up_under_its_workspace_project(tu, tmp_path,
                                                                  monkeypatch):
    project = tmp_path / "alpha-repo"
    project.mkdir()
    hook_ledger_corpus(tu, tmp_path, monkeypatch, project=project)
    data = tu.run_history(by="project", runtime="cursor")
    assert [r["key"] for r in data["rows"]] == [tu.project_slug(str(project.resolve()))]
