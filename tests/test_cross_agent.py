"""Cross-agent packaging: one interpreter name, Grok Build compatibility, licence."""
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import SCRIPT

ROOT = Path(__file__).resolve().parent.parent


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _mcp_entries():
    """(host, server entry) for every manifest that launches the MCP server."""
    return [
        ("claude", load(".claude-plugin/plugin.json")["mcpServers"]["token-usage"]),
        ("cursor", load(".cursor-plugin/plugin.json")["mcpServers"]["token-usage"]),
        ("codex", load(".mcp-codex.json")["mcpServers"]["token-usage"]),
        ("gemini", load("gemini-extension.json")["mcpServers"]["token-usage"]),
        ("copilot", load(".plugin/plugin.json")["mcpServers"]["token-usage"]),
    ]


def test_every_host_launches_the_mcp_server_with_python3():
    # macOS and most Linux distributions ship no bare `python`, so a `python`
    # launcher installs cleanly and then fails the MCP handshake. Gemini CLI
    # 0.63.0 on Debian reported the server Disconnected for exactly that.
    for host, entry in _mcp_entries():
        assert entry["command"] == "python3", host


def test_every_host_pins_its_own_runtime():
    expected = {"claude": "claude", "cursor": "cursor", "codex": "codex",
                "gemini": "gemini", "copilot": "copilot"}
    for host, entry in _mcp_entries():
        assert entry["env"]["TOKEN_USAGE_RUNTIME"] == expected[host], host


def test_grok_shaped_stop_payload_is_a_silent_no_op(tmp_path):
    # Grok Build loads the Claude plugin unchanged and runs its Stop hook with
    # a camelCase envelope and no transcript_path. The hook must exit 0, print
    # nothing and write no ledger rather than guess at a transcript.
    payload = {"hookEventName": "stop", "hook_event_name": "Stop", "sessionId": "grok-1",
               "cwd": str(tmp_path), "stopHookActive": False, "lastAssistantMessage": "done"}
    ledger = tmp_path / "ledger"
    proc = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=json.dumps(payload),
                          capture_output=True, text=True, check=False,
                          env={**os.environ, "TOKEN_USAGE_LEDGER_DIR": str(ledger)})
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not ledger.exists() or not any(ledger.iterdir())


def test_licence_is_standard_mit_with_wicked_sick_copyright_and_credit():
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert "Copyright (c) 2026 Wicked Sick Limited" in licence
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Wicked Sick Limited" in notice and "MIT" in notice
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "credit Wicked Sick Limited" in readme
