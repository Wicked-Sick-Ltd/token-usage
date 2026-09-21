"""examples/statusline.ps1: structural checks plus optional `pwsh` smoke runs.

The structural checks read the script source, so they run everywhere. The smoke
runs need PowerShell 7+ and are skipped without it. No test creates a symlink:
`latest.json` is written as an ordinary file, which is exactly what a Windows
host without symlink privileges leaves behind.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUSLINE = REPO_ROOT / "examples" / "statusline.ps1"

FORBIDDEN_HOST_OUTPUT = (
    "Write-Error",
    "Write-Warning",
    "Write-Host",
    "Write-Information",
    "Write-Verbose",
    "Write-Debug",
)

SESSION_ID = "abc123-session"


def _script_text():
    assert STATUSLINE.is_file(), "examples/statusline.ps1 is missing"
    return STATUSLINE.read_text(encoding="utf-8")


def _ledger_payload(cost=33.87, out_tokens=214000, top="/code-review"):
    return {
        "total": {"usage": {"output": out_tokens}, "cost_usd": cost},
        "by_label": {
            top: {"cost_usd": cost - 1, "usage": {"output": out_tokens - 1000}},
            "/commit": {"cost_usd": 0.27, "usage": {"output": 2400}},
        },
    }


def _write_ledger(ledger_dir: Path, name: str, payload=None) -> Path:
    """One ordinary JSON file — never a symlink."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / name
    path.write_text(json.dumps(payload if payload is not None else _ledger_payload()),
                    encoding="utf-8")
    assert not path.is_symlink()
    return path


def _statusline(ledger_dir, stdin_text, extra_env=None):
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(ledger_dir)}
    env.update(extra_env or {})
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(STATUSLINE)],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


needs_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None,
                                reason="pwsh (PowerShell 7+) not installed")


# --- structural -------------------------------------------------------------

def test_statusline_header_requires_powershell_7():
    head = "\n".join(_script_text().splitlines()[:20])
    assert re.search(r"PowerShell 7", head)
    assert "pwsh" in head


def test_statusline_reads_statusline_json_from_stdin():
    text = _script_text()
    assert "ReadToEnd" in text
    assert "session_id" in text


def test_statusline_prefers_the_session_ledger_over_latest():
    text = _script_text()
    session_at = text.index("$sessionId.json") if "$sessionId.json" in text else -1
    latest_at = text.index("'latest.json'")
    assert session_at != -1, "the script must build <session_id>.json"
    assert session_at < latest_at, "the session ledger must be tried first"


def test_statusline_sanitizes_the_session_id_like_the_hook():
    # The hook names the ledger after re.sub(r"[^A-Za-z0-9_-]", "", session_id);
    # the same filter here keeps a hostile id inside the ledger directory.
    assert "[^A-Za-z0-9_-]" in _script_text()


def test_statusline_honors_ledger_dir_override():
    assert "TOKEN_USAGE_LEDGER_DIR" in _script_text()


def test_statusline_falls_back_to_default_cache_dir():
    text = _script_text()
    assert ".cache" in text and "token-usage" in text


def test_statusline_documents_the_fallback_order():
    text = _script_text()
    lowered = text.lower()
    assert "<session_id>.json" in lowered
    assert "fall back" in lowered or "fallback" in lowered
    # The pointer is best-effort on Windows; the docs must not promise it.
    assert "symlink" in lowered


def test_statusline_parses_with_convertfrom_json_only():
    text = _script_text()
    assert re.search(r"ConvertFrom-Json", text)
    assert "jq" not in text


def test_statusline_reads_total_output_and_cost():
    text = _script_text()
    assert "cost_usd" in text
    assert "usage.output" in text.replace(" ", "")


def test_statusline_finds_top_activity_from_by_label():
    text = _script_text()
    assert "by_label" in text
    assert re.search(r"top:\s", text, re.IGNORECASE)


def test_statusline_never_writes_to_the_host_streams():
    text = _script_text()
    for token in FORBIDDEN_HOST_OUTPUT:
        assert token not in text, token
    tail = text[text.lower().rfind("} catch {"):]
    assert "exit 0" in tail


def test_statusline_documents_cursor_live_not_latest_json():
    lowered = _script_text().lower()
    assert "cursor" in lowered
    assert "live" in lowered


# --- pwsh smoke -------------------------------------------------------------

@needs_pwsh
def test_statusline_resolves_the_stdin_session_ledger_first(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, f"{SESSION_ID}.json", _ledger_payload())
    # A different aggregate behind latest.json proves which file was read.
    _write_ledger(ledger_dir, "latest.json",
                  _ledger_payload(cost=1.11, out_tokens=2000, top="/stale"))
    r = _statusline(ledger_dir, json.dumps({"session_id": SESSION_ID}))
    assert r.returncode == 0
    assert r.stderr.strip() == ""
    assert "33.87" in r.stdout
    assert "/code-review" in r.stdout
    assert "/stale" not in r.stdout
    assert "1.11" not in r.stdout


@needs_pwsh
def test_statusline_falls_back_to_latest_when_the_session_has_no_ledger(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, "latest.json",
                  _ledger_payload(cost=4.5, out_tokens=9000, top="/fallback"))
    r = _statusline(ledger_dir, json.dumps({"session_id": "no-ledger-yet"}))
    assert r.returncode == 0
    assert r.stderr.strip() == ""
    assert "4.5" in r.stdout
    assert "/fallback" in r.stdout


@needs_pwsh
def test_statusline_falls_back_to_latest_without_a_session_id(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, "latest.json",
                  _ledger_payload(cost=2.25, out_tokens=3000, top="/no-id"))
    r = _statusline(ledger_dir, json.dumps({"cwd": "/tmp"}))
    assert r.returncode == 0
    assert "/no-id" in r.stdout


@needs_pwsh
def test_statusline_formats_tokens_and_cost(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, f"{SESSION_ID}.json",
                  _ledger_payload(cost=33.87, out_tokens=214000))
    r = _statusline(ledger_dir, json.dumps({"session_id": SESSION_ID}))
    assert r.returncode == 0
    assert "214" in r.stdout and "k" in r.stdout
    assert "33.87" in r.stdout


@needs_pwsh
@pytest.mark.parametrize("stdin_text", ["", "   ", "{not-json", "null", "[]"])
def test_statusline_unusable_stdin_still_tries_latest(tmp_path, stdin_text):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, "latest.json",
                  _ledger_payload(cost=7.5, out_tokens=5000, top="/salvage"))
    r = _statusline(ledger_dir, stdin_text)
    assert r.returncode == 0
    assert r.stderr.strip() == ""
    assert "/salvage" in r.stdout


@needs_pwsh
@pytest.mark.parametrize("stdin_text", ["", "{not-json", '{"session_id": "abc"}'])
def test_statusline_is_silent_without_any_ledger(tmp_path, stdin_text):
    r = _statusline(tmp_path / "empty", stdin_text)
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert r.stderr.strip() == ""


@needs_pwsh
def test_statusline_is_silent_on_a_malformed_session_ledger(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / f"{SESSION_ID}.json").write_text("{not-json", encoding="utf-8")
    r = _statusline(ledger_dir, json.dumps({"session_id": SESSION_ID}))
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert r.stderr.strip() == ""


@needs_pwsh
def test_statusline_is_silent_on_an_empty_session_ledger(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / f"{SESSION_ID}.json").write_text("", encoding="utf-8")
    r = _statusline(ledger_dir, json.dumps({"session_id": SESSION_ID}))
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert r.stderr.strip() == ""


@needs_pwsh
def test_statusline_session_id_cannot_escape_the_ledger_dir(tmp_path):
    outside = tmp_path / "outside"
    _write_ledger(outside, "secret.json",
                  _ledger_payload(cost=99.99, out_tokens=1, top="/leak"))
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    r = _statusline(ledger_dir, json.dumps({"session_id": "../outside/secret"}))
    assert r.returncode == 0
    assert "/leak" not in r.stdout
    assert "99.99" not in r.stdout


@needs_pwsh
def test_statusline_omits_top_activity_when_by_label_is_empty(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, f"{SESSION_ID}.json",
                  {"total": {"usage": {"output": 500}, "cost_usd": 0.5},
                   "by_label": {}})
    r = _statusline(ledger_dir, json.dumps({"session_id": SESSION_ID}))
    assert r.returncode == 0
    assert "top:" not in r.stdout
    assert "500" in r.stdout


@needs_pwsh
def test_statusline_null_cost_renders_a_question_mark(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_ledger(ledger_dir, f"{SESSION_ID}.json",
                  {"total": {"usage": {"output": 120}, "cost_usd": None},
                   "by_label": {"/go": {"cost_usd": None}}})
    r = _statusline(ledger_dir, json.dumps({"session_id": SESSION_ID}))
    assert r.returncode == 0
    assert "?" in r.stdout


@needs_pwsh
def test_statusline_runs_under_powershell_7(tmp_path):
    r = subprocess.run(["pwsh", "-NoProfile", "-Command",
                        "$PSVersionTable.PSVersion.Major"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0
    assert int(r.stdout.strip()) >= 7
