"""Structured JSONL export: records, render, CLI, and RFC-8259 output."""
import json
import os
import subprocess
import sys

import pytest
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
    assert r.stdout.endswith("\n")
    lines = [ln for ln in r.stdout.split("\n") if ln]
    assert len(lines) >= 3
    first = json.loads(lines[0])
    assert first["schema"] == "token-usage.aggregate.v1"


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
    # A Claude corpus must not leak into a --runtime cursor export. The
    # assertion used to hide behind `if lines and lines[0]`, so an empty
    # stdout — including one caused by reading some other corpus — passed.
    seed_history(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", "--scope", "history",
         "--runtime", "cursor", "--since", "36500d", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path,
                 TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects"),
                 TOKEN_USAGE_CURSOR_DIR=str(tmp_path / "cursor-empty")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    records = [json.loads(ln) for ln in r.stdout.split("\n") if ln]
    assert [rec["key"] for rec in records] == ["total"]
    assert records[0]["runtime"] == "cursor"
    assert records[0]["metrics"]["gen_ai.usage.output_tokens"] == 0
    assert "-Users-x-repo-one" not in r.stdout


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


def test_export_public_helpers_exist(tu):
    assert callable(tu.session_export_records)
    assert callable(tu.history_export_records)
    assert callable(tu.history_export_data)
    assert callable(tu.render_jsonl)
    assert callable(tu.history_scan_measurement)


def test_history_export_worst_measurement_without_public_json_key(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    public = tu.run_history(by="project", since="36500d")
    assert "measurements" not in public

    real_core = tu._run_history_core

    def mixed_counts(**kwargs):
        data, _counts, runtime_name = real_core(**kwargs)
        return data, {"exact": 2, "partial": 1}, runtime_name

    monkeypatch.setattr(tu, "_run_history_core", mixed_counts)
    export_data, counts = tu.history_export_data(by="project", since="36500d")
    assert counts == {"exact": 2, "partial": 1}
    assert "measurements" not in export_data
    records = tu.history_export_records(
        export_data, generated_at=GENERATED_AT, measurement_counts=counts)
    assert records and all(r["measurement"] == "partial" for r in records)


def test_cli_export_rejects_transcript_with_history_scope(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    proj = tmp_path / "projects"
    transcript = next(proj.glob("*/*.jsonl"))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", str(transcript),
         "--scope", "history", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(proj)),
        check=False,
    )
    assert r.returncode != 0
    assert "TRANSCRIPT applies only to --scope session" in r.stderr + r.stdout


@pytest.mark.parametrize("flag,value", [
    ("--project", "repo-one"),
    ("--by", "day"),
    ("--since", "36500d"),
])
def test_cli_export_session_scope_rejects_history_only_options(tu, tmp_path, monkeypatch,
                                                               flag, value):
    # These three only shape a corpus scan. Session scope silently dropped
    # them, so `export --scope session --since 7d` reported the whole session
    # and looked like it had honoured the window.
    path = seed_session(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", str(path), "--scope", "session",
         flag, value, "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode != 0
    assert flag in r.stderr + r.stdout
    assert "--scope history" in r.stderr + r.stdout
    assert r.stdout.strip() == ""


def test_cli_export_session_scope_accepts_its_own_options(tu, tmp_path, monkeypatch):
    path = seed_session(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "export", str(path), "--scope", "session",
         "--runtime", "claude", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert any(json.loads(ln)["key"] == "total" for ln in r.stdout.split("\n") if ln)


def test_cli_export_history_still_defaults_to_by_project(tu, tmp_path, monkeypatch):
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
    records = [json.loads(ln) for ln in r.stdout.split("\n") if ln]
    assert records and all(rec["group_by"] == "project" for rec in records)


@pytest.mark.parametrize("by,dim_key", [
    ("project", "project"),
    ("day", "day"),
    ("command", "command"),
    ("model", "model"),
])
def test_history_export_dimensions_by_grouping(tu, tmp_path, monkeypatch, by, dim_key):
    seed_history(tmp_path, monkeypatch)
    export_data, counts = tu.history_export_data(by=by, since="36500d")
    records = tu.history_export_records(
        export_data, generated_at=GENERATED_AT, measurement_counts=counts)
    groups = [r for r in records if r["key"] != "total"]
    assert groups
    assert all(r["group_by"] == by for r in records)
    assert all(dim_key in r["dimensions"] for r in groups)


def test_history_export_empty_filtered_history(tu, tmp_path, monkeypatch):
    seed_history(tmp_path, monkeypatch)
    export_data, counts = tu.history_export_data(by="project", since="0d")
    assert export_data["rows"] == []
    records = tu.history_export_records(
        export_data, generated_at=GENERATED_AT, measurement_counts=counts)
    assert len(records) == 1
    assert records[0]["key"] == "total"
    assert records[0]["measurement"] == "exact"
    assert records[0]["metrics"]["gen_ai.usage.output_tokens"] == 0


def test_history_scan_measurement_takes_only_the_tally(tu):
    # `has_rows` was accepted and never read, so two call sites passed a value
    # that changed nothing and read as though it did.
    import inspect

    assert list(inspect.signature(tu.history_scan_measurement).parameters) == ["counts"]
    with pytest.raises(TypeError):
        tu.history_scan_measurement({}, has_rows=False)


def test_history_scan_measurement_reports_the_worst_tallied_level(tu):
    assert tu.history_scan_measurement({}) == "exact"
    assert tu.history_scan_measurement({"exact": 3}) == "exact"
    assert tu.history_scan_measurement({"exact": 3, "partial": 1}) == "partial"
    assert tu.history_scan_measurement({"partial": 2, "activity_only": 1}) == "activity_only"


def test_history_export_records_carry_the_measurement_counts(tu, tmp_path, monkeypatch):
    # "partial" alone cannot tell one weak session in a hundred from a corpus
    # nobody measured. The tally can, so ship it beside the worst-case level.
    seed_history(tmp_path, monkeypatch)
    export_data, counts = tu.history_export_data(by="project", since="36500d")
    records = tu.history_export_records(
        export_data, generated_at=GENERATED_AT, measurement_counts=counts)
    assert records
    assert all(r["measurement_counts"] == counts for r in records)


def test_history_export_counts_separate_one_weak_session_from_none_measured(tu):
    data = {"by": "project", "rows": [], "runtime": "claude", "warnings": []}
    weak = tu.history_export_records(
        data, generated_at=GENERATED_AT,
        measurement_counts={"exact": 99, "activity_only": 1})
    none_measured = tu.history_export_records(
        data, generated_at=GENERATED_AT, measurement_counts={"activity_only": 1})
    assert weak[0]["measurement"] == none_measured[0]["measurement"] == "activity_only"
    assert weak[0]["measurement_counts"] == {"exact": 99, "activity_only": 1}
    assert none_measured[0]["measurement_counts"] == {"activity_only": 1}


def test_empty_history_export_counts_nothing_rather_than_measuring_zero(tu, tmp_path,
                                                                        monkeypatch):
    seed_history(tmp_path, monkeypatch)
    export_data, counts = tu.history_export_data(by="project", since="0d")
    records = tu.history_export_records(
        export_data, generated_at=GENERATED_AT, measurement_counts=counts)
    # An empty tally is how a consumer tells "scanned nothing" from
    # "scanned sessions and they were all exact".
    assert records[0]["measurement_counts"] == {}


def test_session_export_records_carry_no_scan_counts(tu, tmp_path, monkeypatch):
    path = seed_session(tmp_path, monkeypatch)
    records = tu.session_export_records(_session_data(tu, path),
                                        generated_at=GENERATED_AT)
    assert records
    assert all("measurement_counts" not in r for r in records)


def test_history_export_records_counts_default_to_the_scan_data(tu, tmp_path,
                                                                monkeypatch):
    seed_history(tmp_path, monkeypatch)
    hist = tu.run_history(by="project", since="36500d")
    records = tu.history_export_records(hist, generated_at=GENERATED_AT)
    assert all(r["measurement_counts"] == {} for r in records)


def test_render_jsonl_strict_trailing_newline(tu):
    assert tu.render_jsonl([]) == ""
    one = tu.render_jsonl([{"schema": "token-usage.aggregate.v1", "metrics": {}}])
    assert one.endswith("\n")
    assert one.count("\n") == 1
    many = tu.render_jsonl([
        {"a": 1},
        {"b": 2},
    ])
    assert many.endswith("\n")
    assert many.count("\n") == 2
