"""Portable live terminal polling: run_live loop and CLI wiring."""
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone

import pytest
from conftest import SCRIPT, assistant, usage, user, write_jsonl
from test_cursor_adapter import build_cursor_tree
from test_cursor_cli import _env

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
    assert joined.count(tu.LIVE_CLEAR) == 1
    assert joined.index(tu.LIVE_CLEAR) < joined.rindex("**Total**")


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
    assert tu.LIVE_CLEAR not in joined
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


class _FlushTrackingStream:
    def __init__(self):
        self.chunks = []
        self.flush_count = 0

    def write(self, text):
        self.chunks.append(text)

    def flush(self):
        self.flush_count += 1


def test_run_live_default_output_flushes_each_frame(tu, tmp_path, monkeypatch):
    seed_two_sessions(tmp_path, monkeypatch)
    stream = _FlushTrackingStream()

    tu.run_live(
        transcript=None,
        runtime="claude",
        interval=0.01,
        iterations=2,
        output_stream=stream,
        sleep_fn=lambda _s: None,
        isatty_fn=lambda: False,
        clock_fn=lambda: FIXED_NOW,
    )

    assert stream.flush_count == 2
    assert "**Total**" in "".join(stream.chunks)


def test_run_live_dedupes_warnings_across_frames(tu, tmp_path, monkeypatch):
    t = write_jsonl(tmp_path / "sess.jsonl", [
        user("2026-06-12T10:00:00Z"),
        assistant("2026-06-12T10:00:01Z", usage(out=5), request_id="r1"),
    ])
    warnings = []
    real_agg = tu._session_aggregate

    def agg_with_repeat(*args, **kwargs):
        data = real_agg(*args, **kwargs)
        wlist = kwargs.get("warnings") if "warnings" in kwargs else args[4]
        wlist.append("repeated live warning")
        return data

    monkeypatch.setattr(tu, "_session_aggregate", agg_with_repeat)
    tu.run_live(
        transcript=str(t),
        runtime="claude",
        interval=0.01,
        iterations=2,
        warnings=warnings,
        output=lambda _s: None,
        sleep_fn=lambda _s: None,
        isatty_fn=lambda: False,
        clock_fn=lambda: FIXED_NOW,
    )
    assert warnings.count("repeated live warning") == 1


def test_live_cursor_explicit_invalid_selector_fail_closed(tmp_path):
    cursor_root = tmp_path / "cursor-user"
    build_cursor_tree(cursor_root)
    bogus = "comp-not-a-file-on-disk"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "live",
            "--runtime",
            "cursor",
            "--iterations",
            "1",
            bogus,
        ],
        capture_output=True,
        text=True,
        env=_env(tmp_path, cursor_root),
        check=False,
    )
    assert r.returncode != 0, r.stdout
    assert "Refactor token parser" not in r.stdout
    assert ".json" in r.stderr.lower()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX SIGINT subprocess semantics not exercised on Windows CI",
)
def test_cli_live_sigint_exits_zero_after_first_frame(tmp_path):
    proj = tmp_path / "projects"
    (tmp_path / "xdg").mkdir()
    transcript = write_jsonl(proj / "-Users-x-live" / "live-sigint.jsonl", [
        user("2026-06-12T10:00:00Z"),
        assistant("2026-06-12T10:00:01Z", usage(out=42), request_id="sig"),
    ])
    env = {
        **os.environ,
        "TOKEN_USAGE_PROJECTS_DIR": str(proj),
        "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(SCRIPT),
            "live",
            str(transcript),
            "--interval",
            "3600",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
        bufsize=0,
    )
    buf_parts = []
    frame_ready = threading.Event()

    def _read_stdout():
        assert proc.stdout is not None
        accumulated = ""
        while True:
            byte = proc.stdout.read(1)
            if not byte:
                break
            buf_parts.append(byte)
            accumulated += byte
            if "**Total**" in accumulated:
                frame_ready.set()
                return

    reader = threading.Thread(target=_read_stdout, daemon=True)
    reader.start()
    try:
        assert frame_ready.wait(timeout=10.0), (
            f"first frame not flushed: {''.join(buf_parts)!r} (rc={proc.poll()})"
        )
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
            pytest.fail("live did not exit after SIGINT within 5s")
        assert proc.returncode == 0, "".join(buf_parts)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
