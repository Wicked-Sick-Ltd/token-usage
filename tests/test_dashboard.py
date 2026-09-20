"""Self-contained HTML dashboard: data, render, output, and CLI."""
import html
import os
import re
import subprocess
import sys

from conftest import SCRIPT, assistant, usage, user, write_jsonl
from test_cursor_adapter import build_cursor_tree
from test_cursor_cli import zero_token_tree


def seed_projects(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    write_jsonl(proj / "-Users-x-repo-one" / "s1.jsonl", [
        user("2026-06-10T10:00:00Z", command="/review"),
        assistant("2026-06-10T10:00:01Z", usage(inp=200, out=100, cache_read=50),
                  request_id="r1"),
    ])
    write_jsonl(proj / "-Users-x-repo-two" / "s2.jsonl", [
        user("2026-06-12T10:00:00Z", command="/commit"),
        assistant("2026-06-12T10:00:01Z", usage(inp=80, out=50), request_id="r2"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    return proj


def seed_escape_model(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    evil = '</td><script>alert(1)</script>'
    write_jsonl(proj / "-Users-x-evil" / "s1.jsonl", [
        user("2026-06-10T10:00:00Z", command="/review"),
        assistant("2026-06-10T10:00:01Z", usage(out=10),
                  model=evil, request_id="r1"),
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


GENERATED_AT = "2026-06-15T12:00:00Z"


def test_dashboard_data_aggregates_claude_history(tu, tmp_path, monkeypatch):
    seed_projects(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="claude", since="36500d", project=None)
    assert data["summary"]["usage"]["output"] == 150
    assert data["summary"]["usage"]["input"] == 280
    assert data["summary"]["sessions"] == 2
    assert data["summary"]["cost_usd"] is not None and data["summary"]["cost_usd"] > 0
    assert len(data["by_day"]) >= 1
    assert {r["key"] for r in data["top_projects"]} == {
        "-Users-x-repo-one", "-Users-x-repo-two"
    }
    assert "/review" in {r["key"] for r in data["top_commands"]}


def test_render_dashboard_deterministic_cards(tu, tmp_path, monkeypatch):
    seed_projects(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="claude", since="36500d")
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    html2 = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert html_out == html2
    assert "150" in html_out or "150" in html_out.replace(",", "")
    assert GENERATED_AT in html_out
    assert "Estimated cost" in html_out
    assert "Output tokens" in html_out


def test_render_dashboard_inline_css_and_svg(tu, tmp_path, monkeypatch):
    seed_projects(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="claude", since="36500d")
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert "<style>" in html_out and "</style>" in html_out
    assert "<svg" in html_out and "</svg>" in html_out
    assert 'rel="stylesheet"' not in html_out


def test_render_dashboard_forbids_external_assets(tu, tmp_path, monkeypatch):
    seed_projects(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="claude", since="36500d")
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT).lower()
    for forbidden in (
        "http://",
        "https://",
        "//cdn",
        "@import",
        'src="http',
        "<script",
        "<iframe",
        "<link ",
        "<object",
        "<embed",
    ):
        assert forbidden not in html_out


def test_render_dashboard_escapes_labels(tu, tmp_path, monkeypatch):
    seed_escape_model(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="claude", since="36500d")
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert "<script>alert(1)</script>" not in html_out
    evil = '</td><script>alert(1)</script>'
    assert html.escape(evil) in html_out


def test_render_dashboard_empty_history(tu, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(tmp_path / "missing-projects"))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    data = tu.dashboard_data(runtime="claude", since="36500d")
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert "nothing was scanned" in html_out.lower()
    assert "no sessions matched" not in html_out.lower()


def test_render_dashboard_activity_only_disclosure(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="36500d")
    assert data.get("runtime") == "cursor"
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    lower = html_out.lower()
    assert "activity" in lower and "unmeasured" in lower
    cards, _ = html_out.split("</section>", 1)
    for label in ("Estimated cost", "Output tokens", "Input tokens", "Cache reads"):
        chunk = cards.split(label, 1)[1].split("</div>", 1)[0]
        assert "unmeasured" in chunk.lower()
        assert ">0<" not in chunk.replace("— (unmeasured)", "")


def test_write_text_output_atomic_file(tu, tmp_path):
    out = tmp_path / "dash.html"
    tu.write_text_output("<html>ok</html>", out)
    assert out.read_text(encoding="utf-8") == "<html>ok</html>"
    assert not list(tmp_path.glob("*.tmp"))


def test_write_text_output_stdout(tu, tmp_path, capsys):
    tu.write_text_output("<html>stdout</html>", "-")
    captured = capsys.readouterr()
    assert captured.out == "<html>stdout</html>"
    assert captured.err == ""


def test_cli_dashboard_writes_file(tu, tmp_path, monkeypatch):
    seed_projects(tmp_path, monkeypatch)
    out = tmp_path / "token-usage-dashboard.html"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "dashboard", "--since", "36500d",
         "--output", str(out)],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    assert "<!DOCTYPE html>" in out.read_text(encoding="utf-8")
    assert r.stdout == ""


def test_cli_dashboard_stdout_mode(tu, tmp_path, monkeypatch):
    seed_projects(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "dashboard", "--since", "36500d", "--output", "-"],
        capture_output=True,
        text=True,
        env=_env(tmp_path, TOKEN_USAGE_PROJECTS_DIR=str(tmp_path / "projects")),
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "<!DOCTYPE html>" in r.stdout
    for line in r.stderr.splitlines():
        assert line.startswith("token-usage:"), line


def test_render_dashboard_partial_command_cost_asterisk(tu, tmp_path, monkeypatch):
    from test_top_consumers import seed as seed_top

    proj = seed_top(tmp_path, monkeypatch)
    write_jsonl(proj / "-Users-x-two" / "s4.jsonl", [
        user("2026-06-14T10:00:00Z", command="/review"),
        assistant("2026-06-14T10:00:01Z", usage(out=500_000),
                  model="claude-mystery-9", request_id="r5"),
    ])
    data = tu.dashboard_data(runtime="claude", since="2026-01-01")
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert "$7.00*" in html_out
    assert "partially priced" in html_out.lower()
    assert "lower bound" in html_out.lower() or "unpriced models" in html_out.lower()


def test_dashboard_data_project_dir_scopes_cursor(tu, tmp_path, monkeypatch):
    from test_cursor_cli import zero_token_tree

    project = tmp_path / "alpha-repo"
    project.mkdir()
    zero_token_tree(tmp_path, monkeypatch, project_folder=str(project))
    scoped = tu.dashboard_data(runtime="cursor", since="2020-01-01",
                               project_dir=str(project))
    assert scoped["summary"]["sessions"] == 1
    elsewhere = tu.dashboard_data(runtime="cursor", since="2020-01-01",
                                  project_dir=str(tmp_path / "beta-repo"))
    assert elsewhere["summary"]["sessions"] == 0


def test_dashboard_data_cursor_runtime(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    data = tu.dashboard_data(runtime="cursor", since="36500d", project="alpha")
    assert data["runtime"] == "cursor"
    assert data["summary"]["sessions"] >= 1


def seed_mixed_cursor_corpus(tu, tmp_path, monkeypatch):
    """A Cursor corpus holding one measured session and one activity-only one."""
    from test_cursor_adapter import ledger_record, write_ledger

    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(
        cursor_root,
        composer_id="comp-unmeasured",
        bubble_headers=[{"bubbleId": "zero-user", "type": 1},
                        {"bubbleId": "zero-asst", "type": 2}],
        project_folder=str((tmp_path / "quiet-repo").resolve()),
    )
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    write_ledger(tu, tmp_path / "cache", "conv-measured", [
        ledger_record("beforeSubmitPrompt", conversation_id="conv-measured",
                      ts="2026-06-12T10:00:00Z", prompt="ship the dashboard",
                      workspace_roots=[str((tmp_path / "loud-repo").resolve())]),
        ledger_record("stop", conversation_id="conv-measured",
                      ts="2026-06-12T10:00:30Z", model="claude-sonnet-4",
                      tokens={"input_tokens": 4000, "output_tokens": 900},
                      workspace_roots=[str((tmp_path / "loud-repo").resolve())]),
    ])
    return cursor_root


def test_dashboard_mixed_corpus_is_not_wholly_activity_only(tu, tmp_path, monkeypatch):
    # One unmeasured session in a corpus does not unmeasure the rest: hiding
    # every card because a single Cursor composer reported no tokens threw
    # away totals the scan really did measure.
    seed_mixed_cursor_corpus(tu, tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01")
    assert set(data["measurements"]) == {"exact", "activity_only"}
    assert tu._dashboard_activity_only(data) is False


def test_dashboard_mixed_corpus_retains_measured_totals(tu, tmp_path, monkeypatch):
    seed_mixed_cursor_corpus(tu, tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01")
    assert data["summary"]["usage"]["output"] == 900
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    cards, _ = html_out.split("</section>", 1)
    for label in ("Estimated cost", "Output tokens", "Input tokens"):
        chunk = cards.split(label, 1)[1].split("</div>", 1)[0]
        assert "unmeasured" not in chunk.lower(), label
    assert "900" in cards
    # The lower bound is disclosed by the scan footnote, not by blanking cards.
    assert "activity-only" in html_out
    assert "unmeasured usage, not free usage" in html_out


def test_dashboard_mixed_corpus_chart_keeps_its_bars(tu, tmp_path, monkeypatch):
    seed_mixed_cursor_corpus(tu, tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01")
    svg = tu._dashboard_svg_chart(data["by_day"], tu._dashboard_activity_only(data))
    assert 'class="bar"' in svg
    assert "Costs unmeasured (activity-only)" not in svg
    heights = [float(h) for h in re.findall(r'class="bar"', svg) and
               re.findall(r'height="([\d.]+)" class="bar"', svg)]
    assert heights and max(heights) > 0


def test_dashboard_all_activity_only_stays_unmeasured(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01")
    assert set(data["measurements"]) == {"activity_only"}
    assert tu._dashboard_activity_only(data) is True
    svg = tu._dashboard_svg_chart(data["by_day"], True)
    assert "Costs unmeasured (activity-only)" in svg


def test_dashboard_deduplicates_repeated_scan_warnings(tu, tmp_path, monkeypatch):
    # dashboard_data runs four history scans plus the partial-enrichment scan
    # over one shared warnings list, so every corpus warning arrived five times.
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(tmp_path / "missing-projects"))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    warnings = []
    data = tu.dashboard_data(runtime="claude", since="36500d", warnings=warnings)
    assert warnings
    assert len(warnings) == len(set(warnings))
    assert data["warnings"] == warnings
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert html_out.count("Claude Code projects directory") == 1


def test_dashboard_renders_each_named_warning_once(tu, tmp_path, monkeypatch):
    # The rendered warnings note names its warnings, so a five-times-repeated
    # scan warning printed "5 warning(s)" for one problem.
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(
        cursor_root,
        bubble_headers=[{"bubbleId": "user-1", "type": 1},
                        {"bubbleId": "ghost-bubble", "type": 2}],
    )
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    warnings = []
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01", warnings=warnings)
    assert any("ghost-bubble" in w for w in warnings)
    assert len(warnings) == len(set(warnings))
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    assert html_out.count("ghost-bubble") == 1
    assert "1 warning(s)" in html_out
