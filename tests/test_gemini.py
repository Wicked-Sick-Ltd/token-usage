"""Native Gemini snapshots and append-only recordings; no live user data."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
from conftest import SCRIPT, write_jsonl


def recording(tmp_path, monkeypatch, legacy=False, sid="parent", folder=None):
    root = tmp_path / "gemini"
    monkeypatch.setenv("TOKEN_USAGE_GEMINI_HOME", str(root))
    monkeypatch.delenv("TOKEN_USAGE_TRANSCRIPT", raising=False)
    project = str((tmp_path / "project").resolve())
    meta = {"sessionId": sid, "projectHash": hashlib.sha256(project.encode()).hexdigest()}
    messages = [
        {"id": "u", "type": "user", "timestamp": "2026-10-05T10:00:00Z", "content": "/review fix"},
        {"id": "a", "type": "gemini", "timestamp": "2026-10-05T10:00:01Z", "model": "gemini-test",
         "tokens": {"input": 100, "output": 20, "cached": 40, "thoughts": 7, "tool": 3}},
    ]
    path = (folder or root / "tmp/project/chats") / (sid + (".json" if legacy else ".jsonl"))
    if legacy:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(meta, messages=messages)), encoding="utf-8")
    else:
        write_jsonl(path, [meta, *messages, messages[-1]])
    return path


@pytest.mark.parametrize("legacy", [False, True])
def test_gemini_accounting_discovery_and_mcp(tu, mcp, tmp_path, monkeypatch, legacy):
    path = recording(tmp_path, monkeypatch, legacy)
    adapter = tu.get_runtime_adapter("gemini")
    assert adapter.locate(session_id="parent") == path
    assert adapter.locate(session_id="missing") is None
    assert adapter.locate("missing.json") is None
    assert list(adapter.iter_sessions(tmp_path / "project")) == [path]
    assert list(adapter.iter_sessions(tmp_path / "other")) == []
    assert tu.resolve_runtime("auto", path)[1] == "gemini"
    parsed = adapter.parse(path)
    assert parsed["measurement"] == "exact"
    total = tu.aggregate(parsed["segments"], {})["total"]["usage"]
    assert total == {"input": 63, "output": 27, "cache_read": 40, "cache_5m": 0, "cache_1h": 0, "requests": 1}
    data = json.loads(mcp.tool_session_cost({"runtime": "gemini", "session_id": "parent"}))
    assert data["runtime"] == "gemini"
    assert data["total"]["usage"] == total
    assert data["total"]["cost_usd"] is None
    assert tu.run_history(runtime="gemini")["rows"][0]["usage"] == total


def test_gemini_children_and_growing_cache(tu, tmp_path, monkeypatch):
    parent = recording(tmp_path, monkeypatch)
    child = recording(tmp_path, monkeypatch, sid="child", folder=parent.parent / "parent")
    adapter = tu.get_runtime_adapter("gemini")
    assert list(adapter.iter_sessions()) == [parent]
    first, _ = tu.cached_adapter_summary(adapter, parent, {})
    assert first["total"]["usage"]["output"] == 54
    rows = list(tu.iter_jsonl(child))
    rows[-1] = dict(rows[-1], id="next", tokens={"input": 2, "output": 3, "cached": 0})
    write_jsonl(child, rows)
    second, _ = tu.cached_adapter_summary(adapter, parent, {})
    assert second["total"]["usage"]["output"] == 57
    recording(tmp_path, monkeypatch, sid="grandchild", folder=parent.parent / "child")
    assert list(adapter.iter_sessions()) == [parent]
    family, _ = tu.cached_adapter_summary(adapter, parent, {})
    assert family["total"]["usage"]["output"] == 84


def test_gemini_incomplete_invalid_and_rewound_usage(tu, tmp_path, monkeypatch):
    path = recording(tmp_path, monkeypatch)
    rows = list(tu.iter_jsonl(path))
    rows += [{"$rewindTo": "u"}, {"id": "b", "type": "gemini", "model": "gemini-test"}]
    write_jsonl(path, rows)
    parsed = tu.parse_gemini_session(path)
    assert parsed["measurement"] == "partial"
    assert tu.aggregate(parsed["segments"], {})["total"]["usage"]["output"] == 27
    for bad in (True, -1, "100"):
        rows[2]["tokens"]["input"] = bad
        write_jsonl(path, rows[:3])
        assert tu.parse_gemini_session(path)["measurement"] == "activity_only"


@pytest.mark.parametrize("command", ["report", "json", "insights", "live", "export", "dashboard", "history", "top_consumers"])
def test_gemini_cli(command, tmp_path, monkeypatch):
    path = recording(tmp_path, monkeypatch)
    args = [sys.executable, str(SCRIPT), command, "--runtime", "gemini"]
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


def antigravity_recording(tmp_path, monkeypatch, sid="ag-parent"):
    root = tmp_path / "gemini"
    monkeypatch.setenv("TOKEN_USAGE_GEMINI_HOME", str(root))
    monkeypatch.delenv("TOKEN_USAGE_TRANSCRIPT", raising=False)
    project = str((tmp_path / "project").resolve())
    path = root / "antigravity" / "brain" / sid / ".system_generated" / "logs" / "transcript.jsonl"
    steps = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-18T10:00:00Z",
            "content": (
                "<USER_REQUEST>\n/review check this\n</USER_REQUEST>\n"
                "<USER_SETTINGS_CHANGE>\n"
                "The user changed setting `Model Selection` from None to Gemini 4 Argon (Medium). "
                "No need to comment on this change if the user doesn't ask about it.\n"
                "</USER_SETTINGS_CHANGE>"
            ),
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-18T10:00:01Z",
            "input_tokens": 120,
            "cache_read_tokens": 30,
            "output_tokens": 25,
            "tool_calls": [
                {
                    "name": "run_command",
                    "args": {"Cwd": json.dumps(project), "CommandLine": '"git status"'},
                }
            ],
        },
        {
            "step_index": 2,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-18T10:00:02Z",
            "content": "<CONTEXT_SUMMARY>\nchanged setting `Model Selection` from .*? to ([^\\n<]+)\n</CONTEXT_SUMMARY>",
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-18T10:00:03Z",
            "input_tokens": 40,
            "cache_read_tokens": 150,
            "output_tokens": 15,
            "tool_calls": [
                {
                    "name": "view_file",
                    "args": {"AbsolutePath": json.dumps("/home/dev/.gemini/config/plugins/ponytail/skills/ponytail-review/SKILL.md")},
                }
            ],
        },
    ]
    write_jsonl(path, steps)
    return path


def test_antigravity_accounting_discovery_skills_and_children(tu, mcp, tmp_path, monkeypatch):
    path = antigravity_recording(tmp_path, monkeypatch)
    adapter = tu.get_runtime_adapter("gemini")
    assert adapter.locate(session_id="ag-parent") == path
    assert list(adapter.iter_sessions(tmp_path / "project")) == [path]
    assert list(adapter.iter_sessions(tmp_path / "other")) == []
    assert tu.resolve_runtime("auto", path)[1] == "gemini"
    parsed = adapter.parse(path)
    assert parsed["measurement"] == "exact"
    assert [s["label"] for s in parsed["segments"]] == ["/review", "$ponytail-review"]
    agg = tu.aggregate(parsed["segments"], {})
    assert agg["total"]["usage"] == {
        "input": 160,
        "output": 40,
        "cache_read": 180,
        "cache_5m": 0,
        "cache_1h": 0,
        "requests": 2,
    }
    assert list(agg["total"]["by_model"]) == ["Gemini 4 Argon (Medium)"]

    # Add a linked subagent transcript under the same Antigravity brain root.
    child_path = path.parents[3] / "ag-child" / ".system_generated" / "logs" / "transcript.jsonl"
    write_jsonl(
        child_path,
        [
            {
                "step_index": 0,
                "source": "SYSTEM",
                "type": "SYSTEM_MESSAGE",
                "status": "DONE",
                "created_at": "2026-10-18T10:00:04Z",
                "content": "[Message] timestamp=2026-10-18T10:00:04Z sender=ag-parent priority=HIGH content=Subagent task",
            },
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "created_at": "2026-10-18T10:00:05Z",
                "input_tokens": 50,
                "cache_read_tokens": 10,
                "output_tokens": 12,
            },
        ],
    )
    assert list(adapter.iter_sessions()) == [path]
    with_child = adapter.parse(path)
    total_with_child = tu.aggregate(with_child["segments"], {})["total"]["usage"]
    assert total_with_child["output"] == 52
    assert with_child["segments"][-1]["subagents"][0]["description"] == "ag-child"
    assert with_child["segments"][-1]["subagents"][0]["output_tokens"] == 12
