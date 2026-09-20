"""Portable live terminal polling: run_live loop and CLI wiring."""
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from conftest import SCRIPT, assistant, usage, user, write_jsonl

LIVE_CLEAR = "\x1b[2J\x1b[H"
FIXED_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def seed_two_sessions(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    a = write_jsonl(proj / "-Users-x-alpha" / "aaa-111.jsonl", [
        user("2026-06-10T10:00:00Z"),
        assistant("2026-06-10T10:00:01Z", usage(out=10), request_id="r1"),
    ])
    b = write_jsonl(proj / "-Users-x-beta" / "bbb-222.jsonl", [
        user("2026-06-12T10:00:00Z"),
        assistant("2026-06-12T10:00:01Z", usage(out=20), request_id="r2"),
    ])
    os.utime(a, (1_700_000_000, 1_700_000_000))
    os.utime(b, (1_700_000_100, 1_700_000_100))
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.delenv("TOKEN_USAGE_TRANSCRIPT", raising=False)
    monkeypatch.chdir(tmp_path)
    return a, b


def test_run_live_finite_iterations_with_injected_sleep(tu, tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    sleeps = []
    chunks = []

    tu.run_live(
        transcript=None,
        runtime="claude",
        interval=1.25,
        iterations=2,
        output=chunks.append,
        sleep_fn=lambda s: sleeps.append(s),
        isatty_fn=lambda: False,
        clock_fn=lambda: FIXED_NOW,
    )

    assert sleeps == [1.25]
    text = "".join(chunks)
    assert text.count("**Total**") == 2
    assert "20" in text  # newest session output tokens


def test_run_live_tty_clear_between_refreshes(tu, tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    chunks = []

    tu.run_live(
        transcript=None,
        runtime="claude",
        interval=0.01,
        iterations=2,
        output=chunks.append,
        sleep_fn=lambda _s: None,
        isatty_fn=lambda: True,
        clock_fn=lambda: FIXED_NOW,
    )

    joined = "".join(chunks)
    assert joined.count(LIVE_CLEAR) == 1
    assert joined.index(LIVE_CLEAR) < joined.rindex("**Total**")


def test_run_live_redirected_uses_timestamp_separator(tu, tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    chunks = []
    expected_ts = "2026-06-15T12:00:00Z"

    tu.run_live(
        transcript=None,
        runtime="claude",
        interval=0.01,
        iterations=2,
        output=chunks.append,
        sleep_fn=lambda _s: None,
        isatty_fn=lambda: False,
        clock_fn=lambda: FIXED_NOW,
    )

    joined = "".join(chunks)
    assert LIVE_CLEAR not in joined
    assert f"\n--- {expected_ts} ---\n" in joined


def test_run_live_rediscovers_latest_each_cycle(tu, tmp_path, monkeypatch):
    a, _b = seed_two_sessions(tmp_path, monkeypatch)
    chunks = []

    def sleep_flip(_interval):
        os.utime(a, (1_800_000_200, 1_800_000_200))

    tu.run_live(
        transcript=None,
        runtime="claude",
        interval=0.01,
        iterations=2,
        output=chunks.append,
        sleep_fn=sleep_flip,
        isatty_fn=lambda: False,
        clock_fn=lambda: FIXED_NOW,
    )

    joined = "".join(chunks)
    first, second = joined.split("--- 2026-06-15T12:00:00Z ---")
    assert "20" in first
    assert "10" in second


def test_run_live_explicit_source_stays_fail_closed(tu, tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    missing = tmp_path / "gone.jsonl"
    with pytest.raises(SystemExit) as exc:
        tu.run_live(
            transcript=str(missing),
            runtime="claude",
            interval=0.01,
            iterations=1,
            output=lambda _s: None,
            sleep_fn=lambda _s: None,
            isatty_fn=lambda: False,
            clock_fn=lambda: FIXED_NOW,
        )
    assert str(exc.value) == f"token-usage: transcript not found: {missing}"


def test_run_live_rejects_non_positive_interval(tu):
    with pytest.raises(ValueError, match="interval"):
        tu.run_live(interval=0, iterations=1, output=lambda _s: None, sleep_fn=lambda _s: None)


def test_run_live_rejects_non_positive_iterations(tu):
    with pytest.raises(ValueError, match="iterations"):
        tu.run_live(interval=1.0, iterations=0, output=lambda _s: None, sleep_fn=lambda _s: None)


def test_run_live_agents_and_models_flags(tu, tmp_path):
    t = write_jsonl(tmp_path / "sess.jsonl", [
        user("2026-06-12T10:00:00Z", command="/review"),
        assistant("2026-06-12T10:00:01Z", usage(out=100), request_id="r1", model="claude-fable-5"),
    ])
    chunks = []
    tu.run_live(
        transcript=str(t),
        runtime="claude",
        interval=0.01,
        iterations=1,
        show_agents=True,
        show_models=True,
        output=chunks.append,
        sleep_fn=lambda _s: None,
        isatty_fn=lambda: False,
        clock_fn=lambda: FIXED_NOW,
    )
    text = "".join(chunks)
    assert "claude-fable-5" in text
    assert "`/review`" in text


def test_cli_live_keyboard_interrupt_exits_zero(tu, monkeypatch):
    def boom(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(tu, "run_live", boom)
    monkeypatch.setattr(sys, "argv", ["token_usage.py", "live"])

    tu.main()  # KeyboardInterrupt swallowed; normal return == exit 0


def test_cli_live_subcommand_help():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "live", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--interval" in r.stdout
    assert "--iterations" in r.stdout
    assert "--agents" in r.stdout
    assert "--models" in r.stdout


def test_cli_live_validates_interval(tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "live", "--interval", "0", "--iterations", "1"],
        capture_output=True,
        text=True,
        env={**os.environ, "TOKEN_USAGE_PROJECTS_DIR": str(tmp_path / "projects")},
        check=False,
    )
    assert r.returncode != 0
    assert "interval" in r.stderr.lower()


def test_cli_live_validates_iterations(tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "live", "--iterations", "0"],
        capture_output=True,
        text=True,
        env={**os.environ, "TOKEN_USAGE_PROJECTS_DIR": str(tmp_path / "projects")},
        check=False,
    )
    assert r.returncode != 0
    assert "iterations" in r.stderr.lower()
