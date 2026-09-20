"""Self-contained HTML dashboard: data, render, output, and CLI."""
import html
import os
import re
import subprocess
import sys

import pytest
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


def _fail_on_tmp(monkeypatch, method, message):
    """Make Path.<method> raise for the temp file only, leaving the rest alone."""
    import pathlib
    original = getattr(pathlib.Path, method)

    def patched(self, *a, **kw):
        if self.name.endswith(".tmp"):
            raise OSError(message)
        return original(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, method, patched)


def test_write_text_output_removes_the_temp_file_when_the_write_fails(
        tu, tmp_path, monkeypatch):
    # A full disk fails part way through the write, so the temp file exists on
    # disk holding a truncated dashboard. The caller hears about the failure,
    # but that debris must not be left in the user's output directory.
    import pathlib
    out = tmp_path / "dash.html"
    original = pathlib.Path.write_text

    def patched(self, data, *a, **kw):
        if self.name.endswith(".tmp"):
            original(self, data[:5], *a, **kw)
            raise OSError("No space left on device")
        return original(self, data, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", patched)
    with pytest.raises(OSError, match="No space left on device"):
        tu.write_text_output("<html>ok</html>", out)
    assert not list(tmp_path.glob("*.tmp"))
    assert not out.exists()


def test_write_text_output_removes_the_temp_file_when_the_replace_fails(
        tu, tmp_path, monkeypatch):
    out = tmp_path / "dash.html"
    out.write_text("<html>previous</html>", encoding="utf-8")
    _fail_on_tmp(monkeypatch, "replace", "Permission denied")
    with pytest.raises(OSError):
        tu.write_text_output("<html>new</html>", out)
    assert not list(tmp_path.glob("*.tmp"))
    # Atomic replacement: a failed write leaves the prior output intact.
    assert out.read_text(encoding="utf-8") == "<html>previous</html>"


def test_write_text_output_survives_a_temp_file_that_is_already_gone(
        tu, tmp_path, monkeypatch):
    # Cleanup must not turn one failure into a different, more confusing one.
    import pathlib
    out = tmp_path / "dash.html"
    original_unlink = pathlib.Path.unlink

    def patched(self, *a, **kw):
        if self.name.endswith(".tmp"):
            raise FileNotFoundError(self)
        return original_unlink(self, *a, **kw)

    _fail_on_tmp(monkeypatch, "replace", "Permission denied")
    monkeypatch.setattr(pathlib.Path, "unlink", patched)
    with pytest.raises(OSError, match="Permission denied"):
        tu.write_text_output("<html>new</html>", out)


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


def _measurement_card(html_out):
    """The text of the summary section's Measurement card."""
    cards, _ = html_out.split("</section>", 1)
    return cards.split("Measurement", 1)[1].split("</div>", 1)[0]


def test_dashboard_measurement_card_names_the_mixed_tally(tu, tmp_path, monkeypatch):
    # "activity_only" alone, next to populated cost cards, reads like a
    # contradiction: the reader cannot tell whether the totals are a lower
    # bound from one weak session or from the whole corpus.
    seed_mixed_cursor_corpus(tu, tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01")
    card = _measurement_card(tu.render_dashboard(data, generated_at=GENERATED_AT))
    assert "activity_only (1 of 2 sessions)" in card


def test_dashboard_measurement_card_stays_clear_when_all_activity_only(
        tu, tmp_path, monkeypatch):
    zero_token_tree(tmp_path, monkeypatch)
    data = tu.dashboard_data(runtime="cursor", since="2020-01-01")
    card = _measurement_card(tu.render_dashboard(data, generated_at=GENERATED_AT))
    assert "activity_only" in card
    # "1 of 1" invites the reader to look for the measured remainder; "all"
    # says plainly that there is none.
    assert "all 1 session)" in card
    assert "1 of 1" not in card


def test_dashboard_measurement_card_is_bare_without_a_tally(tu, tmp_path, monkeypatch):
    # A Claude corpus records no per-session levels, so there is no tally to
    # qualify "exact" with and nothing to add.
    seed_projects(tmp_path, monkeypatch)
    data = tu.dashboard_data(since="36500d")
    assert data["measurements"] == {}
    card = _measurement_card(tu.render_dashboard(data, generated_at=GENERATED_AT))
    assert "exact" in card
    assert "session" not in card


@pytest.mark.parametrize("level,counts,expected", [
    ("activity_only", {"exact": 1, "activity_only": 1}, "activity_only (1 of 2 sessions)"),
    ("activity_only", {"activity_only": 1}, "activity_only (all 1 session)"),
    ("activity_only", {"activity_only": 3}, "activity_only (all 3 sessions)"),
    ("partial", {"exact": 99, "partial": 1}, "partial (1 of 100 sessions)"),
    ("exact", {"exact": 4}, "exact (all 4 sessions)"),
    ("exact", {}, "exact"),
    ("exact", {"exact": 0}, "exact"),
])
def test_measurement_card_label(tu, level, counts, expected):
    assert tu._dashboard_measurement_label(level, counts) == expected


def test_dashboard_activity_only_reads_only_the_scan_tally(tu):
    # dashboard_data always supplies "measurements", so an empty tally means
    # nothing was scanned — not that everything scanned was unmeasured.
    assert tu._dashboard_activity_only(
        {"measurements": {}, "summary": {"measurement": "activity_only"}}) is False
    assert tu._dashboard_activity_only(
        {"measurements": {}, "summary": {}}) is False
    assert tu._dashboard_activity_only(
        {"measurements": {"activity_only": 2}, "summary": {}}) is True
    assert tu._dashboard_activity_only(
        {"measurements": {"exact": 1, "activity_only": 1}, "summary": {}}) is False


def test_dashboard_data_always_supplies_a_measurement_tally(tu, tmp_path, monkeypatch):
    # The predicate above drops its summary fallback, so this is the contract
    # that keeps it correct: every dashboard_data result carries a tally.
    seed_projects(tmp_path, monkeypatch)
    assert "measurements" in tu.dashboard_data(since="36500d")
    assert "measurements" in tu.dashboard_data(since="0d")
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(tmp_path / "gone"))
    assert "measurements" in tu.dashboard_data(since="36500d")


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


def test_dashboard_names_each_scan_warning_once_on_stderr(tu, tmp_path, monkeypatch,
                                                          capsys):
    # The page was deduplicated but stderr was not, so the terminal still
    # showed one problem four times while the page it produced showed it once.
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(tmp_path / "missing-projects"))
    monkeypatch.setenv("TOKEN_USAGE_LEDGER_DIR", str(tmp_path / "cache"))
    tu.dashboard_data(runtime="claude", since="36500d", warnings=[])
    err = capsys.readouterr().err
    assert err.count("no readable Claude Code projects directory") == 1


def test_cli_dashboard_names_each_scan_warning_once_on_stderr(tu, tmp_path, monkeypatch):
    out = tmp_path / "dash.html"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "dashboard", "--since", "36500d",
         "--output", str(out)],
        capture_output=True, text=True, check=False,
        env={**os.environ,
             "TOKEN_USAGE_PROJECTS_DIR": str(tmp_path / "missing-projects"),
             "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "cache")},
    )
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("no readable Claude Code projects directory") == 1


def test_warning_dedup_is_scoped_not_global(tu, capsys):
    # A once-per-process flag would fix the dashboard and quietly break every
    # other command: a warning that recurs legitimately — a second history run
    # in an MCP server, a live refresh after the corpus changed — must still be
    # reported each time it happens.
    tu.warn("a recurring problem")
    tu.warn("a recurring problem")
    assert capsys.readouterr().err.count("a recurring problem") == 2

    with tu.deduped_warnings():
        tu.warn("a recurring problem")
        tu.warn("a recurring problem")
        tu.warn("a different problem")
    err = capsys.readouterr().err
    assert err.count("a recurring problem") == 1
    assert err.count("a different problem") == 1

    # The scope ends with the command that opened it.
    tu.warn("a recurring problem")
    tu.warn("a recurring problem")
    assert capsys.readouterr().err.count("a recurring problem") == 2


def test_warning_dedup_scope_still_collects_every_occurrence(tu, capsys):
    # Suppression is about the terminal, not about the caller's list: a
    # surface that counts warnings keeps seeing them, and dedupes on purpose.
    warnings = []
    with tu.deduped_warnings():
        tu.warn("same problem", warnings)
        tu.warn("same problem", warnings)
    assert warnings == ["same problem", "same problem"]
    assert capsys.readouterr().err.count("same problem") == 1


def test_warning_dedup_scope_is_released_when_the_body_raises(tu, capsys):
    with pytest.raises(ValueError), tu.deduped_warnings():
        tu.warn("noted once")
        raise ValueError("boom")
    capsys.readouterr()
    tu.warn("noted once")
    tu.warn("noted once")
    assert capsys.readouterr().err.count("noted once") == 2


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


def _axis_labels(svg):
    return re.findall(r'class="axis">([^<]*)</text>', svg)


def _bar_titles(svg):
    return re.findall(r"<title>([^<]*)</title>", svg)


def thirty_days(cost=1.0):
    return [{"key": f"2026-06-{d:02d}", "cost_usd": cost * d,
             "usage": {"output": d, "input": d, "cache_read": 0,
                       "cache_5m": 0, "cache_1h": 0, "requests": d},
             "calls": 1}
            for d in range(1, 31)]


def test_dashboard_chart_thins_thirty_day_axis_labels(tu):
    svg = tu._dashboard_svg_chart(thirty_days(), False)
    labels = [ln for ln in _axis_labels(svg) if ln]
    assert 0 < len(labels) <= 10, labels
    # Every bar keeps its own hover title even when its label is dropped.
    assert len(_bar_titles(svg)) == 30


def test_dashboard_chart_keeps_every_label_for_a_short_window(tu):
    svg = tu._dashboard_svg_chart(thirty_days()[:7], False)
    assert len([ln for ln in _axis_labels(svg) if ln]) == 7


def test_dashboard_chart_labels_the_maximum_scale(tu):
    svg = tu._dashboard_svg_chart(thirty_days(), False)
    assert tu.fmt_cost(30.0) in svg
    assert 'class="scale"' in svg


def test_dashboard_chart_scale_label_absent_without_measured_cost(tu):
    svg = tu._dashboard_svg_chart(thirty_days(), True)
    assert 'class="scale"' not in svg


def test_dashboard_model_rows_have_no_unreachable_partial_footnote(tu):
    # A by-model row IS one model: its cost is fully priced or None, never a
    # priced subtotal, so no model row can ever be marked partial.
    rows = [{"key": "claude-mystery-9", "partial": True, "cost_usd": 1.0,
             "usage": tu.empty_usage(), "calls": 1}]
    notes = tu._dashboard_partial_footnotes({"top_models": rows})
    assert notes == []
