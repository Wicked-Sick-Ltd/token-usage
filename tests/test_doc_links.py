"""Local Markdown links and anchors in the current-behaviour docs must resolve.

Covers the README, the contributor/security/submission docs and docs/*.md. The dated
records under docs/superpowers/ are historical and are not checked.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md",
                                 "SUBMISSION.md", "AGENTS.md", "CHANGELOG.md")]
DOCS += sorted((ROOT / "docs").glob("*.md"))
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)


def _slug(heading):
    text = re.sub(r"[`*_]|<[^>]+>", "", heading.strip()).lower()
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(path):
    seen = {}
    found = set()
    body = FENCE.sub("", path.read_text(encoding="utf-8"))
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", body, re.MULTILINE):
        slug = _slug(match.group(1))
        count = seen.get(slug, 0)
        found.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return found


def local_links(path):
    body = FENCE.sub("", path.read_text(encoding="utf-8"))
    body = re.sub(r"`[^`\n]*`", "", body)
    for target in LINK.findall(body):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
            continue  # http:, https:, mailto: and friends
        yield target


def test_local_doc_links_resolve():
    broken = []
    for doc in DOCS:
        if not doc.is_file():
            continue
        for target in local_links(doc):
            file_part, _, anchor = target.partition("#")
            dest = (doc.parent / file_part).resolve() if file_part else doc
            if not dest.exists():
                broken.append(f"{doc.relative_to(ROOT)}: {target} (missing file)")
                continue
            if anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                broken.append(f"{doc.relative_to(ROOT)}: {target} (missing anchor)")
    assert not broken, "\n".join(broken)


def test_user_docs_cover_the_expected_sections():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## Quick start\n", "## What it does\n", "## Installation\n",
                    "### Platforms\n", "## Configuration\n",
                    "## Suggest a feature or report an issue\n"):
        assert heading in readme
    assert "docs/reference.md" in readme
    reference = (ROOT / "docs" / "reference.md").read_text(encoding="utf-8")
    for name in ("report", "json", "history", "insights", "top_consumers",
                 "dashboard", "live", "export", "session_cost", "diff"):
        assert name in reference
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "reference.md" in index


def test_readme_configuration_names_every_env_var_the_scripts_read():
    names = set()
    for path in (ROOT / "scripts").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r'os\.environ(?:\.get)?\(\s*["\']([A-Z0-9_]+)["\']', text))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("## Configuration\n")
    rest = readme[start:]
    end = rest.find("\n## ", len("## Configuration\n"))
    section = rest[:end]
    missing = sorted(name for name in names if name not in section)
    assert not missing, missing


def test_reference_mentions_every_cli_flag():
    """docs/reference.md stays aligned with argparse, including flags --help hides the meaning of."""
    reference = (ROOT / "docs" / "reference.md").read_text(encoding="utf-8")
    script = ROOT / "scripts" / "token_usage.py"
    top = subprocess.run([sys.executable, str(script), "--help"],
                         check=True, capture_output=True, text=True)
    commands = re.findall(r"\{([a-z0-9_,-]+)\}", top.stdout)
    assert commands, top.stdout
    missing = []
    for group in commands:
        for cmd in group.split(","):
            if cmd not in reference:
                missing.append(cmd)
            help_out = subprocess.run(
                [sys.executable, str(script), cmd, "--help"],
                check=True, capture_output=True, text=True).stdout
            for line in help_out.splitlines():
                stripped = line.strip()
                if not stripped.startswith("--"):
                    continue
                flag = stripped.split()[0].split("[")[0]
                if flag not in reference:
                    missing.append(f"{cmd} {flag}")
    assert not missing, missing


def test_slug_matches_github_style():
    assert _slug("Privacy and data handling") == "privacy-and-data-handling"
    assert _slug("`/token-usage:report`") == "token-usagereport"
    assert _slug("Dashboard (`dashboard`)") == "dashboard-dashboard"
    assert _slug("Cursor: what v1 can and cannot measure") == "cursor-what-v1-can-and-cannot-measure"
