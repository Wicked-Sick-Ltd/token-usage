"""A non-string Gemini ``project`` must not abort a corpus scan.

Legacy snapshots and JSONL recordings are discovered when ``sessionId`` and
``projectHash`` are strings. The project name is then used as a grouping key.
These tests plant one ordinary string-project chat beside a crafted one and
require every corpus command that groups or filters on that name to finish
and still report the ordinary chat.

A crafted session may be omitted, or kept under its ``projectHash``. Its
project value must not be stored or grouped as a list, object, or number.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import SCRIPT

GOOD_OUTPUT = 20
BAD_OUTPUT = 9


def _recent(seconds_ago):
    moment = datetime.now(timezone.utc) - timedelta(days=1, seconds=seconds_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# Inside the 30-day window used by dashboard, day history, top consumers,
# insights, and the CLI dashboard default. A fixed calendar day falls out of
# that window and the ordinary chat disappears even after grouping is fixed.
TS_USER = _recent(1)
TS_MODEL = _recent(0)

KINDS = {
    "array": ["p"],
    "object": {"name": "p"},
    "number": 7,
}


def _messages(output):
    return [
        {"id": "u", "type": "user", "timestamp": TS_USER, "content": "hello"},
        {
            "id": "a",
            "type": "gemini",
            "timestamp": TS_MODEL,
            "model": "gemini-test",
            "tokens": {
                "input": 10,
                "output": output,
                "cached": 0,
                "thoughts": 0,
                "tool": 0,
            },
        },
    ]


def write_chat(root, sid, project, output, legacy=True):
    project_hash = hashlib.sha256(sid.encode()).hexdigest()
    meta = {"sessionId": sid, "projectHash": project_hash, "project": project}
    path = root / "tmp" / sid / "chats" / (sid + (".json" if legacy else ".jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    messages = _messages(output)
    if legacy:
        body = dict(meta, messages=messages)
        path.write_text(json.dumps(body), encoding="utf-8")
    else:
        rows = [meta, *messages]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path, project_hash


def plant(tmp_path, monkeypatch, kind, legacy=True, include_good=True):
    root = tmp_path / "gemini"
    monkeypatch.setenv("TOKEN_USAGE_GEMINI_HOME", str(root))
    monkeypatch.delenv("TOKEN_USAGE_TRANSCRIPT", raising=False)
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    bad_path, bad_hash = write_chat(
        root, "crafted", KINDS[kind], BAD_OUTPUT, legacy=legacy)
    if include_good:
        write_chat(root, "good", "workspace", GOOD_OUTPUT, legacy=legacy)
    return bad_path, bad_hash


def assert_workspace_preserved(rows, bad_hash):
    """The ordinary chat is reported, and every grouped name is a string.

    The crafted chat is either absent or grouped under its project hash.
    """
    assert all(isinstance(row["key"], str) for row in rows)
    good = [row for row in rows if row["key"] == "workspace"]
    assert len(good) == 1
    assert good[0]["usage"]["output"] == GOOD_OUTPUT
    for row in rows:
        if row["key"] == "workspace":
            continue
        assert row["key"] == bad_hash
        assert row["usage"]["output"] == BAD_OUTPUT


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("legacy", [True, False])
def test_project_name_is_a_string_or_the_hash(tu, tmp_path, monkeypatch, kind, legacy):
    bad_path, bad_hash = plant(tmp_path, monkeypatch, kind, legacy=legacy)
    adapter = tu.get_runtime_adapter("gemini")
    assert adapter.project(bad_path) == bad_hash
    # The ordinary chat is planted too, so a non-empty list does not prove the
    # crafted snapshot was discovered.
    found = {Path(path).resolve() for path in adapter.iter_sessions()}
    assert bad_path.resolve() in found


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("legacy", [True, False])
def test_history_by_project_reports_the_other_session(tu, tmp_path, monkeypatch, kind, legacy):
    _path, bad_hash = plant(tmp_path, monkeypatch, kind, legacy=legacy)
    data = tu.run_history(by="project", runtime="gemini")
    assert_workspace_preserved(data["rows"], bad_hash)


def test_one_array_snapshot_does_not_abort_history(tu, tmp_path, monkeypatch):
    """A single crafted chat, with no sibling, still has to finish the scan."""
    _path, bad_hash = plant(
        tmp_path, monkeypatch, "array", legacy=True, include_good=False)
    data = tu.run_history(by="project", runtime="gemini")
    assert all(isinstance(row["key"], str) for row in data["rows"])
    assert data["rows"] == [] or (
        len(data["rows"]) == 1
        and data["rows"][0]["key"] == bad_hash
        and data["rows"][0]["usage"]["output"] == BAD_OUTPUT
    )


@pytest.mark.parametrize("kind", ["array", "object"])
@pytest.mark.parametrize("legacy", [True, False])
def test_partial_cost_pass_groups_a_non_string_project(tu, tmp_path, monkeypatch, kind, legacy):
    """Dashboard marks unpriced rows with the project name as a dict key.

    ``gemini-test`` has no price, so this pass indexes ``project`` directly.
    """
    _path, bad_hash = plant(tmp_path, monkeypatch, kind, legacy=legacy)
    data = {
        "top_projects": [{"key": "workspace", "usage": {"output": GOOD_OUTPUT}}],
        "top_commands": [],
    }
    scan = {
        "since": "30d",
        "project": None,
        "runtime": "gemini",
        "corpus_project_dir": None,
    }
    tu._enrich_dashboard_partials(data, scan, [])
    # gemini-test is unpriced, so the ordinary chat has to be marked. A pass
    # that swallows the scan and returns the seed unchanged does not count.
    marked = [row["key"] for row in data["top_projects"] if row.get("partial")]
    assert marked == ["workspace"]
    assert bad_hash not in marked


@pytest.mark.parametrize("kind", list(KINDS))
def test_dashboard_reports_the_other_session(tu, tmp_path, monkeypatch, kind):
    _path, bad_hash = plant(tmp_path, monkeypatch, kind)
    data = tu.dashboard_data(runtime="gemini", since="30d")
    assert_workspace_preserved(data["top_projects"], bad_hash)


@pytest.mark.parametrize("kind", list(KINDS))
def test_history_export_reports_the_other_session(tu, tmp_path, monkeypatch, kind):
    _path, bad_hash = plant(tmp_path, monkeypatch, kind)
    data, _counts = tu.history_export_data(by="project", runtime="gemini")
    assert_workspace_preserved(data["rows"], bad_hash)


def test_numeric_project_filter_does_not_abort_other_scans(tu, tmp_path, monkeypatch):
    """``project in name`` runs before grouping. A number must not raise there.

    Day history, top consumers, window insights, and the dashboard all share
    that filter. Each must still report the ordinary chat.
    """
    plant(tmp_path, monkeypatch, "number")
    history = tu.run_history(by="day", project="workspace", since="30d", runtime="gemini")
    assert history["rows"], "day history dropped the ordinary chat"
    assert all(isinstance(row["key"], str) for row in history["rows"])

    consumers = tu.run_top_consumers(
        by="session", since="30d", project="workspace", runtime="gemini")
    assert [row["project"] for row in consumers["rows"]] == ["workspace"]
    assert consumers["rows"][0]["usage"]["output"] == GOOD_OUTPUT

    insights = tu.run_insights(since="30d", project="workspace", runtime="gemini")
    assert insights["baseline"]["sessions"] >= 1

    dashboard = tu.dashboard_data(runtime="gemini", since="30d", project="workspace")
    assert [row["key"] for row in dashboard["top_projects"]] == ["workspace"]
    assert dashboard["top_projects"][0]["usage"]["output"] == GOOD_OUTPUT


def _cli(args, env):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize("kind", ["array", "number"])
@pytest.mark.parametrize("command", ["history", "dashboard", "export"])
def test_cli_corpus_commands_print_the_other_session(tmp_path, monkeypatch, kind, command):
    plant(tmp_path, monkeypatch, kind)
    env = dict(os.environ, PYTHONUTF8="1")
    if command == "history":
        result = _cli(["history", "--runtime", "gemini"], env)
        assert result.returncode == 0, result.stderr
        assert "| workspace |" in result.stdout
        assert "Traceback" not in result.stderr
    elif command == "dashboard":
        output = tmp_path / "dashboard.html"
        result = _cli(
            ["dashboard", "--runtime", "gemini", "--output", str(output)], env)
        assert result.returncode == 0, result.stderr
        assert output.is_file()
        text = output.read_text(encoding="utf-8")
        assert "workspace" in text
        assert "Traceback" not in result.stderr
    else:
        result = _cli(["export", "--runtime", "gemini", "--scope", "history"], env)
        assert result.returncode == 0, result.stderr
        assert '"workspace"' in result.stdout
        assert "Traceback" not in result.stderr


@pytest.mark.parametrize("kind", ["array", "number"])
def test_mcp_history_reports_the_other_session(mcp, tmp_path, monkeypatch, kind):
    _path, bad_hash = plant(tmp_path, monkeypatch, kind)
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "history",
            "arguments": {"runtime": "gemini", "by": "project"},
        },
    }
    response = mcp.handle_message(message)
    result = response["result"]
    assert result["isError"] is False, result["content"][0]["text"]
    data = json.loads(result["content"][0]["text"])
    assert_workspace_preserved(data["rows"], bad_hash)
