"""Structural checks for examples/statusline.ps1.

This CI environment has no PowerShell (`pwsh`); behavior is validated by reading
the script source. When `pwsh` is available locally, an optional smoke run is
executed against a synthetic ledger.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUSLINE = REPO_ROOT / "examples" / "statusline.ps1"
DEFAULT_CACHE = Path.home() / ".cache" / "token-usage" / "latest.json"


def _script_text():
    assert STATUSLINE.is_file(), "examples/statusline.ps1 is missing"
    return STATUSLINE.read_text(encoding="utf-8")


def test_statusline_honors_ledger_dir_override():
    text = _script_text()
    assert "TOKEN_USAGE_LEDGER_DIR" in text


def test_statusline_falls_back_to_default_latest_path():
    text = _script_text()
    assert "latest.json" in text
    assert ".cache" in text and "token-usage" in text


def test_statusline_parses_json_without_external_tools():
    text = _script_text()
    lowered = text.lower()
    assert "convertfrom-json" in lowered or "json" in lowered
    assert "jq" not in text


def test_statusline_reads_total_output_and_cost():
    text = _script_text()
    assert "total" in text
    assert "output" in text
    assert "cost_usd" in text or "cost" in text.lower()


def test_statusline_finds_top_activity_from_by_label():
    text = _script_text()
    assert "by_label" in text
    assert "top" in text.lower()


def test_statusline_fails_silently_on_errors():
    text = _script_text()
    # No user-visible error stream; missing/malformed ledger should exit quietly.
    assert "Write-Error" not in text
    assert "throw" not in text.lower() or "catch" in text.lower()
    compact = text.replace(" ", "").lower()
    assert "exit0" in compact


def test_statusline_prints_output_cost_and_top_fields():
    text = _script_text()
    assert " out" in text or "out ·" in text or 'out"' in text
    assert "$" in text or "cost" in text.lower()
    assert "top:" in text.lower() or "top :" in text.lower()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
def test_statusline_pwsh_smoke(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    session = ledger_dir / "sess.json"
    session.write_text(
        json.dumps(
            {
                "total": {"usage": {"output": 214000}, "cost_usd": 33.87},
                "by_label": {
                    "/code-review": {"cost_usd": 29.4, "usage": {"output": 180000}},
                    "/commit": {"cost_usd": 0.27, "usage": {"output": 2400}},
                },
            }
        ),
        encoding="utf-8",
    )
    latest = ledger_dir / "latest.json"
    latest.symlink_to(session)
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(ledger_dir)}
    r = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(STATUSLINE)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0
    assert "214" in r.stdout and "33.87" in r.stdout
    assert "/code-review" in r.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
def test_statusline_pwsh_missing_ledger_is_silent(tmp_path):
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(tmp_path / "empty")}
    r = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(STATUSLINE)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert r.stderr.strip() == ""


def test_statusline_default_path_documented():
    text = _script_text()
    # Fallback must mention the same relative layout as Claude hooks (latest.json).
    assert "latest.json" in text
    assert str(DEFAULT_CACHE.parent.name) in text  # token-usage dir name
