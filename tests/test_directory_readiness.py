"""Anthropic plugin directory readiness: the automated checks we can run offline.

Mirrors the Blocks / Held-for-a-reviewer rows of the directory's pre-submission
checklist (claude.com/docs/plugins/pre-submission-checklist, read 3 Oct 2026) that
can be decided from the repository alone, plus a version-agreement check so a
release can never ship mismatched manifests. The portal's own Validate remains the
authority; this only stops a regression from reaching it.
"""
import json
import re
import subprocess
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MANIFESTS = {
    "claude": CLAUDE_MANIFEST,
    "codex": PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
    "cursor": PLUGIN_ROOT / ".cursor-plugin" / "plugin.json",
}

MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 512
IMAGE_OR_FONT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                 ".woff", ".woff2", ".ttf", ".otf"}
SYSTEM_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
WINDOWS_DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
LAUNCHERS = ("npx", "bunx", "uvx", "pnpm dlx", "yarn dlx", "pipx run", "uv run")
VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def shipped_files():
    """Files a directory install would ship: git's tracked set when available,
    otherwise a walk that skips VCS and local tool caches."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=PLUGIN_ROOT,
                             capture_output=True, check=True).stdout
        names = [n for n in out.decode("utf-8").split("\0") if n]
        if names:
            return [PLUGIN_ROOT / n for n in names]
    except (OSError, subprocess.CalledProcessError):
        pass
    skip = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}
    return [p for p in PLUGIN_ROOT.rglob("*")
            if (p.is_file() or p.is_symlink())
            and not skip.intersection(p.relative_to(PLUGIN_ROOT).parts)]


def rel(path):
    return path.relative_to(PLUGIN_ROOT).as_posix()


def test_file_count_within_directory_limit():
    assert len(shipped_files()) <= MAX_FILES


def test_no_large_non_image_files():
    big = [f"{rel(p)} ({p.stat().st_size} bytes)" for p in shipped_files()
           if p.is_file() and p.suffix.lower() not in IMAGE_OR_FONT
           and p.stat().st_size >= MAX_FILE_BYTES]
    assert not big, f"non-image files at or over 256 KiB are held for review: {big}"


def test_only_text_images_and_fonts():
    binary = []
    for p in shipped_files():
        if not p.is_file() or p.suffix.lower() in IMAGE_OR_FONT:
            continue
        data = p.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            binary.append(rel(p))
            continue
        if b"\0" in data:
            binary.append(rel(p))
    assert not binary, f"binary files are held for review: {binary}"


def test_no_symlinks_submodules_or_system_files():
    bad = []
    for p in shipped_files():
        parts = [part.lower() for part in p.relative_to(PLUGIN_ROOT).parts]
        if p.is_symlink():
            bad.append(f"symlink {rel(p)}")
        if p.name.lower() in SYSTEM_FILES or "__macosx" in parts:
            bad.append(f"system file {rel(p)}")
        if p.name == ".gitmodules":
            bad.append("submodules (.gitmodules)")
    assert not bad, bad


def test_file_names_portable_to_windows_and_macos():
    seen = {}
    bad = []
    for p in shipped_files():
        r = rel(p)
        for part in r.split("/"):
            if ":" in part or part.endswith((".", " ")) or WINDOWS_DEVICE.match(part):
                bad.append(r)
        key = r.lower()
        if key in seen and seen[key] != r:
            bad.append(f"{r} differs only by case from {seen[key]}")
        seen.setdefault(key, r)
    assert not bad, bad


def test_gitattributes_do_not_rewrite_archives():
    for p in shipped_files():
        if p.name != ".gitattributes":
            continue
        text = p.read_text(encoding="utf-8")
        for word in ("export-ignore", "export-subst", "filter="):
            assert word not in text, f"{rel(p)} uses {word}, which stops directory validation"


def _prose_words(markdown):
    prose = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    return re.findall(r"[A-Za-z0-9][\w'’-]*", prose)


def test_readme_and_license_present():
    readme = PLUGIN_ROOT / "README.md"
    assert readme.is_file()
    assert len(_prose_words(readme.read_text(encoding="utf-8"))) >= 40
    assert (PLUGIN_ROOT / "LICENSE").is_file()
    assert json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8")).get("license") == "MIT"


def test_claude_manifest_listing_fields():
    cfg = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    assert NAME_RE.match(cfg["name"]), cfg["name"]
    for key in ("description", "version", "author", "displayName", "homepage"):
        assert cfg.get(key), f"plugin.json is missing {key}"
    assert cfg["author"].get("name")
    for key in ("homepage", "documentationUrl", "supportUrl", "privacyPolicyUrl"):
        if key in cfg:
            assert cfg[key].startswith("https://"), key
    # The privacy link points at a README anchor; keep that heading in place.
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    if "#privacy-and-data-handling" in cfg.get("privacyPolicyUrl", ""):
        assert "\n## Privacy and data handling\n" in readme


def _claude_commands():
    """(where, command string, env) for every Claude hook and MCP command."""
    cfg = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    found = []
    for name, srv in cfg.get("mcpServers", {}).items():
        line = " ".join([srv["command"], *srv.get("args", [])])
        found.append((f"mcpServers.{name}", line))
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for event, groups in hooks["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                found.append((f"hooks.{event}", hook["command"]))
    return found


def test_claude_commands_use_only_plugin_root_and_no_inline_programs():
    commands = _claude_commands()
    assert commands
    for where, line in commands:
        variables = set(VAR_RE.findall(line))
        assert variables <= {"CLAUDE_PLUGIN_ROOT"}, f"{where}: {line}"
        assert "$(" not in line and "`" not in line, f"{where}: command substitution"
        assert not re.search(r"(^|\s)-c(\s|$)", line), f"{where}: inline program"
        assert "${CLAUDE_PLUGIN_ROOT}/scripts/" in line, f"{where}: {line}"


def test_no_unpinned_package_launchers():
    for where, line in _claude_commands():
        for launcher in LAUNCHERS:
            if re.search(rf"(^|\s){re.escape(launcher)}(\s|$)", line):
                assert re.search(r"@\d+\.\d+\.\d+|==\d+\.\d+\.\d+|--locked|--frozen", line), \
                    f"{where}: unpinned {launcher}"


def test_hooks_json_uses_known_claude_events():
    known = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
             "Notification", "Stop", "SubagentStop", "SubagentStart",
             "PreCompact", "SessionEnd"}
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert isinstance(hooks.get("hooks"), dict)
    assert set(hooks["hooks"]) <= known
    cfg = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    # hooks/hooks.json loads automatically; naming it again is a warning.
    assert "hooks" not in cfg


def test_skill_frontmatter_is_standard():
    for skill in sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        assert match, f"{rel(skill)} has no front matter"
        keys = re.findall(r"^([A-Za-z][\w-]*):", match.group(1), re.MULTILINE)
        assert "name" in keys and "description" in keys
        assert re.search(rf"^name: {re.escape(skill.parent.name)}$", match.group(1), re.MULTILINE)
        assert "version" not in keys, f"{rel(skill)}: version belongs in plugin.json"


def test_all_manifest_versions_agree_with_changelog():
    versions = {host: json.loads(path.read_text(encoding="utf-8"))["version"]
                for host, path in MANIFESTS.items()}
    assert len(set(versions.values())) == 1, versions
    version = versions["claude"]
    changelog = (PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    assert released and released[0] == version, (released[:1], version)
