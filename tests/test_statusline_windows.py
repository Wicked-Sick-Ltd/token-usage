"""Structural checks for examples/statusline.ps1.

This CI environment has no PowerShell (`pwsh`); behavior is validated by reading
the script source. When `pwsh` is available locally, optional smoke runs use a
real `latest.json` file (no symlinks).
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
DEFAULT_CACHE = Path.home() / ".cache" / "token-usage" / "latest.json"

FORBIDDEN_HOST_OUTPUT = (
    "Write-Error",
    "Write-Warning",
    "Write-Host",
    "Write-Information",
    "Write-Verbose",
    "Write-Debug",
)


def _script_text():
    assert STATUSLINE.is_file(), "examples/statusline.ps1 is missing"
    return STATUSLINE.read_text(encoding="utf-8")


def _sample_ledger_payload():
    return {
        "total": {"usage": {"output": 214000}, "cost_usd": 33.87},
        "by_label": {
            "/code-review": {"cost_usd": 29.4, "usage": {"output": 180000}},
            "/commit": {"cost_usd": 0.27, "usage": {"output": 2400}},
        },
    }


def _write_latest_json(ledger_dir: Path, payload=None) -> Path:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / "latest.json"
    body = json.dumps(payload if payload is not None else _sample_ledger_payload())
    path.write_text(body, encoding="utf-8")
    return path


def test_statusline_honors_ledger_dir_override():
    text = _script_text()
    assert "TOKEN_USAGE_LEDGER_DIR" in text


def test_statusline_falls_back_to_default_latest_path():
    text = _script_text()
    assert "latest.json" in text
    assert ".cache" in text and "token-usage" in text


def test_statusline_parses_with_convertfrom_json_only():
    text = _script_text()
    assert re.search(r"ConvertFrom-Json", text)
    assert "jq" not in text


def test_statusline_reads_total_output_and_cost():
    text = _script_text()
    assert "total" in text
    assert "cost_usd" in text
    assert ".usage.output" in text.replace(" ", "") or "usage.output" in text


def test_statusline_finds_top_activity_from_by_label():
    text = _script_text()
    assert "by_label" in text
    assert re.search(r"top:\s", text, re.IGNORECASE)


def test_statusline_outer_catch_exits_silently():
    text = _script_text()
    for token in FORBIDDEN_HOST_OUTPUT:
        assert token not in text, token
    assert re.search(r"\}\s*catch\s*\{", text)
    # Outer handler must swallow failures without host warnings/errors.
    tail = text[text.lower().rfind("} catch {") :]
    assert "exit 0" in tail or "exit0" in tail.replace(" ", "")


def test_statusline_prints_output_cost_and_top_fields():
    text = _script_text()
    assert " out ·" in text or '$line = "⏶' in text
    assert "top:" in text.lower()


def test_statusline_documents_cursor_live_not_latest_json():
    text = _script_text()
    lowered = text.lower()
    assert "cursor" in lowered
    assert "live" in lowered
    assert "latest.json" in text


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
def test_statusline_pwsh_smoke_with_real_latest_file(tmp_path):
    ledger_dir = tmp_path / "ledger"
    _write_latest_json(ledger_dir)
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(ledger_dir)}
    r = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(STATUSLINE)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0
    assert r.stderr.strip() == ""
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


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
def test_statusline_pwsh_malformed_latest_is_silent(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / "latest.json").write_text("{not-json", encoding="utf-8")
    env = {**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(ledger_dir)}
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
    assert "latest.json" in text
    assert DEFAULT_CACHE.parent.name in text  # token-usage dir name
