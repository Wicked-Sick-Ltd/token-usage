"""Cursor Plugin manifest: public bundle paths and no root mcp.json."""
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CURSOR_MANIFEST = PLUGIN_ROOT / ".cursor-plugin" / "plugin.json"
HOOKS_CURSOR = PLUGIN_ROOT / "hooks" / "hooks-cursor.json"

HOOK_EVENTS = (
    "beforeSubmitPrompt",
    "stop",
    "subagentStart",
    "subagentStop",
)


def test_cursor_plugin_manifest_registers_mcp_skills_and_hooks():
    assert CURSOR_MANIFEST.is_file(), "missing .cursor-plugin/plugin.json"
    cfg = json.loads(CURSOR_MANIFEST.read_text(encoding="utf-8"))

    assert cfg.get("license") == "MIT"
    assert cfg.get("repository") == "https://github.com/Wicked-Sick-Ltd/token-usage"
    assert cfg.get("name") == "token-usage"

    srv = cfg["mcpServers"]["token-usage"]
    assert srv["command"] == "python3"
    assert srv["args"] == ["${CURSOR_PLUGIN_ROOT}/scripts/mcp_server.py"]
    assert (PLUGIN_ROOT / "scripts" / "mcp_server.py").is_file()

    hooks_path = cfg["hooks"]
    assert hooks_path == "hooks/hooks-cursor.json"
    assert (PLUGIN_ROOT / hooks_path).is_file()

    skills = cfg["skills"]
    assert skills == "skills/report" or skills == ["skills/report"]
    skill_dir = PLUGIN_ROOT / "skills" / "report"
    assert (skill_dir / "SKILL.md").is_file()


def test_hooks_cursor_json_matches_cursor_documented_flat_schema():
    """Cursor hooks.json: version 1, command/timeout per entry (no nested hooks)."""
    cfg = json.loads(HOOKS_CURSOR.read_text(encoding="utf-8"))
    assert cfg.get("version") == 1
    assert "description" not in cfg
    hooks = cfg["hooks"]
    for event in HOOK_EVENTS:
        entries = hooks[event]
        assert isinstance(entries, list) and entries
        for entry in entries:
            assert "hooks" not in entry, f"{event} must not nest a hooks array"
            assert entry.get("type", "command") == "command"
            cmd = entry["command"]
            assert "${CURSOR_PLUGIN_ROOT}" in cmd
            assert "cursor-hook" in cmd
            assert entry.get("timeout") == 15


def test_changelog_does_not_claim_afterAgentResponse_is_registered():
    # The parser recognises afterAgentResponse as a completion event, but the
    # manifest never subscribes to it. The CHANGELOG listed it alongside the
    # events that ARE registered, which reads as "this hook fires".
    hooks = json.loads(HOOKS_CURSOR.read_text(encoding="utf-8"))["hooks"]
    assert "afterAgentResponse" not in hooks
    assert set(hooks) == set(HOOK_EVENTS)
    changelog = (PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    claim = changelog.split("### Changed", 1)[0]
    assert "afterAgentResponse" in claim
    assert "not registered" in claim


def test_changelog_describes_latest_json_as_a_pointer_not_an_aggregate():
    # latest.json is a symlink to the newest per-session ledger, not a
    # separate corpus-wide aggregate file.
    changelog = (PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(unreleased.split())
    assert "aggregate `TOKEN_USAGE_LEDGER_DIR/latest.json`" not in flat
    assert "aggregate `latest.json`" not in flat
    assert "latest session aggregate" in flat


def test_no_root_mcp_json_avoids_claude_code_project_scope_collision():
    # Same rationale as .claude-plugin inline mcpServers (see CHANGELOG 0.6.1):
    # a repo-root mcp.json is also read as project-scope MCP inside a checkout.
    assert not (PLUGIN_ROOT / "mcp.json").exists()
