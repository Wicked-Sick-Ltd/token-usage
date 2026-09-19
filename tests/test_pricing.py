"""Bundled data/pricing.json plus user overlay; malformed files non-fatal."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import SCRIPT, assistant, usage, user, write_jsonl

# Isolated bundled table for load_pricing tests — not the live file, and not an
# in-code DEFAULT_PRICING copy. Distinctive enough that a stale fallback would
# still look like these numbers if someone reintroduced one with the live rates.
BUNDLED_FIXTURE = {
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25},
    "claude-fable-5": {"input": 10.0, "output": 50.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
}


def _install_bundled(tu, tmp_path, monkeypatch, payload):
    path = tmp_path / "bundled" / "pricing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        pass
    elif isinstance(payload, bytes):
        path.write_bytes(payload)
    elif isinstance(payload, str):
        path.write_text(payload)
    else:
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(tu, "bundled_pricing_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def _isolated_bundled_pricing(tu, tmp_path, monkeypatch, request):
    # Hook subprocess tests copy the script into a tree of their own; the
    # packaging test must see the real plugin file.
    if request.node.name.startswith(("test_plugin_ships", "test_hook_exits")):
        return
    _install_bundled(tu, tmp_path, monkeypatch, BUNDLED_FIXTURE)


def test_user_pricing_path_respects_xdg(tu, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert tu.user_pricing_path() == tmp_path / "cfg" / "token-usage" / "pricing.json"


def test_overlay_merges_per_key(tu, monkeypatch, tmp_path):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "claude-fable-5": {"input": 99.0, "output": 500.0},   # override bundled
        "claude-newmodel-7": {"input": 4.0, "output": 20.0},  # brand new
    }))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-fable-5"] == {"input": 99.0, "output": 500.0}
    assert pricing["claude-newmodel-7"] == {"input": 4.0, "output": 20.0}
    # a bundled key NOT in the overlay survives the merge
    assert pricing["claude-haiku-4-5"] == {"input": 1.0, "output": 5.0}


def test_malformed_overlay_is_skipped_with_warning(tu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-fable-5"] == {"input": 10.0, "output": 50.0}  # bundled intact
    assert "pricing" in capsys.readouterr().err


def test_invalid_rate_entry_is_skipped(tu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"claude-fable-5": {"input": "cheap"}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-fable-5"] == {"input": 10.0, "output": 50.0}
    assert "claude-fable-5" in capsys.readouterr().err


def test_boolean_rate_entry_is_rejected(tu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"claude-fable-5": {"input": True, "output": 5.0}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-fable-5"] == {"input": 10.0, "output": 50.0}  # bundled intact
    assert "claude-fable-5" in capsys.readouterr().err


def test_non_utf8_overlay_is_skipped_with_warning(tu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\xff\xfe\x00garbage")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-fable-5"] == {"input": 10.0, "output": 50.0}  # bundled intact
    assert "pricing" in capsys.readouterr().err


def test_no_overlay_matches_bundled(tu, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
    assert tu.load_pricing()["claude-sonnet-5"] == {"input": 2.0, "output": 10.0}


def test_overlay_accepts_optional_cache_read_rate(tu, monkeypatch, tmp_path):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "claude-newmodel-7": {"input": 4.0, "output": 20.0, "cache_read": 0.1},
    }))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-newmodel-7"] == {"input": 4.0, "output": 20.0, "cache_read": 0.1}


def test_overlay_rejects_non_numeric_cache_read(tu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"claude-fable-5-1": {"input": 10.0, "output": 50.0,
                                                  "cache_read": "cheap"}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    assert pricing["claude-fable-5-1"] == {"input": 10.0, "output": 50.0, "cache_read": 0.25}
    assert "claude-fable-5-1" in capsys.readouterr().err


def test_unpriced_models_helper(tu):
    by_model = {"claude-fable-5": tu.empty_usage(),
                "claude-mystery-9": dict(tu.empty_usage(), output=100)}
    assert tu.unpriced_models(by_model, BUNDLED_FIXTURE) == ["claude-mystery-9"]
    assert tu.unpriced_models({"claude-fable-5": tu.empty_usage()}, BUNDLED_FIXTURE) == []


def test_unpriced_models_skips_zero_usage_pseudo_model(tu):
    by_model = {"claude-fable-5": dict(tu.empty_usage(), output=100),
                "<synthetic>": tu.empty_usage()}
    assert tu.unpriced_models(by_model, BUNDLED_FIXTURE) == []


def test_report_footnote_for_unpriced_model(tu, tmp_path):
    from conftest import assistant, usage, user, write_jsonl
    t = write_jsonl(tmp_path / "s.jsonl", [
        user("2026-07-01T10:00:00Z", command="/go"),
        assistant("2026-07-01T10:00:05Z", usage(inp=10, out=20),
                  model="claude-mystery-9", request_id="r1"),
    ])
    data = tu.aggregate(tu.parse_session(t), tu.load_pricing())
    assert data["total"]["unpriced_models"] == ["claude-mystery-9"]
    out = tu.render_report(data)
    assert "unpriced" in out and "claude-mystery-9" in out and "pricing.json" in out


def test_no_footnote_when_all_priced(tu, tmp_path):
    from conftest import assistant, usage, user, write_jsonl
    t = write_jsonl(tmp_path / "s.jsonl", [
        user("2026-07-01T10:00:00Z", command="/go"),
        assistant("2026-07-01T10:00:05Z", usage(inp=10, out=20), request_id="r1"),
    ])
    data = tu.aggregate(tu.parse_session(t), tu.load_pricing())
    assert data["total"]["unpriced_models"] == []
    assert "unpriced" not in tu.render_report(data)


def test_history_collects_unpriced(tu, monkeypatch, tmp_path):
    from conftest import assistant, usage, user, write_jsonl
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    write_jsonl(tmp_path / "projects" / "proj-a" / "s1.jsonl", [
        user("2026-07-01T10:00:00Z"),
        assistant("2026-07-01T10:00:05Z", usage(inp=10, out=20),
                  model="claude-mystery-9", request_id="r1"),
    ])
    data = tu.run_history(by="project")
    assert data["unpriced_models"] == ["claude-mystery-9"]
    assert "unpriced" in tu.render_history(data)


def test_load_pricing_collects_its_warnings(tu, tmp_path, monkeypatch, capsys):
    # The overlay silently reverting to bundled rates is exactly the kind of
    # thing an MCP caller cannot see on stderr — collect the same text.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    overlay = tu.user_pricing_path()
    overlay.parent.mkdir(parents=True)
    overlay.write_text("{not json")
    warnings = []
    tu.load_pricing(warnings)
    assert warnings == [f"ignoring malformed pricing file {overlay}"]
    assert "token-usage: ignoring malformed pricing file" in capsys.readouterr().err

    overlay.write_text(json.dumps({"claude-x": {"input": "free", "output": 1.0}}))
    warnings = []
    tu.load_pricing(warnings)
    assert warnings == [f"ignoring invalid rates for claude-x in {overlay}"]

    overlay.write_text(json.dumps({"claude-x": {"input": 1.0, "output": 2.0}}))
    warnings = []
    assert tu.load_pricing(warnings)["claude-x"] == {"input": 1.0, "output": 2.0}
    assert warnings == []


def test_budget_from_env_warns_about_junk(tu, monkeypatch, capsys):
    monkeypatch.delenv("TOKEN_USAGE_BUDGET_USD", raising=False)
    warnings = []
    assert tu.budget_from_env(warnings) is None and warnings == []   # unset is silent
    monkeypatch.setenv("TOKEN_USAGE_BUDGET_USD", "ten pounds")
    assert tu.budget_from_env(warnings) is None
    assert warnings == ["ignoring TOKEN_USAGE_BUDGET_USD='ten pounds' — not a number"]
    assert "not a number" in capsys.readouterr().err
    monkeypatch.setenv("TOKEN_USAGE_BUDGET_USD", "12.5")
    assert tu.budget_from_env() == 12.5


def test_non_finite_and_negative_rates_are_rejected(tu, monkeypatch, tmp_path, capsys):
    # json.loads accepts the bare NaN/Infinity literals, and 1e400 overflows to
    # inf: an overlay carrying one used to poison every cost — a "cost_usd": NaN
    # in the MCP json payload (not RFC-8259 JSON, unparseable by a strict
    # client) and in the Stop-hook ledger, and int(cost // limit) raising on
    # every Stop, which killed the budget nudge behind one stderr line.
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    for raw in ("NaN", "Infinity", "-Infinity", "1e400", "-1000", "-0.5"):
        p.write_text('{"claude-sonnet-4-5": {"input": ' + raw + ', "output": 15.0}}')
        warnings = []
        pricing = tu.load_pricing(warnings)
        assert pricing["claude-sonnet-4-5"] == {"input": 3.0, "output": 15.0}, raw
        assert warnings == [("ignoring invalid rates for claude-sonnet-4-5 in "
                             f"{tu.user_pricing_path()}")], raw
        capsys.readouterr()
    # A huge integer literal is a float overflow away from inf, and 0 is a
    # legitimate free-tier rate.
    p.write_text('{"claude-sonnet-4-5": {"input": ' + "9" * 400 + ', "output": 0}}')
    warnings = []
    assert tu.load_pricing(warnings)["claude-sonnet-4-5"] == {"input": 3.0, "output": 15.0}
    assert warnings and "claude-sonnet-4-5" in warnings[0]
    p.write_text('{"claude-sonnet-4-5": {"input": 0, "output": 0.0}}')
    assert tu.load_pricing()["claude-sonnet-4-5"] == {"input": 0, "output": 0.0}
    capsys.readouterr()


def test_costs_stay_json_serialisable_with_a_poisoned_overlay(tu, monkeypatch, tmp_path, capsys):
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"claude-fable-5": {"input": NaN, "output": NaN}}')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    pricing = tu.load_pricing()
    cost = tu.cost_usd({"claude-fable-5": dict(tu.empty_usage(), output=1_000_000)}, pricing)
    assert cost == 50.0
    assert json.loads(json.dumps({"cost_usd": cost}, allow_nan=False))["cost_usd"] == 50.0
    capsys.readouterr()


def test_no_in_code_default_pricing_table(tu):
    assert not hasattr(tu, "DEFAULT_PRICING")


def test_plugin_ships_bundled_pricing_json(tu):
    path = tu.bundled_pricing_path()
    assert path.is_file(), path
    assert path == SCRIPT.parent.parent / "data" / "pricing.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data
    assert all(tu._valid_rates(v) for v in data.values())
    root = SCRIPT.parent.parent
    assert (root / "data" / "pricing.json").is_file()
    assert (root / "scripts" / "token_usage.py").is_file()
    assert (root / ".claude-plugin" / "plugin.json").is_file()


def test_missing_bundled_warns_and_leaves_models_unpriced(tu, tmp_path, monkeypatch):
    missing = tmp_path / "absent" / "pricing.json"
    monkeypatch.setattr(tu, "bundled_pricing_path", lambda: missing)
    warnings = []
    pricing = tu.load_pricing(warnings)
    assert pricing == {}
    assert warnings == [f"bundled pricing table missing: {missing}"]
    assert tu.rates_for("claude-fable-5", pricing) is None
    by_model = {"claude-fable-5": dict(tu.empty_usage(), output=100)}
    assert tu.unpriced_models(by_model, pricing) == ["claude-fable-5"]
    assert tu.cost_usd(by_model, pricing) is None


def test_malformed_bundled_warns_and_leaves_models_unpriced(tu, tmp_path, monkeypatch):
    path = _install_bundled(tu, tmp_path, monkeypatch, "{not json")
    warnings = []
    pricing = tu.load_pricing(warnings)
    assert pricing == {}
    assert warnings == [f"ignoring malformed pricing file {path}"]
    assert tu.rates_for("claude-fable-5", pricing) is None


def test_bundled_non_dict_leaves_models_unpriced(tu, tmp_path, monkeypatch):
    path = _install_bundled(tu, tmp_path, monkeypatch, "[1, 2]")
    warnings = []
    assert tu.load_pricing(warnings) == {}
    assert warnings == [f"ignoring malformed pricing file {path}"]


def test_invalid_bundled_entry_is_unpriced_not_stale(tu, tmp_path, monkeypatch):
    _install_bundled(tu, tmp_path, monkeypatch, {
        "claude-fable-5": {"input": "cheap", "output": 50.0},
        "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    })
    warnings = []
    pricing = tu.load_pricing(warnings)
    assert "claude-fable-5" not in pricing
    assert pricing == {"claude-haiku-4-5": {"input": 1.0, "output": 5.0}}
    assert any("claude-fable-5" in w for w in warnings)


def test_overlay_applies_when_bundled_is_missing(tu, tmp_path, monkeypatch):
    missing = tmp_path / "absent" / "pricing.json"
    monkeypatch.setattr(tu, "bundled_pricing_path", lambda: missing)
    p = tmp_path / "cfg" / "token-usage" / "pricing.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"claude-only-overlay": {"input": 1.0, "output": 2.0}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    warnings = []
    pricing = tu.load_pricing(warnings)
    assert pricing == {"claude-only-overlay": {"input": 1.0, "output": 2.0}}
    assert any("bundled pricing table missing" in w for w in warnings)
    assert "claude-fable-5" not in pricing


def test_load_pricing_never_raises_on_missing_or_malformed_bundled(tu, tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "bundled_pricing_path", lambda: tmp_path / "nope.json")
    assert tu.load_pricing() == {}
    _install_bundled(tu, tmp_path, monkeypatch, "{")
    assert tu.load_pricing() == {}


def _run_hook_from_plugin_tree(plugin_root, tmp_path, transcript):
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "ledger")}
    env.pop("TOKEN_USAGE_BUDGET_USD", None)
    return subprocess.run(
        [sys.executable, str(plugin_root / "scripts" / "token_usage.py"), "hook"],
        input=json.dumps({"session_id": "s1", "transcript_path": str(transcript)}),
        capture_output=True, text=True, env=env, check=False,
    )


def test_hook_exits_zero_when_bundled_pricing_is_missing(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, plugin / "scripts" / "token_usage.py")
    t = write_jsonl(plugin / "t.jsonl", [
        user("2026-07-01T10:00:00Z", command="/go"),
        assistant("2026-07-01T10:00:05Z", usage(out=100), request_id="r1"),
    ])
    r = _run_hook_from_plugin_tree(plugin, tmp_path, t)
    assert r.returncode == 0, r.stderr
    assert "bundled pricing table missing" in r.stderr
    ledger = json.loads((tmp_path / "ledger" / "s1.json").read_text())
    assert ledger["total"]["unpriced_models"] == ["claude-fable-5"]
    assert ledger["total"]["cost_usd"] is None


def test_hook_exits_zero_when_bundled_pricing_is_malformed(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "data").mkdir()
    shutil.copy(SCRIPT, plugin / "scripts" / "token_usage.py")
    (plugin / "data" / "pricing.json").write_text("{not json")
    t = write_jsonl(plugin / "t.jsonl", [
        user("2026-07-01T10:00:00Z", command="/go"),
        assistant("2026-07-01T10:00:05Z", usage(out=100), request_id="r1"),
    ])
    r = _run_hook_from_plugin_tree(plugin, tmp_path, t)
    assert r.returncode == 0, r.stderr
    assert "ignoring malformed pricing file" in r.stderr
    ledger = json.loads((tmp_path / "ledger" / "s1.json").read_text())
    assert ledger["total"]["unpriced_models"] == ["claude-fable-5"]
    assert ledger["total"]["cost_usd"] is None

