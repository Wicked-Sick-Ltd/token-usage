"""Cursor Plugin manifest: public bundle paths and no root mcp.json."""
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CURSOR_MANIFEST = PLUGIN_ROOT / ".cursor-plugin" / "plugin.json"


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


def test_no_root_mcp_json_avoids_claude_code_project_scope_collision():
    # Same rationale as .claude-plugin inline mcpServers (see CHANGELOG 0.6.1):
    # a repo-root mcp.json is also read as project-scope MCP inside a checkout.
    assert not (PLUGIN_ROOT / "mcp.json").exists()
