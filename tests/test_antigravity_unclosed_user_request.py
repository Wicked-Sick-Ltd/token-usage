"""An unclosed Antigravity USER_REQUEST must stay one prompt, not stall the scan.

These tests fail while the USER_REQUEST expression backtracks across the spaces
in a transcript the Gemini scanner already accepts. They pass once a missing
close is an ordinary unparsed prompt and every scan that shares that parser
returns.
"""
import json
import os
import subprocess
import sys

import pytest
from conftest import SCRIPT, write_jsonl

# Large enough that the overlapping quantifiers do not return within LIMIT_S,
# and small enough that a literal search of the same record is immediate.
SPACES = 6000
LIMIT_S = 3
OPEN = "<USER_REQUEST>"
KEPT_OUTPUT = 4
UNCLOSED_OUTPUT = 11
POISON_OUTPUT = KEPT_OUTPUT + UNCLOSED_OUTPUT
HEALTHY_OUTPUT = 7
CORPUS_OUTPUT = POISON_OUTPUT + HEALTHY_OUTPUT
PARENT_OUTPUT = 5
CHILD_OUTPUT = 13
SIBLING_OUTPUT = 3
FAMILY_OUTPUT = PARENT_OUTPUT + CHILD_OUTPUT + SIBLING_OUTPUT
TS = "2026-11-14T12:00:00Z"


def _content(closed=False):
    body = OPEN + (" " * SPACES)
    if closed:
        body += "</USER_REQUEST>"
    return body


def _step(index, kind, source, content=None, output=None, created_at=TS):
    row = {
        "step_index": index,
        "source": source,
        "type": kind,
        "status": "DONE",
        "created_at": created_at,
    }
    if content is not None:
        row["content"] = content
    if output is not None:
        row["input_tokens"] = 40 + index
        row["cache_read_tokens"] = 0
        row["output_tokens"] = output
    return row


def _transcript(home, sid):
    return home / "antigravity" / "brain" / sid / ".system_generated" / "logs" / "transcript.jsonl"


def _poison_rows(closed=False):
    return [
        _step(0, "USER_INPUT", "USER_EXPLICIT",
              "<USER_REQUEST>\n/review keep\n</USER_REQUEST>"),
        _step(1, "PLANNER_RESPONSE", "MODEL", output=KEPT_OUTPUT),
        _step(2, "USER_INPUT", "USER_EXPLICIT", _content(closed)),
        _step(3, "PLANNER_RESPONSE", "MODEL", output=UNCLOSED_OUTPUT),
    ]


def _healthy_rows():
    return [
        _step(0, "USER_INPUT", "USER_EXPLICIT",
              "<USER_REQUEST>\n/review sibling\n</USER_REQUEST>"),
        _step(1, "PLANNER_RESPONSE", "MODEL", output=HEALTHY_OUTPUT),
    ]


def build_corpus(tmp_path, closed=False):
    home = tmp_path / "gemini"
    poison = write_jsonl(_transcript(home, "ag-poison"), _poison_rows(closed))
    healthy = write_jsonl(_transcript(home, "ag-healthy"), _healthy_rows())
    return home, poison, healthy


def build_family(tmp_path, closed=False):
    """Parent plus a child the parent parse globs, and a sibling top-level session."""
    home = tmp_path / "gemini"
    parent = write_jsonl(_transcript(home, "ag-parent"), [
        _step(0, "USER_INPUT", "USER_EXPLICIT",
              "<USER_REQUEST>\n/review parent\n</USER_REQUEST>"),
        _step(1, "PLANNER_RESPONSE", "MODEL", output=PARENT_OUTPUT),
    ])
    child = write_jsonl(_transcript(home, "ag-child"), [
        _step(0, "SYSTEM_MESSAGE", "SYSTEM",
              "[Message] timestamp=2026-11-14T12:00:00Z sender=ag-parent priority=HIGH"),
        _step(1, "USER_INPUT", "USER_EXPLICIT", _content(closed)),
        _step(2, "PLANNER_RESPONSE", "MODEL", output=CHILD_OUTPUT),
    ])
    sibling = write_jsonl(_transcript(home, "ag-sibling"), [
        _step(0, "USER_INPUT", "USER_EXPLICIT",
              "<USER_REQUEST>\n/review sibling\n</USER_REQUEST>"),
        _step(1, "PLANNER_RESPONSE", "MODEL", output=SIBLING_OUTPUT),
    ])
    return home, parent, child, sibling


def _env(home, ledger):
    env = dict(os.environ)
    env["TOKEN_USAGE_GEMINI_HOME"] = str(home)
    env["TOKEN_USAGE_LEDGER_DIR"] = str(ledger)
    env["PYTHONUTF8"] = "1"
    for key in ("TOKEN_USAGE_TRANSCRIPT", "GEMINI_SESSION_ID", "TOKEN_USAGE_RUNTIME",
                "GEMINI_CLI_HOME"):
        env.pop(key, None)
    return env


def _run(args, env, timeout=LIMIT_S):
    try:
        return subprocess.run(
            args, env=env, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{args} did not finish within {timeout}s")


def _cli(args, env):
    result = _run([sys.executable, str(SCRIPT), *args], env)
    assert result.returncode == 0, result.stderr
    return result


def _worker(mode, env, *args):
    result = _run([sys.executable, __file__, mode, *args], env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _outputs(data):
    return sum(row["usage"]["output"] for row in data["rows"])


def test_unclosed_user_request_is_a_prompt_and_later_tokens_count(tmp_path):
    home, poison, _healthy = build_corpus(tmp_path)
    parsed = _worker("parse", _env(home, tmp_path / "ledger"), str(poison))
    assert parsed["labels"] == ["/review", "(no command)"]
    assert parsed["prompts"][0] == "/review keep"
    preview = parsed["prompts"][1]
    assert preview.startswith(OPEN)
    assert "</USER_REQUEST>" not in preview
    assert len(preview) == 120
    assert parsed["output"] == POISON_OUTPUT
    assert parsed["measurement"] == "exact"


def test_parent_parse_rolls_up_unclosed_child_transcript(tmp_path):
    home, parent, _child, _sibling = build_family(tmp_path)
    parsed = _worker("parse-parent", _env(home, tmp_path / "ledger"), str(parent))
    assert parsed["output"] == PARENT_OUTPUT + CHILD_OUTPUT
    assert parsed["measurement"] == "exact"
    assert parsed["subagents"] == [{"description": "ag-child", "output_tokens": CHILD_OUTPUT}]


def test_history_counts_poisoned_session_and_sibling(tmp_path):
    home, _poison, _healthy = build_corpus(tmp_path)
    result = _cli(["history", "--json", "--runtime", "gemini"], _env(home, tmp_path / "ledger"))
    data = json.loads(result.stdout)
    assert _outputs(data) == CORPUS_OUTPUT
    assert data["rows"]


def test_history_counts_sibling_when_child_transcript_is_unclosed(tmp_path):
    home, _parent, _child, _sibling = build_family(tmp_path)
    result = _cli(["history", "--json", "--runtime", "gemini"], _env(home, tmp_path / "ledger"))
    assert _outputs(json.loads(result.stdout)) == FAMILY_OUTPUT


def test_dashboard_renders_poisoned_corpus(tmp_path):
    home, _poison, _healthy = build_corpus(tmp_path)
    out = tmp_path / "dashboard.html"
    _cli(["dashboard", "--runtime", "gemini", "--since", "30d", "--output", str(out)],
         _env(home, tmp_path / "ledger"))
    html = out.read_text(encoding="utf-8")
    assert f"<span>Output tokens</span><strong>{CORPUS_OUTPUT}</strong>" in html
    assert "<span>Sessions</span><strong>2</strong>" in html


def test_insights_window_scans_poisoned_corpus(tmp_path):
    home, _poison, _healthy = build_corpus(tmp_path)
    result = _cli(["insights", "--json", "--runtime", "gemini", "--since", "30d"],
                  _env(home, tmp_path / "ledger"))
    data = json.loads(result.stdout)
    assert data["mode"] == "window"
    assert data["baseline"]["sessions"] == 2


def test_insights_session_parses_unclosed_transcript(tmp_path):
    home, poison, _healthy = build_corpus(tmp_path)
    result = _cli(["insights", "--json", "--runtime", "gemini", str(poison)],
                  _env(home, tmp_path / "ledger"))
    data = json.loads(result.stdout)
    assert data["mode"] == "session"
    assert data["measurement"] == "exact"
    assert "ag-poison" in data["transcript_path"]


def test_insights_on_sibling_still_scans_unclosed_transcript(tmp_path):
    home, _poison, healthy = build_corpus(tmp_path)
    result = _cli(["insights", "--json", "--runtime", "gemini", str(healthy)],
                  _env(home, tmp_path / "ledger"))
    data = json.loads(result.stdout)
    assert data["mode"] == "session"
    assert data["measurement"] == "exact"
    assert "ag-healthy" in data["transcript_path"]


def test_export_history_totals_include_unclosed_transcript(tmp_path):
    home, _poison, _healthy = build_corpus(tmp_path)
    result = _cli(["export", "--runtime", "gemini", "--scope", "history"],
                  _env(home, tmp_path / "ledger"))
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    total = next(row for row in rows if row["key"] == "total")
    assert total["metrics"]["gen_ai.usage.output_tokens"] == CORPUS_OUTPUT


def test_export_session_keeps_unclosed_prompt_activity(tmp_path):
    home, poison, _healthy = build_corpus(tmp_path)
    result = _cli(["export", "--runtime", "gemini", "--scope", "session", str(poison)],
                  _env(home, tmp_path / "ledger"))
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    total = next(row for row in rows if row["key"] == "total")
    assert total["metrics"]["gen_ai.usage.output_tokens"] == POISON_OUTPUT
    assert any(row["key"] == "(no command)" for row in rows)


def test_top_consumers_lists_poisoned_session_and_sibling(tmp_path):
    home, _poison, _healthy = build_corpus(tmp_path)
    result = _cli(["top_consumers", "--json", "--runtime", "gemini", "--since", "30d"],
                  _env(home, tmp_path / "ledger"))
    data = json.loads(result.stdout)
    assert {row["session_id"] for row in data["rows"]} == {"ag-poison", "ag-healthy"}
    assert _outputs(data) == CORPUS_OUTPUT


@pytest.mark.parametrize("command", ["report", "json", "live"])
def test_session_commands_finish_on_unclosed_transcript(command, tmp_path):
    home, poison, _healthy = build_corpus(tmp_path)
    args = [command, "--runtime", "gemini", str(poison)]
    if command == "live":
        args += ["--iterations", "1"]
    result = _cli(args, _env(home, tmp_path / "ledger"))
    if command == "json":
        data = json.loads(result.stdout)
        assert data["total"]["usage"]["output"] == POISON_OUTPUT
        assert data["measurement"] == "exact"
        assert "(no command)" in data["by_label"]
    else:
        assert "(no command)" in result.stdout
        assert "**15**" in result.stdout


def test_diff_parses_unclosed_transcript(tmp_path):
    home, poison, healthy = build_corpus(tmp_path)
    result = _cli(["json", "--runtime", "gemini", "--diff", str(healthy), str(poison)],
                  _env(home, tmp_path / "ledger"))
    data = json.loads(result.stdout)
    assert data["a_total"]["usage"]["output"] == HEALTHY_OUTPUT
    assert data["b_total"]["usage"]["output"] == POISON_OUTPUT


def _hook(env, payload):
    try:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "hook"],
            input=json.dumps(payload),
            env=env, capture_output=True, text=True, encoding="utf-8",
            timeout=LIMIT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"hook did not finish within {LIMIT_S}s")


def test_stop_hook_returns_for_unclosed_transcript(tmp_path):
    home, poison, _healthy = build_corpus(tmp_path)
    ledger = tmp_path / "ledger"
    result = _hook(_env(home, ledger),
                   {"conversationId": "ag-poison", "transcriptPath": str(poison)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    saved = json.loads((ledger / "gemini-ag-poison.json").read_text(encoding="utf-8"))
    assert saved["total"]["usage"]["output"] == POISON_OUTPUT


def test_stop_hook_rolls_up_unclosed_child(tmp_path):
    home, parent, _child, _sibling = build_family(tmp_path)
    ledger = tmp_path / "ledger"
    result = _hook(_env(home, ledger),
                   {"conversationId": "ag-parent", "transcriptPath": str(parent)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    saved = json.loads((ledger / "gemini-ag-parent.json").read_text(encoding="utf-8"))
    assert saved["total"]["usage"]["output"] == PARENT_OUTPUT + CHILD_OUTPUT


def test_mcp_scans_finish_on_unclosed_transcript(tmp_path):
    home, poison, healthy = build_corpus(tmp_path)
    payload = _worker(
        "mcp", _env(home, tmp_path / "ledger"), str(home), str(poison), str(healthy),
    )
    assert payload["history_output"] == CORPUS_OUTPUT
    assert payload["session_output"] == POISON_OUTPUT
    assert payload["session_measurement"] == "exact"
    assert "(no command)" in payload["session_labels"]
    assert payload["insights_sessions"] == 2
    assert payload["top_ids"] == ["ag-healthy", "ag-poison"]
    assert payload["top_output"] == CORPUS_OUTPUT
    assert payload["diff_a"] == HEALTHY_OUTPUT
    assert payload["diff_b"] == POISON_OUTPUT


def _load_tu():
    scripts = str(SCRIPT.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import token_usage as tu
    return tu


def _main(argv):
    mode = argv[1]
    tu = _load_tu()
    if mode == "parse":
        parsed = tu.parse_gemini_session(argv[2])
        segments = parsed["segments"]
        print(json.dumps({
            "labels": [seg["label"] for seg in segments],
            "prompts": [seg["prompt"] for seg in segments],
            "output": tu.aggregate(segments, {})["total"]["usage"]["output"],
            "measurement": parsed["measurement"],
        }))
        return
    if mode == "parse-parent":
        parsed = tu.get_runtime_adapter("gemini").parse(argv[2])
        subs = [sub for seg in parsed["segments"] for sub in seg["subagents"]]
        print(json.dumps({
            "output": tu.aggregate(parsed["segments"], {})["total"]["usage"]["output"],
            "measurement": parsed["measurement"],
            "subagents": [{"description": sub["description"],
                           "output_tokens": sub["output_tokens"]} for sub in subs],
        }))
        return
    if mode == "mcp":
        import mcp_server
        home, poison, healthy = argv[2], argv[3], argv[4]
        os.environ["TOKEN_USAGE_GEMINI_HOME"] = home
        history = json.loads(mcp_server.tool_history({"runtime": "gemini", "format": "json"}))
        session = json.loads(mcp_server.tool_session_cost(
            {"runtime": "gemini", "session_id": "ag-poison", "format": "json"}))
        insights = json.loads(mcp_server.tool_insights(
            {"runtime": "gemini", "since": "30d", "format": "json"}))
        top = json.loads(mcp_server.tool_top_consumers(
            {"runtime": "gemini", "since": "30d", "format": "json"}))
        diff = json.loads(mcp_server.tool_diff(
            {"runtime": "gemini", "old": healthy, "new": poison, "format": "json"}))
        print(json.dumps({
            "history_output": _outputs(history),
            "session_output": session["total"]["usage"]["output"],
            "session_measurement": session["measurement"],
            "session_labels": list(session["by_label"]),
            "insights_sessions": insights["baseline"]["sessions"],
            "top_ids": sorted(row["session_id"] for row in top["rows"]),
            "top_output": _outputs(top),
            "diff_a": diff["a_total"]["usage"]["output"],
            "diff_b": diff["b_total"]["usage"]["output"],
        }))
        return
    raise SystemExit(f"unknown worker {mode}")


if __name__ == "__main__":
    _main(sys.argv)
