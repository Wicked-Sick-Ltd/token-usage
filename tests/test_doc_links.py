"""Local Markdown links and anchors in the current-behaviour docs must resolve.

Covers the README, the contributor/security/submission docs and docs/*.md. The dated
records under docs/superpowers/ are historical and are not checked.
"""
import re
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


def test_slug_matches_github_style():
    assert _slug("Privacy and data handling") == "privacy-and-data-handling"
    assert _slug("`/token-usage:report`") == "token-usagereport"
    assert _slug("Dashboard (`dashboard`)") == "dashboard-dashboard"
    assert _slug("Cursor: what v1 can and cannot measure") == "cursor-what-v1-can-and-cannot-measure"
