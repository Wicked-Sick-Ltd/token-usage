"""A non-string Claude session timestamp must not abort a windowed corpus scan.

The opening user turn is what the summary stores as first_ts. The day rollup's
fallback parses whichever timestamp appears first in the file, and that call
sits inside the per-transcript handler (OSError, ValueError, AttributeError).
An assistant line with an ISO timestamp and no usage does not open a segment,
so the file still parses, while the user turn's numeric or boolean timestamp
is what the cutoff comparison sees. That comparison is outside the handler.
One such file must not stop history, dashboard, insights, top-consumers, or
export, and the sessions that do parse must still be reported.

These tests fail while that comparison can raise TypeError. Adapter runtimes
are not covered: their summaries run the day parse on the same start_ts they
store, so a non-string is rejected inside the per-session handler.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import assistant, usage, user, write_jsonl

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "token_usage.py"
HEALTHY = "healthy-proj"
POISON = "poison-proj"
HEALTHY_OUTPUT = 1000


def seed(tmp_path, monkeypatch, bad_timestamp):
    proj = tmp_path / "projects"
    write_jsonl(proj / HEALTHY / "ok.jsonl", [
        user("2026-11-15T12:00:00Z", text="hello healthy"),
        assistant("2026-11-15T12:00:01Z", usage(out=HEALTHY_OUTPUT), request_id="h1"),
    ])
    # The preamble timestamp is a string so the day-rollup fallback does not
    # reject the file. It carries no usage, so it does not open a segment:
    # the stored first_ts stays the user turn's non-string value.
    write_jsonl(proj / POISON / "bad.jsonl", [
        {"type": "assistant", "timestamp": "2026-11-14T00:00:00Z",
         "message": {"role": "assistant", "model": "claude-fable-5",
                     "content": "warmup"}},
        user(bad_timestamp, text="hello poison"),
        assistant("2026-11-15T12:00:02Z", usage(out=10), request_id="p1"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    return proj


def assert_healthy_history(data):
    rows = {r["key"]: r for r in data["rows"]}
    assert HEALTHY in rows
    assert rows[HEALTHY]["usage"]["output"] == HEALTHY_OUTPUT


def assert_timestamp_reusable(ts, cutoff):
    """The value that survived the cutoff filter is still safe to reuse.

    top-consumers slices it, and insights compares it again when splitting
    the window. A guard that only rewrites a local copy of the filter check
    leaves the stored timestamp untouched, and both of those later uses
    raise TypeError for an int or a bool.
    """
    if not ts:
        return
    assert isinstance(ts, str)
    assert ts[:10]
    assert (ts < cutoff) or (ts >= cutoff)


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_history_since_keeps_the_healthy_session(tu, tmp_path, monkeypatch, bad_timestamp):
    seed(tmp_path, monkeypatch, bad_timestamp)
    data = tu.run_history(since="30d")
    assert_healthy_history(data)
    assert tu.render_history(data)


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_dashboard_default_window_keeps_the_healthy_session(tu, tmp_path, monkeypatch,
                                                           bad_timestamp):
    seed(tmp_path, monkeypatch, bad_timestamp)
    data = tu.dashboard_data(since="30d")
    assert data["summary"]["sessions"] >= 1
    assert any(r["key"] == HEALTHY for r in data["top_projects"])
    html_text = tu.render_dashboard(data)
    assert HEALTHY in html_text
    assert "No sessions matched this window." not in html_text


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_top_consumers_reuses_first_ts_after_the_filter(tu, tmp_path, monkeypatch,
                                                       bad_timestamp):
    seed(tmp_path, monkeypatch, bad_timestamp)
    data = tu.run_top_consumers()  # default since is 30d, same as the CLI
    ids = [r["session_id"] for r in data["rows"]]
    assert "ok" in ids
    cutoff = tu.since_cutoff("30d")
    for row in data["rows"]:
        assert_timestamp_reusable(row["first_ts"], cutoff)
    rendered = tu.render_top_consumers(data)
    assert "| ok | healthy-proj |" in rendered


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_insights_window_and_session_baseline_finish(tu, tmp_path, monkeypatch,
                                                    bad_timestamp):
    proj = seed(tmp_path, monkeypatch, bad_timestamp)
    window = tu.run_insights(since="30d")
    assert window["mode"] == "window"
    assert window["baseline"]["sessions"] >= 1
    assert isinstance(window["findings"], list)
    assert tu.render_insights(window)

    session = tu.run_insights(transcript=str(proj / HEALTHY / "ok.jsonl"))
    assert session["mode"] == "session"
    assert session["transcript_path"].endswith("ok.jsonl")
    assert isinstance(session["findings"], list)
    assert tu.render_insights(session)


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_export_since_keeps_the_healthy_session(tu, tmp_path, monkeypatch, bad_timestamp):
    seed(tmp_path, monkeypatch, bad_timestamp)
    data, _counts = tu.history_export_data(since="30d")
    assert_healthy_history(data)
    text = tu.render_jsonl(tu.history_export_records(data))
    assert HEALTHY in text
    for line in text.splitlines():
        json.loads(line)


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_cli_windowed_commands_report_the_healthy_session(tmp_path, monkeypatch,
                                                         bad_timestamp):
    proj = seed(tmp_path, monkeypatch, bad_timestamp)
    env = dict(os.environ)
    dash = tmp_path / "dash.html"
    commands = [
        ["history", "--since", "30d"],
        ["dashboard", "--output", str(dash)],
        ["top_consumers"],
        ["insights", "--since", "30d"],
        ["insights", str(proj / HEALTHY / "ok.jsonl")],
        ["export", "--since", "30d", "--output", "-"],
    ]
    for args in commands:
        proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env, check=False)
        assert proc.returncode == 0, proc.stderr
        if args[0] == "dashboard":
            assert HEALTHY in dash.read_text(encoding="utf-8")
        else:
            assert HEALTHY in proc.stdout


@pytest.mark.parametrize("bad_timestamp", [1, True], ids=["int", "bool"])
def test_mcp_windowed_tools_report_the_healthy_session(mcp, tmp_path, monkeypatch,
                                                      bad_timestamp):
    proj = seed(tmp_path, monkeypatch, bad_timestamp)

    def call(name, **arguments):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}
        result = mcp.handle_message(message)["result"]
        return result["content"][0]["text"], result["isError"]

    text, err = call("history", since="30d")
    assert not err, text
    assert HEALTHY in text

    text, err = call("top_consumers")
    assert not err, text
    assert "healthy-proj" in text and "| ok |" in text

    text, err = call("insights", since="30d")
    assert not err, text

    text, err = call("insights", transcript=str(proj / HEALTHY / "ok.jsonl"))
    assert not err, text
    assert "ok.jsonl" in text
