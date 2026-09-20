"""Self-contained HTML dashboard: data, render, output, and CLI."""
import html
import os
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
    assert "http://" not in html_out
    assert "https://" not in html_out
    assert "<script" not in html_out
    assert "<iframe" not in html_out


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
    assert "No sessions" in html_out or "no sessions" in html_out.lower()
    assert "nothing was scanned" in html_out.lower() or "No readable" in html_out


def test_render_dashboard_activity_only_disclosure(tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="36500d")
    assert data.get("runtime") == "cursor"
    html_out = tu.render_dashboard(data, generated_at=GENERATED_AT)
    lower = html_out.lower()
    assert "activity" in lower and "unmeasured" in lower


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
    assert r.stderr == "" or "token-usage:" in r.stderr  # progress on stderr ok


def test_dashboard_data_cursor_runtime(tu, tmp_path, monkeypatch):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    monkeypatch.setenv("TOKEN_USAGE_CURSOR_DIR", str(cursor_root))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    data = tu.dashboard_data(runtime="cursor", since="36500d", project="alpha")
    assert data["runtime"] == "cursor"
    assert data["summary"]["sessions"] >= 1
