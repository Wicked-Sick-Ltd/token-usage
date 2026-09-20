"""Structured JSONL export: records, render, CLI, and RFC-8259 output."""
import json
import os
import subprocess
import sys

from conftest import SCRIPT, assistant, usage, user, write_jsonl
from test_cursor_cli import zero_token_tree

GENERATED_AT = "2026-06-15T12:00:00Z"
METRIC_KEYS = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read_tokens",
    "gen_ai.usage.cache_write_tokens",
    "gen_ai.usage.requests",
    "gen_ai.estimated_cost.usd",
)


def seed_session(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    path = write_jsonl(proj / "-Users-x-repo-one" / "s1.jsonl", [
        user("2026-06-10T10:00:00Z", command="/review"),
        assistant("2026-06-10T10:00:01Z",
                  usage(inp=200, out=100, cache_read=50, cache_5m=20, cache_1h=10),
                  request_id="r1"),
        user("2026-06-10T10:01:00Z", command="/commit"),
        assistant("2026-06-10T10:01:01Z", usage(inp=80, out=50), request_id="r2"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    return path


def seed_history(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    write_jsonl(proj / "-Users-x-repo-one" / "s1.jsonl", [
        user("2026-06-10T10:00:00Z", command="/review"),
        assistant("2026-06-10T10:00:01Z", usage(out=100), request_id="r1"),
    ])
    write_jsonl(proj / "-Users-x-repo-two" / "s2.jsonl", [
        user("2026-06-12T10:00:00Z", command="/commit"),
        assistant("2026-06-12T10:00:01Z", usage(out=50), request_id="r2"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))


def _env(tmp_path, **extra):
    env = {
        **os.environ,
        "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", str(tmp_path / "xdg")),
    }
    env.update(extra)
    return env


def _session_data(tu, path):
    warnings = []
    pricing = tu.load_pricing(warnings)
    segments = tu.parse_session(path)
    data = tu.aggregate(segments, pricing)
    data["transcript_path"] = str(path)
    data["runtime"] = "claude"
    data["measurement"] = "exact"
    data["warnings"] = warnings
    return data


def _sum_metrics(records):
    total = {k: 0 for k in METRIC_KEYS}
    cost = 0.0
    have_cost = False
    for rec in records:
        m = rec["metrics"]
        for k in METRIC_KEYS[:-1]:
            total[k] += m[k]
        c = m["gen_ai.estimated_cost.usd"]
        if c is not None:
            cost += c
            have_cost = True
    total["gen_ai.estimated_cost.usd"] = cost if have_cost else None
    return total


def test_session_export_reconciles_total_and_activities(tu, tmp_path, monkeypatch):
    path = seed_session(tmp_path, monkeypatch)
    data = _session_data(tu, path)
    records = tu.session_export_records(data, generated_at=GENERATED_AT)
    assert len(records) == 3
    total = next(r for r in records if r["key"] == "total")
    activities = [r for r in records if r["key"] != "total"]
    assert len(activities) == 2
    assert _sum_metrics(activities) == total["metrics"]


def test_history_export_reconciles_groups_and_total(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    hist = tu.run_history(by="project", since="36500d")
    records = tu.history_export_records(hist, generated_at=GENERATED_AT)
    groups = [r for r in records if r["key"] != "total"]
    total = next(r for r in records if r["key"] == "total")
    assert len(groups) == 2
    assert _sum_metrics(groups) == total["metrics"]


def test_export_schema_and_metric_names(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    hist = tu.run_history(by="project", since="36500d")
    rec = tu.history_export_records(hist, generated_at=GENERATED_AT)[0]
    assert rec["schema"] == "token-usage.aggregate.v1"
    assert rec["scope"] == "history"
    assert rec["group_by"] == "project"
    assert set(rec["metrics"]) == set(METRIC_KEYS)
    assert rec["dimensions"] == {"project": rec["key"]}


def test_session_export_scope_and_dimensions(tu, tmp_path, monkeypatch):
    path = seed_session(tmp_path, monkeypatch)
    data = _session_data(tu, path)
    records = tu.session_export_records(data, generated_at=GENERATED_AT)
    for rec in records:
        assert rec["scope"] == "session"
        assert "group_by" not in rec
        assert rec["runtime"] == "claude"
        assert rec["timestamp"] == GENERATED_AT
    activity = next(r for r in records if r["key"] == "/review")
    assert activity["dimensions"] == {"activity": "/review"}


def test_export_null_cost_activity_only(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    warnings = []
    adapter, runtime_name = tu.resolve_runtime("cursor", transcript_arg=None, warnings=warnings)
    data = tu._session_aggregate(adapter, runtime_name, None, tu.load_pricing(warnings), warnings)
    records = tu.session_export_records(data, generated_at=GENERATED_AT)
    total = next(r for r in records if r["key"] == "total")
    assert total["measurement"] == "activity_only"
    assert total["metrics"]["gen_ai.estimated_cost.usd"] is None
    assert total["metrics"]["gen_ai.usage.output_tokens"] == 0


def test_export_preserves_warnings(tu, tmp_path, monkeypatch):
    path = seed_session(tmp_path, monkeypatch)
    data = _session_data(tu, path)
    data["warnings"] = ["sample pricing warning"]
    data["measurement"] = "partial"
    records = tu.session_export_records(data, generated_at=GENERATED_AT)
    assert all(r["warnings"] == ["sample pricing warning"] for r in records)
    assert all(r["measurement"] == "partial" for r in records)


def test_render_jsonl_rfc8259_no_nan(tu):
    records = [{
        "schema": "token-usage.aggregate.v1",
        "metrics": {"gen_ai.estimated_cost.usd": None, "x": 1},
    }]
    text = tu.render_jsonl(records)
    line = text.strip()
    assert "NaN" not in line and "Infinity" not in line
    parsed = json.loads(line)
    assert parsed["metrics"]["gen_ai.estimated_cost.usd"] is None
    bad = [{"metrics": {"gen_ai.estimated_cost.usd": float("nan")}}]
    text2 = tu.render_jsonl(bad)
    assert "NaN" not in text2
    assert json.loads(text2.strip())["metrics"]["gen_ai.estimated_cost.usd"] is None


def test_render_jsonl_escapes_control_characters(tu):
    label = 'line\nbreak"\ttab'
    records = [{
        "schema": "token-usage.aggregate.v1",
        "key": label,
        "dimensions": {"activity": label},
        "metrics": {},
        "warnings": [],
    }]
    text = tu.render_jsonl(records)
    parsed = json.loads(text.strip())
    assert parsed["key"] == label


def test_write_text_output_jsonl_atomic(tu, tmp_path):
    tu.write_text_output("line1\nline2\n", tmp_path / "out.jsonl")
    assert (tmp_path / "out.jsonl").read_text(encoding="utf-8") == "line1\nline2\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_export_history_stdout(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", "--scope", "history",
         "--since", "36500d", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.strip().split("\n") if ln]
    assert len(lines) >= 3
    first = json.loads(lines[0])
    assert first["schema"] == "token-usage.aggregate.v1"
    assert r.stdout.endswith("\n") or len(lines) == 1


def test_cli_export_session_file(tu, tmp_path, monkeypatch):
    path = seed_session(tmp_path, monkeypatch)
    out = tmp_path / "usage.jsonl"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", str(path),
         "--scope", "session", "--output", str(out)],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    records = [json.loads(ln) for ln in out.read_text(encoding="utf-8").strip().split("\n")]
    assert any(rec["key"] == "total" for rec in records)


def test_cli_export_history_runtime_isolation(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", "--scope", "history",
         "--runtime", "cursor", "--since", "36500d", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().split("\n")
    if lines and lines[0]:
        assert json.loads(lines[0])["runtime"] == "cursor"


def test_cli_export_default_scope_history(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", "--since", "36500d", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().split("\n")[0])["scope"] == "history"


def test_export_apis_missing_before_implementation(tu):
    assert callable(getattr(tu, "session_export_records", None))
    assert callable(getattr(tu, "history_export_records", None))
    assert callable(getattr(tu, "render_jsonl", None))
