"""Runtime adapter registry and Claude compatibility shims."""
import pytest
from conftest import assistant, usage, user, write_jsonl


def seed(tmp_path, monkeypatch):
    proj = tmp_path / "projects"
    transcript = write_jsonl(proj / "-Users-x-alpha" / "aaa-111.jsonl", [
        user("2026-06-10T10:00:00Z", command="/commit"),
        assistant("2026-06-10T10:00:01Z", usage(inp=100, out=10), request_id="r1"),
    ])
    monkeypatch.setenv("TOKEN_USAGE_PROJECTS_DIR", str(proj))
    monkeypatch.delenv("TOKEN_USAGE_TRANSCRIPT", raising=False)
    return transcript


def test_claude_adapter_locates_explicit_path(tu, tmp_path, monkeypatch):
    t = seed(tmp_path, monkeypatch)
    adapter = tu.get_runtime_adapter("claude")
    assert adapter.locate(str(t)) == t


def test_claude_adapter_parse_matches_parse_session_aggregate(tu, tmp_path, monkeypatch):
    t = seed(tmp_path, monkeypatch)
    pricing = tu.load_pricing()
    expected = tu.aggregate(tu.parse_session(t), pricing)
    adapter = tu.get_runtime_adapter("claude")
    assert tu.aggregate(adapter.parse(t), pricing) == expected


def test_unknown_runtime_is_rejected(tu):
    with pytest.raises(ValueError, match="unknown runtime"):
        tu.get_runtime_adapter("not-a-runtime")
