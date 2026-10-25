#!/usr/bin/env python3
"""Opt-in activity stream from Cursor, Claude Code, Codex and Grok Bot to llm1.

The bridge is inert until WIZZO_COORD_URL, WIZZO_TWIN_API_URL, or
~/.config/wizzo/activity-bridge.env (honouring XDG_CONFIG_HOME) sets a
destination. Hook entry points always exit 0. A short HTTP timeout and a
local SQLite spool keep a turn when mcp-coordination (:8140) or twin-api
(:8100) is unreachable. `sync` forwards new JSONL turns idempotently.

Stdlib only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CURSOR_EVENTS = {
    "beforeSubmitPrompt",
    "stop",
    "subagentStart",
    "subagentStop",
    "afterAgentResponse",
    "sessionEnd",
}
COMPLETION_EVENTS = {"stop", "subagentStop", "Stop", "SubagentStop", "afterAgentResponse"}
PROMPT_EVENTS = {"beforeSubmitPrompt", "subagentStart", "SubagentStart"}
END_EVENTS = {"sessionEnd", "SessionEnd"}
# Live snapshots. A newer delivered turn must win over an older spooled one.
MUTABLE_ENDPOINTS = {"heartbeat", "turn", "ai_sessions"}
_CODEX_PAYLOAD_TYPES = {"response_item", "session_meta", "turn_context", "token_usage_record"}
ENDPOINTS = {
    "register": ("coord", "/session/register"),
    "heartbeat": ("coord", "/session/heartbeat"),
    "turn": ("coord", "/session/turn"),
    "ai_sessions": ("coord", "/api/ai-sessions"),
    "end": ("coord", "/session/end"),
    "ingest": ("twin", "/v1/ingest"),
}
ENV_NAMES = (
    "WIZZO_COORD_URL",
    "WIZZO_COORD_TOKEN",
    "WIZZO_TWIN_API_URL",
    "WIZZO_TWIN_TOKEN",
    "WIZZO_ACTIVITY_BRIDGE",
    "WIZZO_ACTIVITY_STATE_DIR",
    "WIZZO_ACTIVITY_HTTP_TIMEOUT",
    "WIZZO_ACTIVITY_SYNC_DIRS",
    "WIZZO_MACHINE",
    "WIZZO_REPO",
    "WIZZO_BRANCH",
    "WIZZO_SURFACE",
    "WIZZO_VENDOR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "TOKEN_USAGE_LEDGER_DIR",
)
FIELD_LIMIT = 20000
_GIT_CACHE = {}
_DISABLED = {"0", "off", "false", "no"}
_TRANSCRIPT_GENERATION = "__transcript__"
_SKIP_TYPES = {
    "session_meta",
    "progress",
    "event_msg",
    "token_count",
    "turn_context",
    "rate_limit",
    "rate_limit_event",
    "system",
}


class DeliveryError(Exception):
    """A POST did not land. The body stays in the local spool."""


class Config:
    """Resolved bridge settings. Empty URLs leave that target unused."""

    def __init__(self, coord_url, coord_token, twin_url, twin_token, enabled,
                 state_dir, timeout, machine, repo, branch, surface, vendor,
                 sync_dirs):
        self.coord_url = coord_url
        self.coord_token = coord_token
        self.twin_url = twin_url
        self.twin_token = twin_token
        self.enabled = enabled
        self.state_dir = state_dir
        self.timeout = timeout
        self.machine = machine
        self.repo = repo
        self.branch = branch
        self.surface = surface
        self.vendor = vendor
        self.sync_dirs = sync_dirs


class Turn:
    """One user prompt plus the tool calls and assistant text that followed."""

    def __init__(self):
        self.prompt = ""
        self.assistant_parts = []
        self.tools = []
        self.model = ""
        self.timestamp = ""
        self.usage = _empty_usage()
        self.turn_id = ""
        # requestId -> per-field maxima, folded into usage when the turn closes.
        self.request_usage = {}

    @property
    def assistant(self):
        return "\n\n".join(part for part in self.assistant_parts if part)

    @property
    def has_assistant(self):
        return bool(self.assistant or self.tools)

    def add_usage(self, usage):
        if not usage:
            return
        for key in self.usage:
            self.usage[key] += int(usage.get(key) or 0)


def _warn(message):
    try:
        sys.stderr.write(str(message) + "\n")
        sys.stderr.flush()
    except OSError:
        # A closed or broken stderr must not crash the hook.
        pass


def _flush_stdout():
    try:
        sys.stdout.flush()
    except OSError:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, sys.stdout.fileno())
            os.close(fd)
            sys.stdout.flush()
        except (OSError, ValueError, AttributeError):
            # stdout is already unusable; the hook still has to exit 0.
            pass


def _process_env():
    """Literals so the README configuration check sees every variable."""
    return {
        "WIZZO_COORD_URL": os.environ.get("WIZZO_COORD_URL"),
        "WIZZO_COORD_TOKEN": os.environ.get("WIZZO_COORD_TOKEN"),
        "WIZZO_TWIN_API_URL": os.environ.get("WIZZO_TWIN_API_URL"),
        "WIZZO_TWIN_TOKEN": os.environ.get("WIZZO_TWIN_TOKEN"),
        "WIZZO_ACTIVITY_BRIDGE": os.environ.get("WIZZO_ACTIVITY_BRIDGE"),
        "WIZZO_ACTIVITY_STATE_DIR": os.environ.get("WIZZO_ACTIVITY_STATE_DIR"),
        "WIZZO_ACTIVITY_HTTP_TIMEOUT": os.environ.get("WIZZO_ACTIVITY_HTTP_TIMEOUT"),
        "WIZZO_ACTIVITY_SYNC_DIRS": os.environ.get("WIZZO_ACTIVITY_SYNC_DIRS"),
        "WIZZO_MACHINE": os.environ.get("WIZZO_MACHINE"),
        "WIZZO_REPO": os.environ.get("WIZZO_REPO"),
        "WIZZO_BRANCH": os.environ.get("WIZZO_BRANCH"),
        "WIZZO_SURFACE": os.environ.get("WIZZO_SURFACE"),
        "WIZZO_VENDOR": os.environ.get("WIZZO_VENDOR"),
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME"),
        "XDG_CACHE_HOME": os.environ.get("XDG_CACHE_HOME"),
        "TOKEN_USAGE_LEDGER_DIR": os.environ.get("TOKEN_USAGE_LEDGER_DIR"),
    }


def config_file(environ=None):
    source = _process_env() if environ is None else environ
    xdg = _text(source.get("XDG_CONFIG_HOME"))
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "wizzo" / "activity-bridge.env"


def parse_env_file(path):
    """KEY=VALUE lines. Does not expand shells or override existing keys."""
    vals = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return vals
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            vals[key] = val
    return vals


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _timeout(raw):
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.8
    if val <= 0:
        return 0.8
    return val


def _state_dir(vals):
    explicit = _text(vals.get("WIZZO_ACTIVITY_STATE_DIR"))
    if explicit:
        return Path(explicit)
    ledger = _text(vals.get("TOKEN_USAGE_LEDGER_DIR"))
    if ledger:
        return Path(ledger) / "activity-bridge"
    xdg = _text(vals.get("XDG_CACHE_HOME"))
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "token-usage" / "activity-bridge"


def _split_dirs(raw):
    text = _text(raw)
    if not text:
        return []
    return [part for part in text.split(os.pathsep) if part]


def load_config(environ=None):
    """File values first, then non-empty process environment.

    Pass `environ` from tests so a developer machine with the bridge
    configured cannot leak into the suite.
    """
    file_vals = parse_env_file(config_file(environ))
    process = _process_env() if environ is None else {
        name: environ.get(name) for name in ENV_NAMES
    }
    merged = dict(file_vals)
    for key, val in process.items():
        if _text(val):
            merged[key] = _text(val)
    flag = _text(merged.get("WIZZO_ACTIVITY_BRIDGE")).lower()
    coord = _text(merged.get("WIZZO_COORD_URL")).rstrip("/")
    twin = _text(merged.get("WIZZO_TWIN_API_URL")).rstrip("/")
    enabled = flag not in _DISABLED and bool(coord or twin)
    machine = _text(merged.get("WIZZO_MACHINE")) or socket.gethostname() or "unknown"
    return Config(
        coord_url=coord,
        coord_token=_text(merged.get("WIZZO_COORD_TOKEN")),
        twin_url=twin,
        twin_token=_text(merged.get("WIZZO_TWIN_TOKEN")),
        enabled=enabled,
        state_dir=_state_dir(merged),
        timeout=_timeout(merged.get("WIZZO_ACTIVITY_HTTP_TIMEOUT")),
        machine=machine,
        repo=_text(merged.get("WIZZO_REPO")),
        branch=_text(merged.get("WIZZO_BRANCH")),
        surface=_text(merged.get("WIZZO_SURFACE")),
        vendor=_text(merged.get("WIZZO_VENDOR")),
        sync_dirs=_split_dirs(merged.get("WIZZO_ACTIVITY_SYNC_DIRS")),
    )


def clip(text, limit=FIELD_LIMIT):
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def clean_id(value, fallback="unknown"):
    text = _text(value)
    text = re.sub(r"[\r\n\x00]", "", text)
    return text or fallback


def vendor_name(runtime):
    runtime = _text(runtime).lower().replace("-", "_")
    if runtime in {"grok", "grok_bot"}:
        return "grok"
    if runtime in {"cursor", "cloud_agent"}:
        return "cursor"
    if runtime in {"claude", "claude_code"}:
        return "claude"
    if runtime == "codex":
        return "codex"
    if runtime in {"gemini", "antigravity"}:
        return "gemini"
    return runtime or "cursor"


def source_type_name(runtime):
    runtime = _text(runtime).lower().replace("-", "_")
    if runtime in {"grok", "grok_bot"}:
        return "grok_bot"
    if runtime in {"cursor", "cloud_agent"}:
        return "cursor"
    if runtime in {"claude", "claude_code"}:
        return "claude"
    if runtime == "codex":
        return "codex"
    if runtime in {"gemini", "antigravity"}:
        return "gemini"
    return runtime or "cursor"


def default_surface(runtime, path=""):
    path_l = str(path or "").replace("\\", "/").lower()
    if "cloud-agent" in path_l or "cloud_agent" in path_l:
        return "cloud_agent"
    runtime = _text(runtime).lower().replace("-", "_")
    if runtime in {"grok", "grok_bot"}:
        return "grok_bot"
    if runtime == "codex":
        return "codex"
    if runtime in {"claude", "claude_code"}:
        return "claude_code"
    if runtime in {"gemini", "antigravity"}:
        return "antigravity"
    return "ide"


def infer_runtime(path, vendor=""):
    if _text(vendor):
        return vendor_name(vendor)
    path_l = str(path).replace("\\", "/").lower()
    if "grok" in path_l:
        return "grok"
    if "codex" in path_l:
        return "codex"
    if ".claude" in path_l or "/claude/" in path_l:
        return "claude"
    if "gemini" in path_l or "antigravity" in path_l:
        return "gemini"
    return "cursor"


def repo_slug(remote, toplevel):
    """org/repo from an origin URL, else the checkout directory name."""
    text = _text(remote)
    text = text.removesuffix(".git")
    if text:
        if "://" in text:
            text = text.split("://", 1)[1]
            text = text.split("/", 1)[1] if "/" in text else ""
        elif ":" in text:
            text = text.split(":", 1)[1]
        text = text.strip("/")
        if text:
            return text
    if toplevel:
        return Path(toplevel).name
    return ""


def lookup_git(cwd):
    """(repo slug, branch) for cwd. Cached. Empty strings when git is absent."""
    key = str(cwd or "")
    if key in _GIT_CACHE:
        return _GIT_CACHE[key]
    repo, branch = "", ""
    if key and Path(key).exists():
        try:
            top = subprocess.run(
                ["git", "-C", key, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=0.4, check=False,
            )
            if top.returncode == 0:
                toplevel = _text(top.stdout)
                remote = subprocess.run(
                    ["git", "-C", key, "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=0.4, check=False,
                )
                repo = repo_slug(remote.stdout if remote.returncode == 0 else "", toplevel)
                branch_run = subprocess.run(
                    ["git", "-C", key, "symbolic-ref", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=0.4, check=False,
                )
                if branch_run.returncode != 0:
                    branch_run = subprocess.run(
                        ["git", "-C", key, "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True, text=True, timeout=0.4, check=False,
                    )
                if branch_run.returncode == 0:
                    branch = _text(branch_run.stdout)
                    if branch == "HEAD":
                        branch = ""
        except (OSError, subprocess.TimeoutExpired):
            repo, branch = "", ""
    _GIT_CACHE[key] = (repo, branch)
    return repo, branch


def resolve_place(cwd, config):
    repo, branch = config.repo, config.branch
    if repo and branch:
        return repo, branch
    got_repo, got_branch = lookup_git(cwd)
    return repo or got_repo, branch or got_branch


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _earliest_timestamp(left, right):
    left = left or ""
    right = right or ""
    if left and right:
        return min(left, right)
    return left or right


def _normalize_cursor_usage(usage):
    """Cursor payload input includes cache reads and writes.

    Matches `_cursor_usage_from_tokens`: subtract cache only when it fits.
    A cache total above input keeps the reported input. Claude's input is
    already exclusive of cache and must not go through this function.
    """
    def count(key):
        value = int(usage.get(key) or 0)
        return max(0, value)

    incoming = count("input_tokens")
    read = count("cache_read_tokens")
    written = count("cache_write_tokens")
    cached = read + written
    if incoming and cached and incoming >= cached:
        incoming -= cached
    return {
        "input_tokens": incoming,
        "output_tokens": count("output_tokens"),
        "cache_read_tokens": read,
        "cache_write_tokens": written,
    }


def _hash(body):
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Store:
    """SQLite watermark and spool. The directory is owner-only when it can be."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            # Restrictive filesystems can reject chmod; the spool still works.
            pass
        self.path = self.directory / "bridge.sqlite"
        self.db = sqlite3.connect(str(self.path), timeout=1.0)
        self.db.execute("PRAGMA busy_timeout=1000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS delivered (
                source_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                session_id TEXT DEFAULT '',
                turn_index INTEGER DEFAULT 0,
                mutable INTEGER DEFAULT 0,
                stage INTEGER NOT NULL DEFAULT 0,
                usage_revision INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_id, endpoint, content_hash)
            );
            CREATE TABLE IF NOT EXISTS spool (
                source_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                target TEXT NOT NULL,
                path TEXT NOT NULL,
                body TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1,
                last_error TEXT,
                created_at TEXT NOT NULL,
                session_id TEXT DEFAULT '',
                turn_index INTEGER DEFAULT 0,
                mutable INTEGER DEFAULT 0,
                stage INTEGER NOT NULL DEFAULT 0,
                usage_revision INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_id, endpoint, content_hash)
            );
            CREATE TABLE IF NOT EXISTS prompts (
                session_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                agent_key TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL,
                model TEXT,
                cwd TEXT,
                turn_index INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, generation_id, agent_key)
            );
            CREATE TABLE IF NOT EXISTS generation_usage (
                session_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                agent_key TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                in_transcript INTEGER NOT NULL DEFAULT 0,
                covered_turns INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, generation_id, agent_key)
            );
            CREATE TABLE IF NOT EXISTS turn_map (
                session_id TEXT NOT NULL,
                agent_key TEXT NOT NULL DEFAULT '',
                generation_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                seen_at TEXT NOT NULL DEFAULT '',
                transcript_turn INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, agent_key, generation_id)
            );
            CREATE TABLE IF NOT EXISTS turn_high_water (
                session_id TEXT NOT NULL,
                agent_key TEXT NOT NULL DEFAULT '',
                high_water INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                open_prompt INTEGER NOT NULL DEFAULT 0,
                usage_revision INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, agent_key)
            );
            """
        )
        self._migrate()
        self.db.commit()

    def _columns(self, table):
        return [row[1] for row in self.db.execute(f"PRAGMA table_info({table})")]

    def _migrate(self):
        """Upgrade spools created before agent keys and snapshot versions existed."""
        prompt_cols = self._columns("prompts")
        if prompt_cols and "agent_key" not in prompt_cols:
            self.db.executescript(
                """
                ALTER TABLE prompts RENAME TO prompts_v1;
                CREATE TABLE prompts (
                    session_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    agent_key TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL,
                    model TEXT,
                    cwd TEXT,
                    turn_index INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, generation_id, agent_key)
                );
                INSERT INTO prompts (
                    session_id, generation_id, agent_key, prompt, model, cwd, turn_index, updated_at
                )
                SELECT session_id, generation_id, '', prompt, model, cwd, turn_index, updated_at
                FROM prompts_v1;
                DROP TABLE prompts_v1;
                """
            )
        for table in ("spool", "delivered"):
            cols = self._columns(table)
            if not cols:
                continue
            if "session_id" not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN session_id TEXT DEFAULT ''")
            if "turn_index" not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN turn_index INTEGER DEFAULT 0")
            if "mutable" not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN mutable INTEGER DEFAULT 0")
            if "stage" not in cols:
                self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN stage INTEGER NOT NULL DEFAULT 0"
                )
            if "usage_revision" not in cols:
                self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN usage_revision INTEGER NOT NULL DEFAULT 0"
                )
        usage_cols = self._columns("generation_usage")
        if usage_cols and "covered_turns" not in usage_cols:
            self.db.execute(
                "ALTER TABLE generation_usage ADD COLUMN covered_turns INTEGER NOT NULL DEFAULT 0"
            )
        map_cols = self._columns("turn_map")
        if map_cols and "transcript_turn" not in map_cols:
            self.db.execute(
                "ALTER TABLE turn_map ADD COLUMN transcript_turn INTEGER NOT NULL DEFAULT 0"
            )
        water_cols = self._columns("turn_high_water")
        if water_cols and "open_prompt" not in water_cols:
            self.db.execute(
                "ALTER TABLE turn_high_water ADD COLUMN open_prompt INTEGER NOT NULL DEFAULT 0"
            )
        if water_cols and "usage_revision" not in water_cols:
            self.db.execute(
                "ALTER TABLE turn_high_water ADD COLUMN usage_revision INTEGER NOT NULL DEFAULT 0"
            )

    def close(self):
        self.db.close()

    def is_delivered(self, source_id, endpoint, content_hash):
        row = self.db.execute(
            "SELECT 1 FROM delivered WHERE source_id=? AND endpoint=? AND content_hash=?",
            (source_id, endpoint, content_hash),
        ).fetchone()
        return row is not None

    def acknowledge_delivery(self, source_id, endpoint, content_hash, session_id="",
                             turn_index=0, mutable=0, stage=0, usage_revision=0):
        """Record a successful post without lowering a version won during the send.

        A rescan can raise the queued revision for this same body while the POST
        is in flight. The delivered row keeps the later turn, stage, and
        revision among the posted values, the queued row, and any row already
        delivered. The spool row is removed in that same transaction.
        """
        posted = (
            int(turn_index or 0), int(stage or 0), int(usage_revision or 0),
            session_id or "", 1 if mutable else 0,
        )
        if self.db.in_transaction:
            self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        best = posted
        queued = self.db.execute(
            "SELECT turn_index, stage, usage_revision, session_id, mutable FROM spool "
            "WHERE source_id=? AND endpoint=? AND content_hash=?",
            (source_id, endpoint, content_hash),
        ).fetchone()
        stored = self.db.execute(
            "SELECT turn_index, stage, usage_revision, session_id, mutable FROM delivered "
            "WHERE source_id=? AND endpoint=? AND content_hash=?",
            (source_id, endpoint, content_hash),
        ).fetchone()
        for row in (queued, stored):
            if row is None:
                continue
            candidate = (
                int(row[0] or 0), int(row[1] or 0), int(row[2] or 0),
                row[3] or "", int(row[4] or 0),
            )
            if candidate[:3] > best[:3]:
                best = candidate
        kept_turn, kept_stage, kept_revision, kept_session, kept_mutable = best
        if stored is None:
            self.db.execute(
                "INSERT INTO delivered (source_id, endpoint, content_hash, delivered_at, "
                "session_id, turn_index, mutable, stage, usage_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source_id, endpoint, content_hash, utc_now(), kept_session,
                 kept_turn, kept_mutable, kept_stage, kept_revision),
            )
        else:
            self.db.execute(
                "UPDATE delivered SET session_id=?, turn_index=?, mutable=?, stage=?, "
                "usage_revision=?, delivered_at=? "
                "WHERE source_id=? AND endpoint=? AND content_hash=? "
                "AND (turn_index<? OR (turn_index=? AND stage<?) "
                "OR (turn_index=? AND stage=? AND usage_revision<?))",
                (kept_session, kept_turn, kept_mutable, kept_stage, kept_revision, utc_now(),
                 source_id, endpoint, content_hash,
                 kept_turn, kept_turn, kept_stage, kept_turn, kept_stage, kept_revision),
            )
        self.db.execute(
            "DELETE FROM spool WHERE source_id=? AND endpoint=? AND content_hash=?",
            (source_id, endpoint, content_hash),
        )
        if kept_mutable and kept_session:
            self.db.execute(
                "DELETE FROM spool WHERE session_id=? AND endpoint=? AND mutable=1 "
                "AND (turn_index<? OR (turn_index=? AND stage<?) "
                "OR (turn_index=? AND stage=? AND usage_revision<?))",
                (kept_session, endpoint, kept_turn, kept_turn, kept_stage,
                 kept_turn, kept_stage, kept_revision),
            )
        self.db.commit()

    def mark_delivered(self, source_id, endpoint, content_hash, session_id="", turn_index=0,
                       mutable=0, stage=0, usage_revision=0):
        self.db.execute(
            "INSERT OR REPLACE INTO delivered (source_id, endpoint, content_hash, delivered_at, "
            "session_id, turn_index, mutable, stage, usage_revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, endpoint, content_hash, utc_now(), session_id or "", int(turn_index),
             int(mutable), int(stage), int(usage_revision or 0)),
        )
        self.db.commit()

    def newer_delivered(self, session_id, endpoint, turn_index, stage=0, usage_revision=0):
        """True when a later mutable snapshot for this session already landed.

        Stage separates a prompt-only snapshot (0) from the completion (1) of
        the same turn. An equal stage is not newer, so one completion does not
        suppress another. A higher usage revision at that same turn and stage
        is newer: it was committed with the counters the earlier total lacked.
        Immutable posts are not compared.
        """
        if not session_id:
            return False
        turn_index = int(turn_index)
        stage = int(stage)
        usage_revision = int(usage_revision or 0)
        row = self.db.execute(
            "SELECT 1 FROM delivered WHERE session_id=? AND endpoint=? AND mutable=1 "
            "AND (turn_index>? OR (turn_index=? AND stage>?) "
            "OR (turn_index=? AND stage=? AND usage_revision>?)) LIMIT 1",
            (session_id, endpoint, turn_index, turn_index, stage,
             turn_index, stage, usage_revision),
        ).fetchone()
        return row is not None

    def enqueue(self, source_id, endpoint, content_hash, target, path, body, error,
                session_id="", turn_index=0, mutable=0, stage=0, usage_revision=0):
        mutable = 1 if mutable else 0
        turn_index = int(turn_index)
        stage = int(stage)
        usage_revision = int(usage_revision or 0)
        if mutable and self.newer_delivered(
            session_id, endpoint, turn_index, stage, usage_revision,
        ):
            return False
        # Keep the later publication, ordered the same way delivery is:
        # turn index, then stage, then usage revision. A higher revision at a
        # lower index must not demote the queued row.
        newer = (
            "excluded.turn_index > spool.turn_index "
            "OR (excluded.turn_index = spool.turn_index AND excluded.stage > spool.stage) "
            "OR (excluded.turn_index = spool.turn_index AND excluded.stage = spool.stage "
            "AND excluded.usage_revision > spool.usage_revision)"
        )
        self.db.execute(
            "INSERT INTO spool (source_id, endpoint, content_hash, target, path, "
            "body, attempts, last_error, created_at, session_id, turn_index, mutable, stage, "
            "usage_revision) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_id, endpoint, content_hash) DO UPDATE SET "
            f"turn_index = CASE WHEN {newer} THEN excluded.turn_index ELSE spool.turn_index END, "
            f"stage = CASE WHEN {newer} THEN excluded.stage ELSE spool.stage END, "
            f"usage_revision = CASE WHEN {newer} THEN excluded.usage_revision ELSE spool.usage_revision END, "
            f"session_id = CASE WHEN {newer} THEN excluded.session_id ELSE spool.session_id END, "
            f"mutable = CASE WHEN {newer} THEN excluded.mutable ELSE spool.mutable END",
            (source_id, endpoint, content_hash, target, path,
             json.dumps(body, ensure_ascii=False), _text(error)[:300], utc_now(),
             session_id or "", turn_index, mutable, stage, usage_revision),
        )
        if mutable and session_id:
            kept = self.db.execute(
                "SELECT turn_index, stage, usage_revision FROM spool "
                "WHERE source_id=? AND endpoint=? AND content_hash=?",
                (source_id, endpoint, content_hash),
            ).fetchone()
            if kept:
                turn_index, stage, usage_revision = (
                    int(kept[0]), int(kept[1]), int(kept[2] or 0))
            # A completion drops the prompt-only snapshot of the same turn.
            # An older turn drops too. The same stage stays, and ingest is immutable.
            # A later usage revision drops a stale total at that same turn.
            # Prune from the version the queue kept, not from a losing conflict.
            self.db.execute(
                "DELETE FROM spool WHERE session_id=? AND endpoint=? AND mutable=1 "
                "AND (turn_index<? OR (turn_index=? AND stage<?) "
                "OR (turn_index=? AND stage=? AND usage_revision<?))",
                (session_id, endpoint, turn_index, turn_index, stage,
                 turn_index, stage, usage_revision),
            )
        self.db.commit()
        return True

    def advance_delivered(self, source_id, endpoint, content_hash, session_id="",
                          turn_index=0, stage=0, usage_revision=0):
        """Raise the version recorded for a body that was already delivered.

        Ordering matches delivery: turn index, then stage, then usage revision.
        A duplicate cannot lower that version. Pending snapshots older than the
        version kept are removed in the same transaction.
        """
        turn_index = int(turn_index or 0)
        stage = int(stage or 0)
        usage_revision = int(usage_revision or 0)
        session_id = session_id or ""
        if self.db.in_transaction:
            self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "UPDATE delivered SET turn_index=?, stage=?, usage_revision=? "
            "WHERE source_id=? AND endpoint=? AND content_hash=? AND mutable=1 "
            "AND (turn_index<? OR (turn_index=? AND stage<?) "
            "OR (turn_index=? AND stage=? AND usage_revision<?))",
            (turn_index, stage, usage_revision,
             source_id, endpoint, content_hash,
             turn_index, turn_index, stage, turn_index, stage, usage_revision),
        )
        row = self.db.execute(
            "SELECT turn_index, stage, usage_revision FROM delivered "
            "WHERE source_id=? AND endpoint=? AND content_hash=? AND mutable=1",
            (source_id, endpoint, content_hash),
        ).fetchone()
        if row and session_id:
            kept_turn, kept_stage, kept_revision = (
                int(row[0]), int(row[1]), int(row[2] or 0))
            self.db.execute(
                "DELETE FROM spool WHERE session_id=? AND endpoint=? AND mutable=1 "
                "AND (turn_index<? OR (turn_index=? AND stage<?) "
                "OR (turn_index=? AND stage=? AND usage_revision<?))",
                (session_id, endpoint, kept_turn, kept_turn, kept_stage,
                 kept_turn, kept_stage, kept_revision),
            )
        self.db.commit()

    def drop_stale_pending(self, session_id, endpoint, turn_index, stage=0, usage_revision=0):
        if not session_id:
            return
        turn_index = int(turn_index)
        stage = int(stage)
        usage_revision = int(usage_revision or 0)
        self.db.execute(
            "DELETE FROM spool WHERE session_id=? AND endpoint=? AND mutable=1 "
            "AND (turn_index<? OR (turn_index=? AND stage<?) "
            "OR (turn_index=? AND stage=? AND usage_revision<?))",
            (session_id, endpoint, turn_index, turn_index, stage,
             turn_index, stage, usage_revision),
        )
        self.db.commit()

    def drop_spool(self, source_id, endpoint, content_hash):
        self.db.execute(
            "DELETE FROM spool WHERE source_id=? AND endpoint=? AND content_hash=?",
            (source_id, endpoint, content_hash),
        )
        self.db.commit()

    def pending(self, limit, targets=None):
        """Oldest spool rows, optionally only for targets this process can post.

        The limit applies after that filter, so a disabled twin-api cannot fill
        the window and leave coordination rows unflushed.
        """
        columns = (
            "source_id, endpoint, content_hash, target, path, body, attempts, "
            "session_id, turn_index, mutable, stage, usage_revision"
        )
        limit = int(limit)
        if targets is None:
            return self.db.execute(
                f"SELECT {columns} FROM spool ORDER BY created_at, source_id LIMIT ?",
                (limit,),
            ).fetchall()
        chosen = [target for target in targets if target]
        if not chosen:
            return []
        marks = ",".join("?" for _ in chosen)
        return self.db.execute(
            f"SELECT {columns} FROM spool WHERE target IN ({marks}) "
            "ORDER BY created_at, source_id LIMIT ?",
            tuple(chosen) + (limit,),
        ).fetchall()

    def note_failure(self, source_id, endpoint, content_hash, error):
        self.db.execute(
            "UPDATE spool SET attempts = attempts + 1, last_error=? "
            "WHERE source_id=? AND endpoint=? AND content_hash=?",
            (_text(error)[:300], source_id, endpoint, content_hash),
        )
        self.db.commit()

    def pending_count(self):
        return self.db.execute("SELECT COUNT(*) FROM spool").fetchone()[0]

    def save_prompt(self, session_id, generation_id, prompt, model, cwd, turn_index, agent_key=""):
        self.db.execute(
            "INSERT OR REPLACE INTO prompts (session_id, generation_id, agent_key, prompt, model, "
            "cwd, turn_index, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, generation_id, agent_key or "", prompt, model or "", cwd or "",
             int(turn_index), utc_now()),
        )
        self.db.commit()

    def load_prompt(self, session_id, generation_id, agent_key=""):
        row = self.db.execute(
            "SELECT prompt, model, cwd, turn_index FROM prompts "
            "WHERE session_id=? AND generation_id=? AND agent_key=?",
            (session_id, generation_id, agent_key or ""),
        ).fetchone()
        if row is None:
            return None
        return {"prompt": row[0], "model": row[1], "cwd": row[2], "turn_index": row[3]}

    def next_turn_index(self, session_id, agent_key=""):
        row = self.db.execute(
            "SELECT COALESCE(MAX(turn_index), 0) + 1 FROM prompts "
            "WHERE session_id=? AND agent_key=?",
            (session_id, agent_key or ""),
        ).fetchone()
        return int(row[0] if row else 1)

    def _bump_usage_revision(self, session_id, agent_key):
        """Advance the summary version. Caller holds the counter transaction."""
        self.db.execute(
            """
            INSERT INTO turn_high_water (
                session_id, agent_key, high_water, started_at, usage_revision
            ) VALUES (?, ?, 0, '', 1)
            ON CONFLICT(session_id, agent_key) DO UPDATE SET
                usage_revision = turn_high_water.usage_revision + 1
            """,
            (session_id, agent_key or ""),
        )

    def usage_revision(self, session_id, agent_key=""):
        row = self.db.execute(
            "SELECT usage_revision FROM turn_high_water WHERE session_id=? AND agent_key=?",
            (session_id, agent_key or ""),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def save_generation_usage(self, session_id, generation_id, agent_key, usage, included=False):
        """Keep the fullest raw snapshot of one generation. A repeat does not add again.

        `included` marks a generation already covered by a transcript total.
        A later save keeps that flag, so the counters are not added on top.
        """
        agent_key = agent_key or ""
        self.db.execute(
            """
            INSERT INTO generation_usage (
                session_id, generation_id, agent_key,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, in_transcript
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, generation_id, agent_key) DO UPDATE SET
                input_tokens = MAX(generation_usage.input_tokens, excluded.input_tokens),
                output_tokens = MAX(generation_usage.output_tokens, excluded.output_tokens),
                cache_read_tokens = MAX(generation_usage.cache_read_tokens, excluded.cache_read_tokens),
                cache_write_tokens = MAX(generation_usage.cache_write_tokens, excluded.cache_write_tokens),
                in_transcript = MAX(generation_usage.in_transcript, excluded.in_transcript)
            """,
            (session_id, generation_id, agent_key,
             int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
             int(usage.get("cache_read_tokens") or 0), int(usage.get("cache_write_tokens") or 0),
             1 if included else 0),
        )
        self._bump_usage_revision(session_id, agent_key)
        self.db.commit()

    def replace_transcript_usage(self, session_id, agent_key, usage, covered_turns,
                                 usage_turns=None, open_prompt=None, apply_usage=True):
        """Store a transcript total and how many turns that snapshot covers.

        A later snapshot with fewer turns is ignored, so an earlier generation
        cannot shrink the baseline. Mapped turns inside the accepted snapshot
        are cleared first, then only the usage-bearing subset is marked. A
        trailing prompt with no counters stays additive, and so does a
        payload-only generation past the window. A flag left by an older sync
        cannot survive a rescan via MAX in save_generation_usage. Coverage uses
        the stored transcript turn when a generation has one, so a stable
        publication index past the snapshot cannot drop a turn the file already
        counted.
        """
        agent_key = agent_key or ""
        covered_turns = int(covered_turns or 0)
        if usage_turns is None:
            usage_turns = list(range(1, covered_turns + 1))
        else:
            usage_turns = [int(index) for index in usage_turns if int(index) > 0]
        # The freshness read and every snapshot write share one immediate
        # transaction. An older scan that already passed the check cannot
        # commit after a newer scan, and a counter-free file still records
        # its length so a later shorter scan is rejected.
        if self.db.in_transaction:
            self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        stored_turns = self._stored_covered_turns(session_id, agent_key)
        if stored_turns is not None and stored_turns > covered_turns:
            self.db.rollback()
            return False
        if apply_usage:
            self._replace_transcript_counters(
                session_id, agent_key, usage, covered_turns, usage_turns)
        else:
            self._remember_snapshot_length(session_id, agent_key, covered_turns)
        self._write_high_water(session_id, agent_key, covered_turns)
        if open_prompt is not None:
            self._write_open_prompt(session_id, agent_key, open_prompt)
        if apply_usage:
            self._bump_usage_revision(session_id, agent_key)
        self.db.commit()
        return True

    def _stored_covered_turns(self, session_id, agent_key):
        """Covered length of the retained snapshot, or None when none is stored."""
        row = self.db.execute(
            "SELECT covered_turns FROM generation_usage "
            "WHERE session_id=? AND agent_key=? AND generation_id=?",
            (session_id, agent_key, _TRANSCRIPT_GENERATION),
        ).fetchone()
        if row is None:
            return None
        return int(row[0] or 0)

    def _remember_snapshot_length(self, session_id, agent_key, covered_turns):
        """Record an accepted length without replacing counters already stored."""
        self.db.execute(
            """
            INSERT INTO generation_usage (
                session_id, generation_id, agent_key,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                in_transcript, covered_turns
            ) VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?)
            ON CONFLICT(session_id, generation_id, agent_key) DO UPDATE SET
                covered_turns = MAX(generation_usage.covered_turns, excluded.covered_turns)
            """,
            (session_id, _TRANSCRIPT_GENERATION, agent_key, int(covered_turns or 0)),
        )

    def _replace_transcript_counters(self, session_id, agent_key, usage, covered_turns,
                                     usage_turns):
        """Caller holds the transaction and has already accepted the snapshot."""
        self.db.execute(
            """
            INSERT INTO generation_usage (
                session_id, generation_id, agent_key,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                in_transcript, covered_turns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(session_id, generation_id, agent_key) DO UPDATE SET
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                cache_read_tokens = excluded.cache_read_tokens,
                cache_write_tokens = excluded.cache_write_tokens,
                in_transcript = 0,
                covered_turns = excluded.covered_turns
            """,
            (session_id, _TRANSCRIPT_GENERATION, agent_key,
             int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
             int(usage.get("cache_read_tokens") or 0), int(usage.get("cache_write_tokens") or 0),
             covered_turns),
        )
        # turn_index is the stable publication id. transcript_turn is the
        # reader's turn and can sit behind that id. Clear both, then mark the
        # usage-bearing coverage turn so a later prompt cannot un-cover it.
        self.db.execute(
            """
            UPDATE generation_usage SET in_transcript=0
            WHERE session_id=? AND agent_key=? AND generation_id!=?
              AND generation_id IN (
                  SELECT generation_id FROM turn_map
                  WHERE session_id=? AND agent_key=?
                    AND (
                        turn_index<=?
                        OR (transcript_turn>0 AND transcript_turn<=?)
                    )
              )
            """,
            (session_id, agent_key, _TRANSCRIPT_GENERATION, session_id, agent_key,
             covered_turns, covered_turns),
        )
        if usage_turns:
            marks = ",".join("?" for _ in usage_turns)
            self.db.execute(
                f"""
                UPDATE generation_usage SET in_transcript=1
                WHERE session_id=? AND agent_key=? AND generation_id!=?
                  AND generation_id IN (
                      SELECT generation_id FROM turn_map
                      WHERE session_id=? AND agent_key=?
                        AND (
                            CASE WHEN transcript_turn>0 THEN transcript_turn
                                 ELSE turn_index END
                        )<=?
                        AND (
                            CASE WHEN transcript_turn>0 THEN transcript_turn
                                 ELSE turn_index END
                        ) IN ({marks})
                  )
                """,
                (session_id, agent_key, _TRANSCRIPT_GENERATION, session_id, agent_key,
                 covered_turns, *usage_turns),
            )

    def combined_usage(self, session_id, agent_key="", cursor=False):
        """Transcript total plus generations not already covered by that total.

        Cursor payload rows are stored raw and normalized after the per-generation
        max, so a partial snapshot cannot win on an already-netted input.
        """
        agent_key = agent_key or ""
        rows = self.db.execute(
            "SELECT generation_id, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens "
            "FROM generation_usage WHERE session_id=? AND agent_key=? "
            "AND (generation_id=? OR in_transcript=0)",
            (session_id, agent_key, _TRANSCRIPT_GENERATION),
        ).fetchall()
        total = _empty_usage()
        for generation_id, incoming, outgoing, read, written in rows:
            usage = {
                "input_tokens": int(incoming or 0),
                "output_tokens": int(outgoing or 0),
                "cache_read_tokens": int(read or 0),
                "cache_write_tokens": int(written or 0),
            }
            if cursor and generation_id != _TRANSCRIPT_GENERATION:
                usage = _normalize_cursor_usage(usage)
            for key in total:
                total[key] += int(usage.get(key) or 0)
        return total

    def publication_snapshot(self, session_id, agent_key="", cursor=False):
        """Combined usage and high water from one immediate read.

        A hook write blocks until the values are captured. The revision is the
        one committed with those counters, so an index allocated before the
        matching save cannot lend its version to this total.
        """
        if self.db.in_transaction:
            self.db.rollback()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            usage = self.combined_usage(session_id, agent_key, cursor=cursor)
            water = self.high_water(session_id, agent_key)
            revision = self.usage_revision(session_id, agent_key)
        finally:
            if self.db.in_transaction:
                self.db.rollback()
        return usage, water, revision

    def high_water(self, session_id, agent_key=""):
        """Highest turn index already claimed by a transcript, sync, or generation."""
        agent_key = agent_key or ""
        stored = self.db.execute(
            "SELECT high_water FROM turn_high_water WHERE session_id=? AND agent_key=?",
            (session_id, agent_key),
        ).fetchone()
        prompt = self.db.execute(
            "SELECT COALESCE(MAX(turn_index), 0) FROM prompts WHERE session_id=? AND agent_key=?",
            (session_id, agent_key),
        ).fetchone()
        mapped = self.db.execute(
            "SELECT COALESCE(MAX(turn_index), 0) FROM turn_map WHERE session_id=? AND agent_key=?",
            (session_id, agent_key),
        ).fetchone()
        return max(int(stored[0]) if stored else 0, int(prompt[0] or 0), int(mapped[0] or 0))

    def _write_high_water(self, session_id, agent_key, count):
        """Raise the shared turn counter. Caller owns the transaction."""
        agent_key = agent_key or ""
        self.db.execute(
            """
            INSERT INTO turn_high_water (session_id, agent_key, high_water, started_at)
            VALUES (?, ?, ?, '')
            ON CONFLICT(session_id, agent_key) DO UPDATE SET
                high_water = MAX(turn_high_water.high_water, excluded.high_water)
            """,
            (session_id, agent_key, int(count or 0)),
        )

    def note_high_water(self, session_id, agent_key, count):
        """Record how many turns a transcript or sync has already published."""
        self._write_high_water(session_id, agent_key, count)
        self.db.commit()

    def _write_open_prompt(self, session_id, agent_key, turn_index):
        """Caller holds the transaction. Only a prompt with no assistant is open."""
        self.db.execute(
            """
            INSERT INTO turn_high_water (
                session_id, agent_key, high_water, started_at, open_prompt
            ) VALUES (?, ?, 0, '', ?)
            ON CONFLICT(session_id, agent_key) DO UPDATE SET
                open_prompt = excluded.open_prompt
            """,
            (session_id, agent_key or "", int(turn_index or 0)),
        )

    def transcript_turn_for(self, session_id, agent_key, generation_id):
        row = self.db.execute(
            "SELECT transcript_turn FROM turn_map "
            "WHERE session_id=? AND agent_key=? AND generation_id=?",
            (session_id, agent_key or "", generation_id),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def bind_turn(self, session_id, agent_key, generation_id, turn_index):
        """Map a generation onto a transcript turn index without lowering either."""
        agent_key = agent_key or ""
        turn_index = int(turn_index)
        self.db.execute(
            """
            INSERT INTO turn_map (session_id, agent_key, generation_id, turn_index, seen_at)
            VALUES (?, ?, ?, ?, '')
            ON CONFLICT(session_id, agent_key, generation_id) DO UPDATE SET
                turn_index = MAX(turn_map.turn_index, excluded.turn_index)
            """,
            (session_id, agent_key, generation_id, turn_index),
        )
        self.note_high_water(session_id, agent_key, turn_index)
        row = self.db.execute(
            "SELECT turn_index FROM turn_map WHERE session_id=? AND agent_key=? AND generation_id=?",
            (session_id, agent_key, generation_id),
        ).fetchone()
        return int(row[0])

    def remember_transcript_turn(self, session_id, agent_key, generation_id, transcript_turn):
        """Record the reader's turn without moving the stable publication index."""
        transcript_turn = int(transcript_turn or 0)
        if transcript_turn <= 0:
            return
        self.db.execute(
            "UPDATE turn_map SET transcript_turn=? "
            "WHERE session_id=? AND agent_key=? AND generation_id=?",
            (transcript_turn, session_id, agent_key or "", generation_id),
        )
        self.db.commit()

    def ensure_turn(self, session_id, agent_key, generation_id, seen_at=""):
        """Return the stable index and first-seen timestamp for one generation.

        The index is allocated only after the shared high water, so a payload-only
        generation cannot reuse a turn a transcript or sync already published.
        The read, insert, and high-water update share one immediate transaction.
        A concurrent sync cannot advance the counter in the gap, and two
        allocations cannot take the same index. A conflicting insert retries.
        """
        agent_key = agent_key or ""
        last_error = None
        for _attempt in range(6):
            if self.db.in_transaction:
                self.db.rollback()
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT turn_index, seen_at FROM turn_map "
                    "WHERE session_id=? AND agent_key=? AND generation_id=?",
                    (session_id, agent_key, generation_id),
                ).fetchone()
                if row:
                    index, stored = int(row[0]), row[1] or ""
                    if seen_at and not stored:
                        self.db.execute(
                            "UPDATE turn_map SET seen_at=? "
                            "WHERE session_id=? AND agent_key=? AND generation_id=?",
                            (seen_at, session_id, agent_key, generation_id),
                        )
                        stored = seen_at
                    self.db.commit()
                    return index, stored
                index = self.high_water(session_id, agent_key) + 1
                # The first payload after an open prompt belongs to that turn.
                # A second generation must not claim it, or its counters would
                # disappear once the prompt gains usage.
                open_row = self.db.execute(
                    "SELECT open_prompt FROM turn_high_water "
                    "WHERE session_id=? AND agent_key=?",
                    (session_id, agent_key),
                ).fetchone()
                open_prompt = int(open_row[0] or 0) if open_row else 0
                transcript_turn = 0
                if open_prompt:
                    taken = self.db.execute(
                        "SELECT 1 FROM turn_map WHERE session_id=? AND agent_key=? "
                        "AND CASE WHEN transcript_turn>0 THEN transcript_turn "
                        "ELSE turn_index END=? LIMIT 1",
                        (session_id, agent_key, open_prompt),
                    ).fetchone()
                    if not taken:
                        transcript_turn = open_prompt
                self.db.execute(
                    "INSERT INTO turn_map ("
                    "session_id, agent_key, generation_id, turn_index, seen_at, transcript_turn"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, agent_key, generation_id, index, seen_at or "", transcript_turn),
                )
                self._write_high_water(session_id, agent_key, index)
                self.db.commit()
                return index, seen_at or ""
            except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
                last_error = exc
                if self.db.in_transaction:
                    self.db.rollback()
                continue
        raise sqlite3.OperationalError("could not allocate a turn index") from last_error

    def remember_started(self, session_id, agent_key, candidate):
        """Keep the earliest session start. Later completions must not move it."""
        agent_key = agent_key or ""
        row = self.db.execute(
            "SELECT started_at FROM turn_high_water WHERE session_id=? AND agent_key=?",
            (session_id, agent_key),
        ).fetchone()
        existing = row[0] if row and row[0] else ""
        chosen = _earliest_timestamp(existing, candidate)
        if row is None:
            self.db.execute(
                "INSERT INTO turn_high_water (session_id, agent_key, high_water, started_at) "
                "VALUES (?, ?, 0, ?)",
                (session_id, agent_key, chosen),
            )
        elif chosen != existing:
            self.db.execute(
                "UPDATE turn_high_water SET started_at=? WHERE session_id=? AND agent_key=?",
                (chosen, session_id, agent_key),
            )
        self.db.commit()
        return chosen


def post_json(url, body, token, timeout):
    """POST JSON. 2xx and 409 count as delivered. Other HTTP statuses and
    network errors raise DeliveryError so the caller can spool the body."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            resp.read()
        if 200 <= status < 300 or status == 409:
            return status
        raise DeliveryError("HTTP " + str(status))
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 300 or exc.code == 409:
            return exc.code
        raise DeliveryError("HTTP " + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # socket timeouts are TimeoutError (an OSError), not URLError, on every
        # supported Python. Spool them instead of escaping the hook.
        raise DeliveryError(str(getattr(exc, "reason", exc))) from exc


class Bridge:
    """Sends one process's posts. The first network failure spools the rest."""

    def __init__(self, config, poster=None, store=None):
        self.config = config
        self.poster = poster or post_json
        self.store = store or Store(config.state_dir)
        self.offline = False
        self.delivered = 0
        self.spooled = 0

    def close(self):
        self.store.close()

    def send(self, endpoint, source_id, body, session_id="", turn_index=0, stage=0,
             usage_revision=0):
        target, path = ENDPOINTS[endpoint]
        base = self.config.coord_url if target == "coord" else self.config.twin_url
        if not base:
            return False
        content_hash = _hash(body)
        mutable = 1 if endpoint in MUTABLE_ENDPOINTS else 0
        turn_index = int(turn_index or 0)
        stage = int(stage or 0)
        usage_revision = int(usage_revision or 0)
        if mutable and self.store.newer_delivered(
            session_id, endpoint, turn_index, stage, usage_revision,
        ):
            return True
        if self.store.is_delivered(source_id, endpoint, content_hash):
            if mutable:
                self.store.advance_delivered(
                    source_id, endpoint, content_hash, session_id, turn_index, stage,
                    usage_revision)
            return True
        if self.offline:
            if self.store.enqueue(
                source_id, endpoint, content_hash, target, path, body, "offline",
                session_id, turn_index, mutable, stage, usage_revision,
            ):
                self.spooled += 1
            return False
        token = self.config.coord_token if target == "coord" else self.config.twin_token
        try:
            self.poster(base + path, body, token, self.config.timeout)
        except (DeliveryError, TimeoutError, OSError) as exc:
            self.offline = True
            if self.store.enqueue(
                source_id, endpoint, content_hash, target, path, body, str(exc),
                session_id, turn_index, mutable, stage, usage_revision,
            ):
                self.spooled += 1
            return False
        self.store.acknowledge_delivery(
            source_id, endpoint, content_hash, session_id, turn_index, mutable, stage,
            usage_revision)
        self.delivered += 1
        return True

    def flush(self, limit=50):
        sent = 0
        targets = []
        if self.config.coord_url:
            targets.append("coord")
        if self.config.twin_url:
            targets.append("twin")
        for row in self.store.pending(limit, targets):
            (source_id, endpoint, content_hash, target, path, body, _attempts,
             session_id, turn_index, mutable, stage, usage_revision) = row
            session_id = session_id or ""
            turn_index = int(turn_index or 0)
            mutable = int(mutable or 0)
            stage = int(stage or 0)
            usage_revision = int(usage_revision or 0)
            if self.offline:
                break
            base = self.config.coord_url if target == "coord" else self.config.twin_url
            if not base:
                continue
            if mutable and self.store.newer_delivered(
                session_id, endpoint, turn_index, stage, usage_revision,
            ):
                self.store.drop_spool(source_id, endpoint, content_hash)
                continue
            if self.store.is_delivered(source_id, endpoint, content_hash):
                if mutable:
                    self.store.advance_delivered(
                        source_id, endpoint, content_hash, session_id, turn_index, stage,
                        usage_revision)
                self.store.drop_spool(source_id, endpoint, content_hash)
                continue
            token = self.config.coord_token if target == "coord" else self.config.twin_token
            try:
                self.poster(base + path, json.loads(body), token, self.config.timeout)
            except (DeliveryError, TimeoutError, OSError, ValueError) as exc:
                self.offline = True
                self.store.note_failure(source_id, endpoint, content_hash, str(exc))
            else:
                self.store.acknowledge_delivery(
                    source_id, endpoint, content_hash, session_id, turn_index, mutable, stage,
                    usage_revision)
                sent += 1
                self.delivered += 1
        return sent


def _content(rec):
    msg = rec.get("message")
    if isinstance(msg, dict) and "content" in msg:
        return msg.get("content")
    if "content" in rec:
        return rec.get("content")
    if isinstance(rec.get("text"), str):
        return rec.get("text")
    return ""


def _text_from_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, (int, float)) and not isinstance(content, bool):
        return str(content)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"].strip()
        nested = content.get("content")
        if isinstance(nested, str):
            return nested.strip()
        return ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block.strip())
                continue
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind in {"tool_result", "tool_result_error", "tool_use", "tool_call"}:
                continue
            if isinstance(block.get("text"), str) and block["text"].strip():
                parts.append(block["text"].strip())
            elif (
                kind in {None, "text", "output_text"}
                and isinstance(block.get("content"), str)
                and block["content"].strip()
            ):
                parts.append(block["content"].strip())
        return "\n".join(parts).strip()
    return ""


def _role(rec):
    typ = rec.get("type")
    if isinstance(typ, str) and typ in _SKIP_TYPES:
        return ""
    if typ in {"tool_result", "tool"}:
        return "tool"
    msg = rec.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("role"), str):
        return msg["role"].strip().lower()
    if isinstance(rec.get("role"), str):
        return rec["role"].strip().lower()
    speaker = rec.get("speaker") or rec.get("author")
    if isinstance(speaker, str):
        speaker = speaker.strip().lower()
        if speaker in {"human", "user"}:
            return "user"
        if speaker in {"assistant", "ai", "bot", "grok", "model"}:
            return "assistant"
        if speaker == "tool":
            return "tool"
    if typ in {"user", "assistant"}:
        return typ
    return ""


def _model(rec):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    for candidate in (msg.get("model"), rec.get("model"), rec.get("subagent_model")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _timestamp(rec):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    for candidate in (rec.get("timestamp"), rec.get("ts"), rec.get("created_at"),
                      rec.get("time"), msg.get("timestamp")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _num(usage, *keys):
    for key in keys:
        val = usage.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val)
    return 0


def _empty_usage():
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def _any_usage(usage):
    return any(int(usage.get(key) or 0) for key in _empty_usage())


def _codex_counter(usage, key):
    """Codex counters are non-negative numbers. A bad value fails open as None."""
    value = usage.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _codex_flat_usage(total):
    """Net Codex totals. input_tokens includes cache; reasoning is inside output.

    Matches `_codex_flat` in token_usage.py, but returns None instead of raising
    so a hook can skip one bad notification.
    """
    if not isinstance(total, dict):
        return None
    if "input_tokens" not in total or "output_tokens" not in total:
        return None
    cached = _codex_counter(total, "cached_input_tokens")
    written = _codex_counter(total, "cache_write_input_tokens")
    incoming = _codex_counter(total, "input_tokens")
    outgoing = _codex_counter(total, "output_tokens")
    if None in (cached, written, incoming, outgoing):
        return None
    if cached + written > incoming:
        return None
    return {
        "input_tokens": incoming - cached - written,
        "output_tokens": outgoing,
        "cache_read_tokens": cached,
        "cache_write_tokens": written,
    }


def _codex_owner_id(rec):
    """Owning rollout id from a raw or normalized session_meta record."""
    if not isinstance(rec, dict) or rec.get("type") != "session_meta":
        return ""
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    for source in (payload, rec):
        for key in ("id", "session_id"):
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _codex_thread_id(rec):
    """thread_id from a raw payload record or one already lifted to the top level."""
    if not isinstance(rec, dict):
        return ""
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    for source in (payload, rec):
        val = source.get("thread_id")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _codex_record_is_local(rec, owner_id):
    """True when a token_usage_record belongs to this rollout.

    Matches parse_codex_session: a thread_id that differs from the session_meta
    id is inherited or forwarded and must not count. A record with no thread_id
    stays local. A thread_id with no known owner is foreign, so it cannot select
    the per-response stream or disable local cumulative totals.
    """
    thread_id = _codex_thread_id(rec)
    if not thread_id:
        return True
    return bool(owner_id) and thread_id == owner_id


def _codex_usage_choice(records):
    """`(mode, owner_id)` for one rollout.

    Only a local token_usage_record selects the per-response stream. Foreign
    records are ignored here so they cannot turn off event_msg token_count.
    """
    owner = ""
    saw_bare = False
    threads = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if not owner:
            owner = _codex_owner_id(rec) or owner
        if rec.get("type") != "token_usage_record":
            continue
        thread_id = _codex_thread_id(rec)
        if thread_id:
            threads.append(thread_id)
        else:
            saw_bare = True
    local = saw_bare or any(owner and thread == owner for thread in threads)
    return ("records" if local else "cumulative"), owner


def _codex_usage_mode(records):
    """Per-response records win for the whole file. Otherwise totals are cumulative."""
    mode, _owner = _codex_usage_choice(records)
    return mode


def _usage(rec):
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    usage = rec.get("usage") or msg.get("usage") or rec.get("token_usage") or rec.get("tokens") or {}
    if not isinstance(usage, dict):
        return {}
    creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    five_m = creation.get("ephemeral_5m_input_tokens")
    one_h = creation.get("ephemeral_1h_input_tokens")
    if five_m is None and one_h is None:
        # Flat total is the whole cache-creation count. Nested TTL buckets, when
        # present, already include that total and must not be added again.
        cache_write = _num(
            usage, "cache_write_tokens", "cache_creation_input_tokens",
            "cache_write_input_tokens", "cache_write",
        )
    else:
        cache_write = int(five_m or 0) + int(one_h or 0)
    input_tokens = _num(usage, "input_tokens", "input")
    output_tokens = _num(usage, "output_tokens", "output")
    cache_read = _num(
        usage, "cache_read_tokens", "cache_read_input_tokens", "cache_read", "cached_input_tokens",
    )
    # Codex input_tokens already includes cached_input_tokens and
    # cache_write_input_tokens. Claude's cache_read_input_tokens does not.
    codex_keys = "cached_input_tokens" in usage or "cache_write_input_tokens" in usage
    if codex_keys:
        flat = _codex_flat_usage(usage)
        if flat is not None:
            input_tokens = flat["input_tokens"]
            output_tokens = flat["output_tokens"]
            cache_read = flat["cache_read_tokens"]
            if five_m is None and one_h is None:
                cache_write = flat["cache_write_tokens"]
        else:
            cached = _num(usage, "cached_input_tokens")
            written = _num(usage, "cache_write_input_tokens")
            if cached + written > input_tokens:
                input_tokens = 0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


def _request_id(rec):
    sources = [rec]
    msg = rec.get("message")
    if isinstance(msg, dict):
        sources.append(msg)
    for source in sources:
        for key in ("requestId", "request_id", "response_id"):
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _max_usage(dest, usage):
    for key in dest:
        dest[key] = max(int(dest[key]), int(usage.get(key) or 0))


def _bounded_input(value):
    """Keep tool arguments, but don't retain a multi-megabyte blob on the turn."""
    coerced = _coerce_input(value)
    if isinstance(coerced, str):
        return clip(coerced)
    try:
        raw = json.dumps(coerced, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return clip(str(coerced))
    if len(raw) <= FIELD_LIMIT:
        return coerced
    return clip(raw)


def _coerce_input(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[":
            try:
                return json.loads(stripped)
            except ValueError:
                return value
        return value
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    return {"value": value}


def _tool_calls(rec, content):
    calls = []
    blocks = content if isinstance(content, list) else []
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    extras = []
    for source in (rec.get("tool_calls"), msg.get("tool_calls")):
        if isinstance(source, list):
            extras.extend(source)
    for block in list(blocks) + extras:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind not in {None, "tool_use", "tool_call", "function"} and "name" not in block and "function" not in block:
            continue
        fn = block.get("function") if isinstance(block.get("function"), dict) else {}
        name = block.get("name") or block.get("tool") or fn.get("name")
        if not name:
            continue
        if kind not in {None, "tool_use", "tool_call", "function"} and "input" not in block and "arguments" not in block:
            continue
        raw_input = block.get("input") if "input" in block else block.get("arguments", fn.get("arguments"))
        calls.append({
            "id": str(block.get("id") or block.get("tool_use_id") or ""),
            "name": str(name),
            "input": _bounded_input(raw_input),
            "result": "",
        })
    return calls


def _tool_results(content):
    results = []
    if not isinstance(content, list):
        return results
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {"tool_result", "tool_result_error"}:
            continue
        results.append({
            "id": str(block.get("tool_use_id") or block.get("id") or ""),
            "name": str(block.get("name") or ""),
            "text": clip(_text_from_content(block.get("content"))),
        })
    return results


def _tool_only(content):
    if not isinstance(content, list) or not content:
        return False
    saw = False
    for block in content:
        if not isinstance(block, dict):
            return False
        if block.get("type") not in {"tool_result", "tool_result_error"}:
            return False
        saw = True
    return saw


def _attach_results(turn, results):
    for result in results:
        if not result:
            continue
        matched = None
        rid = result.get("id") or ""
        name = result.get("name") or ""
        if rid:
            for call in turn.tools:
                if call.get("id") == rid:
                    matched = call
                    break
        if matched is None and name:
            for call in turn.tools:
                if call.get("name") == name and not call.get("result"):
                    matched = call
                    break
        if matched is None:
            matched = {"id": rid, "name": name or "tool", "input": {}, "result": ""}
            turn.tools.append(matched)
        text = clip(result.get("text") or "")
        if not text:
            continue
        if matched["result"]:
            matched["result"] = clip(matched["result"] + "\n" + text)
        else:
            matched["result"] = text


def _tool_message_result(rec):
    return {
        "id": str(rec.get("tool_call_id") or rec.get("tool_use_id") or rec.get("id") or ""),
        "name": str(rec.get("name") or rec.get("tool") or ""),
        "text": clip(_text_from_content(_content(rec))),
    }


def _shape_codex_tool(merged):
    """Turn a Codex function_call into an assistant tool call, and its output into a result.

    These records have no role. `call_id` is what attaches the output to the call.
    """
    kind = merged.get("type")
    call_id = str(merged.get("call_id") or merged.get("id") or "")
    if kind == "function_call":
        fn = merged.get("function") if isinstance(merged.get("function"), dict) else {}
        name = merged.get("name") or fn.get("name") or "tool"
        if "arguments" in merged:
            raw_input = merged.get("arguments")
        elif "arguments" in fn:
            raw_input = fn.get("arguments")
        else:
            raw_input = merged.get("input") if "input" in merged else {}
        merged["role"] = "assistant"
        merged["tool_calls"] = [{
            "id": call_id,
            "name": str(name),
            "arguments": {} if raw_input is None else raw_input,
        }]
        return merged
    output = merged.get("output")
    if output is None:
        output = merged.get("content") or ""
    merged["role"] = "tool"
    merged["tool_call_id"] = call_id
    merged["content"] = output
    return merged


def _normalize_record(rec):
    """Lift Codex payload records so role, content, usage, and model are top-level."""
    if not isinstance(rec, dict) or rec.get("_codex_norm"):
        return rec
    typ = rec.get("type")
    payload = rec.get("payload")
    if typ in {"function_call", "function_call_output"} and not isinstance(payload, dict):
        shaped = dict(rec)
        shaped["_codex_norm"] = True
        return _shape_codex_tool(shaped)
    if typ not in _CODEX_PAYLOAD_TYPES or not isinstance(payload, dict):
        return rec
    merged = dict(payload)
    for key in ("timestamp", "ts"):
        if key not in merged and isinstance(rec.get(key), str):
            merged[key] = rec[key]
    merged["_codex_norm"] = True
    if typ == "response_item":
        merged.setdefault("type", payload.get("type") or "message")
        if merged.get("type") in {"function_call", "function_call_output"}:
            return _shape_codex_tool(merged)
    else:
        merged["type"] = typ
    if typ == "token_usage_record":
        response_id = payload.get("response_id")
        if isinstance(response_id, str) and response_id.strip() and "requestId" not in merged:
            merged["requestId"] = response_id.strip()
    return merged


def _apply_usage(turn, rec):
    usage = _usage(rec)
    if not any(usage.values()):
        return
    request_id = _request_id(rec)
    if not request_id:
        turn.add_usage(usage)
        return
    bucket = turn.request_usage.get(request_id)
    if bucket is None:
        bucket = _empty_usage()
        turn.request_usage[request_id] = bucket
    _max_usage(bucket, usage)


def iter_turns(records, on_record=None, codex_usage=None, codex_owner=None):
    """Yield turns one at a time so a large JSONL file is not kept twice.

    Tool text is clipped as it is attached. Assistant usage snapshots that share
    a requestId keep per-field maxima; snapshots with no id still sum.

    Codex rollouts that contain a local token_usage_record use only that stream.
    A record whose thread_id belongs to another rollout is ignored, and it does
    not disable event_msg token_count. Otherwise those totals are cumulative
    and become deltas. A turn_context that arrives after the open turn already
    has a prompt or assistant text belongs to the next turn. It must not
    relabel this one.
    """
    if isinstance(records, (list, tuple)) and (codex_usage is None or codex_owner is None):
        mode, owner = _codex_usage_choice(records)
        if codex_usage is None:
            codex_usage = mode
        if codex_owner is None:
            codex_owner = owner
    elif codex_usage is None:
        codex_usage = "records"
    codex_owner = codex_owner or ""
    current = None
    pending_model = ""
    pending_turn_id = ""
    pending_turn = None
    divert = False
    previous_cumulative = None

    def fold(turn):
        if turn is None:
            return
        for bucket in turn.request_usage.values():
            turn.add_usage(bucket)
        turn.request_usage = {}

    def close_content():
        """Return the open turn when it has chat text. Leave a usage-only turn open."""
        nonlocal current
        if current is None:
            return None
        fold(current)
        if not current.prompt and not current.has_assistant:
            return None
        finished = current
        current = None
        return finished

    def usage_target():
        """Usage after a new turn_context waits for that turn instead of the previous one."""
        nonlocal current, pending_turn
        if divert:
            if pending_turn is None:
                pending_turn = Turn()
                pending_turn.model = pending_model
                pending_turn.turn_id = pending_turn_id
            return pending_turn
        if current is None:
            current = Turn()
            if pending_model and not current.model:
                current.model = pending_model
            if pending_turn_id and not current.turn_id:
                current.turn_id = pending_turn_id
        return current

    def apply_cumulative(rec):
        nonlocal previous_cumulative
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else None
        total = info.get("total_token_usage") if info is not None else None
        flat = _codex_flat_usage(total) if isinstance(total, dict) else None
        if flat is None:
            return
        if previous_cumulative is not None and any(flat[key] < previous_cumulative[key] for key in flat):
            previous_cumulative = flat
            return
        if previous_cumulative is None:
            delta = flat
        else:
            delta = {key: flat[key] - previous_cumulative[key] for key in flat}
        previous_cumulative = flat
        if not any(delta.values()):
            return
        target = usage_target()
        target.add_usage(delta)
        seen_at = _timestamp(rec)
        if seen_at and not target.timestamp:
            target.timestamp = seen_at

    for rec in records:
        if not isinstance(rec, dict):
            continue
        rec = _normalize_record(rec)
        if on_record is not None:
            on_record(rec)
        if not codex_owner:
            codex_owner = _codex_owner_id(rec) or codex_owner
        typ = rec.get("type")
        if typ == "turn_context":
            found = _model(rec)
            raw_id = rec.get("turn_id")
            turn_id = raw_id.strip() if isinstance(raw_id, str) else ""
            if found:
                pending_model = found
            if turn_id:
                pending_turn_id = turn_id
            same_turn = bool(turn_id) and current is not None and current.turn_id == turn_id
            started = current is not None and bool(current.prompt or current.has_assistant)
            if started and not same_turn:
                # The next turn's context often precedes its user message.
                divert = True
                if pending_turn is not None and found:
                    pending_turn.model = found
                if pending_turn is not None and turn_id:
                    pending_turn.turn_id = turn_id
            elif current is not None:
                if turn_id and not current.turn_id:
                    current.turn_id = turn_id
                if found and not current.model:
                    current.model = found
                seen_at = _timestamp(rec)
                if seen_at and not current.timestamp:
                    current.timestamp = seen_at
            continue
        if typ == "event_msg":
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if payload.get("type") == "token_count" and codex_usage == "cumulative":
                apply_cumulative(rec)
            continue
        if typ == "token_usage_record":
            # Foreign thread_ids are inherited or forwarded. Counting them inflates
            # this session, and letting them select records mode drops the local
            # cumulative stream. Apply a record only when it belongs here.
            if codex_usage != "records" or not _codex_record_is_local(rec, codex_owner):
                continue
            target = usage_target()
            _apply_usage(target, rec)
            seen_at = _timestamp(rec)
            if seen_at and not target.timestamp:
                target.timestamp = seen_at
            continue
        role = _role(rec)
        if not role or role == "system":
            continue
        content = _content(rec)
        ts = _timestamp(rec)
        model = _model(rec)
        if role == "user":
            results = _tool_results(content)
            text = "" if _tool_only(content) else _text_from_content(content)
            if results and not text:
                if current is None:
                    current = Turn()
                _attach_results(current, results)
                if ts and not current.timestamp:
                    current.timestamp = ts
                if model:
                    current.model = model
                continue
            finished = close_content()
            if finished is not None:
                yield finished
            carried = None
            if current is not None:
                fold(current)
                carried = current
                current = None
            extra = None
            if pending_turn is not None:
                fold(pending_turn)
                extra = pending_turn
                pending_turn = None
            divert = False
            current = Turn()
            if carried is not None:
                current.add_usage(carried.usage)
                if carried.turn_id:
                    current.turn_id = carried.turn_id
                if carried.model and not pending_model:
                    current.model = carried.model
                if carried.timestamp and not ts:
                    current.timestamp = carried.timestamp
            if extra is not None:
                current.add_usage(extra.usage)
                if extra.turn_id:
                    current.turn_id = extra.turn_id
                if extra.model:
                    current.model = extra.model
                if extra.timestamp and not current.timestamp:
                    current.timestamp = extra.timestamp
            current.prompt = clip(text)
            current.timestamp = ts or current.timestamp
            if pending_turn_id and not current.turn_id:
                current.turn_id = pending_turn_id
            if pending_model:
                current.model = pending_model
                pending_model = ""
            if model:
                current.model = model
            if results:
                _attach_results(current, results)
            continue
        if current is None:
            current = Turn()
            current.timestamp = ts
            if pending_turn_id and not divert:
                current.turn_id = pending_turn_id
            if pending_model and not divert:
                current.model = pending_model
                pending_model = ""
        if model:
            current.model = model
        elif pending_model and not current.model and not divert:
            current.model = pending_model
            pending_model = ""
        if ts and not current.timestamp:
            current.timestamp = ts
        if role == "assistant":
            text = _text_from_content(content)
            if text:
                current.assistant_parts.append(clip(text))
            seen = {
                (call.get("id"), call.get("name"), _input_text(call.get("input")))
                for call in current.tools
            }
            for call in _tool_calls(rec, content):
                key = (call.get("id"), call.get("name"), _input_text(call.get("input")))
                if key in seen:
                    continue
                seen.add(key)
                current.tools.append(call)
            _apply_usage(current, rec)
            continue
        if role == "tool":
            _attach_results(current, [_tool_message_result(rec)])
    finished = close_content()
    if finished is not None:
        yield finished
    if current is not None:
        fold(current)
        if current.prompt or current.has_assistant or _any_usage(current.usage):
            yield current
    if pending_turn is not None:
        fold(pending_turn)
        if pending_turn.prompt or pending_turn.has_assistant or _any_usage(pending_turn.usage):
            yield pending_turn


def extract_turns(records):
    """Group JSONL records into turns. Tool results stay on the open turn."""
    return list(iter_turns(records))


def _iter_records(path):
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def read_jsonl(path):
    return list(_iter_records(path))


def _path_codex_choice(path):
    """Second read so a one-pass extract can pick the usage stream and owner."""
    return _codex_usage_choice(_iter_records(path))


def _path_codex_mode(path):
    """Second read so a one-pass extract can pick records vs cumulative totals."""
    mode, _owner = _path_codex_choice(path)
    return mode


def _identity_state(path):
    path = Path(path)
    session_id = path.stem
    if path.name.lower() in {"transcript.jsonl", "session.jsonl", "messages.jsonl"} and path.parent.name:
        session_id = path.parent.name
    return {"session_id": session_id, "model": "", "started": "", "updated": "", "cwd": ""}


def _observe_identity(path, state, rec):
    path = Path(path)
    rec = _normalize_record(rec)
    if rec.get("type") in {"session_meta", "session"}:
        for key in ("session_id", "id", "conversation_id"):
            if (
                isinstance(rec.get(key), str)
                and rec[key].strip()
                and state["session_id"] == path.stem
            ):
                state["session_id"] = rec[key].strip()
                break
        if isinstance(rec.get("model"), str) and rec["model"].strip() and not state["model"]:
            state["model"] = rec["model"].strip()
        if isinstance(rec.get("cwd"), str) and rec["cwd"].strip() and not state["cwd"]:
            state["cwd"] = rec["cwd"].strip()
    if (
        isinstance(rec.get("session_id"), str)
        and rec["session_id"].strip()
        and state["session_id"] in {path.stem, path.parent.name}
    ):
        state["session_id"] = rec["session_id"].strip()
    if not state["model"]:
        found = _model(rec)
        if found:
            state["model"] = found
    seen_at = _timestamp(rec)
    if seen_at:
        if not state["started"]:
            state["started"] = seen_at
        state["updated"] = seen_at
    if not state["cwd"]:
        for key in ("cwd", "workspace"):
            if isinstance(rec.get(key), str) and rec[key].strip():
                state["cwd"] = rec[key].strip()
                break


def session_identity(path, records):
    """(session_id, model, started_at, cwd) from a transcript path and records."""
    state = _identity_state(path)
    for rec in records:
        if isinstance(rec, dict):
            _observe_identity(path, state, rec)
    if not state["cwd"]:
        state["cwd"] = str(Path(path).parent)
    return state["session_id"], state["model"], state["started"], state["cwd"]


def _input_text(value):
    if value is None or value == {}:
        return "{}"
    if isinstance(value, str):
        return clip(value)
    try:
        return clip(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return clip(str(value))


def format_turn_markdown(vendor, model, turn):
    """The twin-api `content` body for one turn."""
    lines = [
        "# Chat turn — {}/{}".format(vendor_name(vendor), model or "unknown"),
        "",
        "## User",
        clip(turn.prompt),
        "",
        "## Tools",
    ]
    if not turn.tools:
        lines.append("(none)")
    else:
        for call in turn.tools:
            lines.append("- `{}` {}".format(call.get("name") or "tool", _input_text(call.get("input"))))
            if call.get("result"):
                result = clip(call["result"]).replace("\n", "\n  ")
                lines.append("  result: " + result)
    lines.extend(["", "## Assistant", clip(turn.assistant), ""])
    return "\n".join(lines)


def source_id_for(session_id, turn_index, subagent_id=""):
    if subagent_id:
        return f"{session_id}:{subagent_id}:turn-{int(turn_index)}"
    return f"{session_id}:turn-{int(turn_index)}"


def sum_usage(turns):
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    for turn in turns:
        for key in total:
            total[key] += int(turn.usage.get(key) or 0)
    return total


def _payload_tokens(payload):
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    found = {}
    mapping = {
        "input_tokens": ("input_tokens", "input"),
        "output_tokens": ("output_tokens", "output"),
        "cache_read_tokens": ("cache_read_tokens", "cache_read"),
        "cache_write_tokens": ("cache_write_tokens", "cache_write"),
    }
    for dest, keys in mapping.items():
        for source in (payload, nested):
            for key in keys:
                val = source.get(key) if isinstance(source, dict) else None
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    found[dest] = int(val)
                    break
            if dest in found:
                break
    return found


def _cost(payload):
    if not isinstance(payload, dict):
        return 0.0, "unpriced"
    for key in ("session_cost_usd", "cost_usd", "cost"):
        val = payload.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val), "payload"
    return 0.0, "unpriced"


def register_body(session_id, machine, cwd, repo, branch, vendor, model, cost_basis):
    return {
        "session_id": session_id,
        "machine": machine,
        "cwd": cwd or "",
        "repo": repo or "",
        "branch": branch or "",
        "vendor": vendor_name(vendor),
        "model": model or "unknown",
        "cost_basis": cost_basis or "unpriced",
    }


def heartbeat_body(session_id, prompt, output, turn_index, cost, model, vendor, surface):
    return {
        "session_id": session_id,
        "last_prompt": clip(prompt),
        "last_output": clip(output),
        "turn_index": int(turn_index),
        "session_cost_usd": float(cost or 0.0),
        "model": model or "unknown",
        "vendor": vendor_name(vendor),
        "surface": surface,
    }


def ai_sessions_body(session_id, vendor, surface, usage, turns, repo, branch,
                     started_at, updated_at, model, machine, cost, cost_basis):
    usage = usage or {}
    return {
        "vendor": vendor_name(vendor),
        "surface": surface,
        "session_id": session_id,
        "model": model or "unknown",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
        "turns": int(turns),
        "repo": repo or "",
        "branch": branch or "",
        "machine": machine,
        "started_at": started_at or updated_at,
        "updated_at": updated_at,
        "session_cost_usd": float(cost or 0.0),
        "cost_basis": cost_basis or "unpriced",
    }


def ingest_body(session_id, turn_index, vendor, model, turn, repo, branch, machine, surface, subagent_id=""):
    return {
        "source_type": source_type_name(vendor),
        "source_id": source_id_for(session_id, turn_index, subagent_id),
        "content": format_turn_markdown(vendor, model or turn.model, turn),
        "metadata": {
            "repo": repo or "",
            "branch": branch or "",
            "machine": machine,
            "surface": surface,
            # Empty when the transcript has no timestamp. A clock read would
            # change the body hash on every scan and repost the same turn.
            "timestamp": turn.timestamp or "",
        },
    }


def end_body(session_id, vendor, surface, machine, repo, branch):
    return {
        "session_id": session_id,
        "vendor": vendor_name(vendor),
        "surface": surface,
        "machine": machine,
        "repo": repo or "",
        "branch": branch or "",
    }


def _cwd_from_payload(payload):
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    roots = payload.get("workspace_roots")
    if isinstance(roots, str) and roots.strip():
        return roots.strip()
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str) and root.strip():
                return root.strip()
    return os.getcwd()


def _payload_prompt(payload):
    for key in ("prompt", "task", "last_prompt", "user_prompt"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _payload_output(payload):
    for key in ("output", "text", "response", "agent_message", "last_assistant_message", "last_output"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _payload_tools(payload):
    raw = payload.get("tool_calls") or payload.get("tools")
    if not isinstance(raw, list):
        return []
    calls = []
    for block in raw:
        if isinstance(block, str):
            calls.append({"id": "", "name": block, "input": {}, "result": ""})
            continue
        if not isinstance(block, dict):
            continue
        name = block.get("name") or block.get("tool")
        if not name:
            continue
        calls.append({
            "id": str(block.get("id") or ""),
            "name": str(name),
            "input": _bounded_input(block.get("input") if "input" in block else block.get("arguments")),
            "result": clip(_text_from_content(block.get("result") or block.get("output") or "")),
        })
    return calls


def _runtime_from_payload(payload):
    name = str(payload.get("hook_event_name") or "")
    if name in CURSOR_EVENTS:
        return "cursor"
    if ("transcriptPath" in payload or "conversationId" in payload) and "transcript_path" not in payload:
        return "gemini"
    return "claude"


def publish_turn(bridge, session_id, turn_index, turn, vendor, surface, model,
                 cwd, repo, branch, machine, cost, cost_basis, usage, turns,
                 started_at, include_ingest, include_summary=True, subagent_id="",
                 updated_at="", stage=1, usage_revision=0, summary_index=0):
    """Register, heartbeat, optional ingest, and optionally the session summary.

    The turn's own model wins over the session's first model. Prompt hooks pass
    include_summary=False and stage 0 so a later prompt cannot replace a real
    summary with empty usage. A completion uses stage 1 and drops a spooled
    prompt snapshot of the same turn. Sync publishes the summary once per file,
    not once per turn. updated_at is the transcript's last timestamp.
    turn.timestamp stays the prompt time and is what ingest metadata records.
    summary_index is only the delivery key for the session summary. The body
    turn count and the heartbeat stay on this generation's own turn.
    """
    model = turn.model or model or "unknown"
    stage = int(stage or 0)
    reg_id = "register:" + session_id
    bridge.send(
        "register", reg_id, register_body(
            session_id, machine, cwd, repo, branch, vendor, model, cost_basis),
        session_id=session_id, turn_index=0,
    )
    live_id = source_id_for(session_id, turn_index, subagent_id)
    beat = heartbeat_body(
        session_id, turn.prompt, turn.assistant, turn_index, cost, model, vendor, surface)
    bridge.send(
        "heartbeat", live_id, beat, session_id=session_id, turn_index=turn_index, stage=stage)
    bridge.send("turn", live_id, beat, session_id=session_id, turn_index=turn_index, stage=stage)
    if include_summary:
        updated = updated_at or turn.timestamp or started_at or ""
        bridge.send(
            "ai_sessions", "ai-sessions:" + session_id, ai_sessions_body(
                session_id, vendor, surface, usage, turns, repo, branch,
                started_at or turn.timestamp or "", updated, model, machine, cost, cost_basis),
            session_id=session_id,
            turn_index=int(summary_index or turns or turn_index or 0),
            stage=stage,
            usage_revision=int(usage_revision or 0),
        )
    if include_ingest and turn.has_assistant:
        bridge.send(
            "ingest", live_id, ingest_body(
                session_id, turn_index, vendor, model, turn, repo, branch, machine, surface,
                subagent_id),
            session_id=session_id, turn_index=turn_index,
        )
    return live_id


def _open_bridge(config, poster):
    return Bridge(config, poster=poster, store=Store(config.state_dir))


def process_hook(payload, config, poster=None, runtime=""):
    """Forward one hook payload. Always returns 0."""
    try:
        if not isinstance(payload, dict) or not config.enabled:
            return 0
        runtime = vendor_name(runtime) if _text(runtime) else _runtime_from_payload(payload)
        event = str(payload.get("hook_event_name") or "")
        bridge = _open_bridge(config, poster or post_json)
        try:
            _process_hook(bridge, payload, config, runtime, event)
        finally:
            bridge.close()
        if bridge.spooled:
            _warn(f"activity-bridge: spooled {bridge.spooled} post(s)")
        return 0
    except Exception as exc:  # noqa: BLE001 — a hook must not break the session
        _warn(f"activity-bridge: {type(exc).__name__}: {exc}")
        return 0


def _stream_transcript(path):
    """One pass over a JSONL file.

    Only the latest assistant turn is kept. Usage is summed as turns are
    discarded so a long sidechain cannot sit in memory twice.
    """
    state = _identity_state(path)
    usage = _empty_usage()
    count = 0
    last = None
    last_index = 0
    updated = ""
    usage_turns = []

    def observe(rec):
        _observe_identity(path, state, rec)

    codex_usage, codex_owner = _path_codex_choice(path)
    for index, turn in enumerate(
        iter_turns(
            _iter_records(path), on_record=observe,
            codex_usage=codex_usage, codex_owner=codex_owner,
        ),
        start=1,
    ):
        count = index
        for key in usage:
            usage[key] += int(turn.usage.get(key) or 0)
        if _any_usage(turn.usage):
            usage_turns.append(index)
        if turn.timestamp:
            updated = turn.timestamp
        if turn.has_assistant:
            last = turn
            last_index = index
    if not state["cwd"]:
        state["cwd"] = str(Path(path).parent)
    return state, usage, count, last, last_index, updated, usage_turns


def _coverage_turn(reader_turn, allocated_index, usage_turns, turn_count, stored_turn=0):
    """Transcript turn this snapshot already counts, or 0 when it does not.

    A stored association is authoritative: when that turn has no counters this
    snapshot does not cover the generation, even if the publication index does.
    Otherwise the reader's turn counts only when it is the end of this snapshot,
    so a trailing prompt cannot treat an earlier assistant turn as coverage.
    """
    covered = {int(index) for index in usage_turns or []}
    reader_turn = int(reader_turn or 0)
    allocated_index = int(allocated_index or 0)
    turn_count = int(turn_count or 0)
    stored_turn = int(stored_turn or 0)
    if stored_turn > 0:
        if stored_turn in covered and stored_turn <= turn_count:
            return stored_turn
        return 0
    if allocated_index in covered:
        return reader_turn if reader_turn in covered else allocated_index
    if reader_turn in covered and reader_turn == turn_count and allocated_index > turn_count:
        return reader_turn
    return 0


def _process_hook(bridge, payload, config, runtime, event):
    if event in END_EVENTS:
        _publish_end(bridge, payload, config, runtime)
        return
    session_id = clean_id(
        payload.get("conversation_id") or payload.get("session_id") or payload.get("conversationId"),
        "unknown",
    )
    agent_key = _agent_key(payload)
    transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    session_id, agent_key = _resolve_identity(transcript, session_id, agent_key)
    ledger_id = _ledger_id(session_id, agent_key)
    generation_id = clean_id(payload.get("generation_id") or event or "turn", "turn")
    cwd = _cwd_from_payload(payload)
    repo, branch = resolve_place(cwd, config)
    surface = config.surface or default_surface(runtime, transcript)
    vendor = vendor_name(config.vendor or runtime)
    model = _text(payload.get("model") or payload.get("subagent_model"))
    cost, basis = _cost(payload)
    machine = config.machine

    if event in PROMPT_EVENTS:
        prompt = _payload_prompt(payload)
        turn_index, seen = bridge.store.ensure_turn(
            session_id, agent_key, generation_id, utc_now())
        bridge.store.remember_started(session_id, agent_key, seen)
        stored = bridge.store.load_prompt(session_id, generation_id, agent_key)
        if stored and stored.get("prompt") and not prompt:
            prompt = stored["prompt"]
        bridge.store.save_prompt(
            session_id, generation_id, prompt, model, cwd, turn_index, agent_key)
        turn = Turn()
        turn.prompt = prompt
        turn.model = model
        turn.timestamp = seen or utc_now()
        # Prompt hooks must not publish a zero-usage session summary. The
        # matching completion (or the file sync) owns /api/ai-sessions.
        publish_turn(
            bridge, ledger_id, turn_index, turn, vendor, surface, model, cwd, repo, branch,
            machine, cost, basis, _payload_tokens(payload), turn_index, turn.timestamp,
            include_ingest=False, include_summary=False, stage=0,
        )
        return

    file_usage = _empty_usage()
    turn_count = 0
    started = ""
    updated_at = ""
    target = None
    turn_index = 0
    transcript_turn = 0
    usage_turns = []
    snapshot_accepted = False
    if isinstance(transcript, str) and transcript and Path(transcript).is_file():
        state, file_usage, turn_count, target, turn_index, _updated, usage_turns = (
            _stream_transcript(transcript)
        )
        if session_id == "unknown" and not _sidechain_parts(transcript)[1]:
            session_id = clean_id(state["session_id"], "unknown")
            session_id, agent_key = _resolve_identity(transcript, session_id, agent_key)
            ledger_id = _ledger_id(session_id, agent_key)
        if not model:
            model = state["model"]
        started = state["started"]
        # turn.timestamp is the prompt time. The summary clock is the last record.
        updated_at = state["updated"] or ""
        if turn_count:
            # A finished assistant turn is not an open prompt, even with no
            # counters yet. A shorter snapshot must not reopen an older turn.
            open_prompt = 0
            if turn_count not in usage_turns and int(turn_index or 0) != turn_count:
                open_prompt = turn_count
            snapshot_accepted = bridge.store.replace_transcript_usage(
                session_id, agent_key, file_usage, turn_count, usage_turns,
                open_prompt=open_prompt, apply_usage=bool(_any_usage(file_usage)),
            )
        if turn_index:
            transcript_turn = int(turn_index)
            turn_index = bridge.store.bind_turn(session_id, agent_key, generation_id, turn_index)
        if started:
            started = bridge.store.remember_started(session_id, agent_key, started)
        if state["cwd"] and not payload.get("cwd") and not payload.get("workspace_roots"):
            cwd = state["cwd"]
            repo, branch = resolve_place(cwd, config)

    if target is None:
        stored = bridge.store.load_prompt(session_id, generation_id, agent_key)
        prompt = _payload_prompt(payload) or (stored["prompt"] if stored else "")
        output = _payload_output(payload)
        tools = _payload_tools(payload)
        turn_index, seen = bridge.store.ensure_turn(session_id, agent_key, generation_id, "")
        if not seen:
            seen = utc_now()
            turn_index, seen = bridge.store.ensure_turn(
                session_id, agent_key, generation_id, seen)
        if stored and not model:
            model = stored["model"]
        if prompt:
            bridge.store.save_prompt(
                session_id, generation_id, prompt, model, cwd, turn_index, agent_key)
        if prompt or output or tools:
            target = Turn()
            target.prompt = prompt
            target.model = model
            target.timestamp = seen
            if output:
                target.assistant_parts.append(clip(output))
            target.tools.extend(tools)
    elif not target.prompt:
        stored = bridge.store.load_prompt(session_id, generation_id, agent_key)
        if stored and stored.get("prompt"):
            target.prompt = stored["prompt"]
    if target is None:
        return
    if not target.model:
        target.model = model
    payload_usage = _payload_tokens(payload)
    transcript_measured = bool(turn_count) and _any_usage(file_usage)
    cursor_payload = vendor == "cursor"
    if transcript_measured:
        # Remember a generation as covered only when its turn accounts for the
        # counters. A trailing prompt is inside the window but stays additive,
        # so a later payload retry can still restore usage the file never had.
        # Generations past the snapshot's turn count stay additive. The stored
        # counters stay raw; Cursor normalization happens when a generation is summed.
        # bind_turn will not lower an index allocated before this turn existed.
        # A rejected shorter snapshot must not set inclusion: its reader turn
        # can look covered while the retained baseline never counted this
        # generation. Remember the covering turn only when the snapshot is kept.
        stored_turn = bridge.store.transcript_turn_for(session_id, agent_key, generation_id)
        coverage_turn = _coverage_turn(
            transcript_turn, turn_index, usage_turns, turn_count, stored_turn)
        accepted = snapshot_accepted
        if accepted and coverage_turn:
            bridge.store.remember_transcript_turn(
                session_id, agent_key, generation_id, coverage_turn)
        bridge.store.save_generation_usage(
            session_id, generation_id, agent_key, payload_usage or _empty_usage(),
            included=bool(accepted and coverage_turn),
        )
    elif any(payload_usage.values()):
        bridge.store.save_generation_usage(session_id, generation_id, agent_key, payload_usage)
    # Usage and revision are one read. A retry that commits between two reads
    # would otherwise publish this hook's stale total at the retry's revision.
    # The empty-usage fallback stays on the path that stored no counters.
    measured = transcript_measured or any(payload_usage.values())
    usage, published_index, revision = bridge.store.publication_snapshot(
        session_id, agent_key, cursor=cursor_payload)
    if not measured and not _any_usage(usage):
        usage = file_usage if turn_count else sum_usage([target])
    started = bridge.store.remember_started(
        session_id, agent_key, started or target.timestamp or "")
    if not updated_at:
        updated_at = utc_now()
    count = turn_count or turn_index
    publish_turn(
        bridge, ledger_id, turn_index, target, vendor, surface, model,
        cwd, repo, branch, machine, cost, basis, usage, count, started or target.timestamp,
        include_ingest=target.has_assistant, include_summary=True, updated_at=updated_at,
        usage_revision=revision, summary_index=published_index,
    )


def _sidechain_parts(path):
    """`(parent, agent stem)` for `<parent>/subagents/agent-*.jsonl`, else empty."""
    if not path:
        return "", ""
    path = Path(path)
    parts = path.parts
    lowered = [part.lower() for part in parts]
    if "subagents" not in lowered:
        return "", ""
    index = lowered.index("subagents")
    parent = parts[index - 1] if index else ""
    return parent, path.stem


def _resolve_identity(path, session_id, agent_key):
    """Shared ledger identity for a live hook and a `sync_file` backfill.

    A sidechain path is always `{parent}:{agent stem}`. An explicit subagent id
    is used only when the transcript is not a sidechain file.
    """
    parent, agent = _sidechain_parts(path)
    if not agent:
        return session_id, agent_key
    parent_id = clean_id(parent, "") or session_id or "unknown"
    return clean_id(parent_id, "unknown"), clean_id(agent, "")


def _agent_key(payload):
    """Subagent id, or the sidechain transcript stem when the id is absent.

    Claude Code and Codex send SubagentStop in PascalCase and point
    transcript_path at `<session>/subagents/agent-*.jsonl` without a separate id.
    """
    if not isinstance(payload, dict):
        return ""
    for key in ("subagent_id", "agent_id", "agentId"):
        val = clean_id(payload.get(key) or "", "")
        if val and val != "unknown":
            return val
    transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    if not isinstance(transcript, str) or not transcript.strip():
        return ""
    path = Path(transcript)
    if "subagents" not in {part.lower() for part in path.parts}:
        return ""
    return clean_id(path.stem, "")


def _ledger_id(session_id, agent_key):
    if agent_key:
        return f"{session_id}:{agent_key}"
    return session_id


def _publish_end(bridge, payload, config, runtime):
    session_id = clean_id(
        payload.get("session_id") or payload.get("conversation_id") or payload.get("conversationId"),
        "unknown",
    )
    cwd = _cwd_from_payload(payload)
    repo, branch = resolve_place(cwd, config)
    surface = config.surface or default_surface(runtime, "")
    vendor = vendor_name(config.vendor or runtime)
    bridge.send("end", "end:" + session_id, end_body(
        session_id, vendor, surface, config.machine, repo, branch))


def iter_jsonl(root):
    root = Path(root)
    if root.is_file():
        if root.suffix == ".jsonl":
            yield root
        return
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if not name.startswith(".") and name not in {"node_modules", "__pycache__"}
        ]
        for name in sorted(filenames):
            if name.endswith(".jsonl"):
                yield Path(dirpath) / name


def sync_tree(root, config, poster=None, vendor="", surface="", flush_limit=50):
    """Forward new turns under `root`. Returns (delivered, spooled)."""
    if not config.enabled:
        return 0, 0
    bridge = _open_bridge(config, poster or post_json)
    try:
        bridge.flush(flush_limit)
        for path in iter_jsonl(root):
            sync_file(bridge, path, config, vendor=vendor, surface=surface)
        return bridge.delivered, bridge.spooled
    finally:
        bridge.close()


def sync_file(bridge, path, config, vendor="", surface=""):
    """Publish each turn's coord snapshot, one session summary, then ingests.

    The summary is one post per file scan. started_at is the first transcript
    timestamp and updated_at is the last, so a second scan of an unchanged file
    does not post /api/ai-sessions again. The summary goes out before ingest so
    a twin-api failure cannot skip it. Turns are clipped while they are read,
    so a large tool result is not retained.
    """
    path = Path(path)
    state = _identity_state(path)
    runtime = infer_runtime(path, vendor or config.vendor)
    surface = surface or config.surface or default_surface(runtime, path)

    def observe(rec):
        _observe_identity(path, state, rec)

    codex_usage, codex_owner = _path_codex_choice(path)
    turns = list(enumerate(
        iter_turns(
            _iter_records(path), on_record=observe,
            codex_usage=codex_usage, codex_owner=codex_owner,
        ),
        start=1,
    ))
    if not turns:
        return
    cwd = state["cwd"] or str(path.parent)
    repo, branch = resolve_place(cwd, config)
    usage = _empty_usage()
    for _index, turn in turns:
        for key in usage:
            usage[key] += int(turn.usage.get(key) or 0)
    session_id, agent_key = _resolve_identity(path, state["session_id"], "")
    session = _ledger_id(clean_id(session_id, path.stem), agent_key)
    usage_turns = [index for index, turn in turns if _any_usage(turn.usage)]
    last_index, last_turn = turns[-1]
    open_prompt = 0
    if last_turn.prompt and not last_turn.has_assistant and not _any_usage(last_turn.usage):
        open_prompt = last_index
    # Bounds and the baseline share one decision. A shorter file, including one
    # with no counters, must not reopen a turn the retained snapshot already passed.
    accepted = bridge.store.replace_transcript_usage(
        session_id, agent_key, usage, len(turns), usage_turns,
        open_prompt=open_prompt, apply_usage=bool(usage_turns),
    )
    # A file with no counters must not replace totals live hooks already stored.
    # Equal turn index and stage would otherwise let an all-zero summary win.
    # The total and its publication index are one read. A hook that lands
    # during the per-turn posts can publish a newer total at a higher index;
    # this scan must not reuse that index for the total it already captured.
    # A rejected shorter snapshot stays on the file length.
    started_at = bridge.store.remember_started(session_id, agent_key, state["started"] or "")
    outgoing, published_index, summary_revision = bridge.store.publication_snapshot(
        session_id, agent_key, cursor=vendor_name(runtime) == "cursor")
    summary_index = published_index if accepted else len(turns)
    live = [(index, turn) for index, turn in turns if turn.prompt or turn.has_assistant]
    if not live:
        return
    for index, turn in live:
        trailing_prompt = (index, turn) == live[-1] and not turn.has_assistant
        if not turn.has_assistant and not trailing_prompt:
            continue
        publish_turn(
            bridge, session, index, turn, runtime, surface,
            turn.model or state["model"], cwd, repo, branch, config.machine, 0.0, "unpriced",
            outgoing, len(turns), state["started"] or turn.timestamp,
            include_ingest=False, include_summary=False,
            stage=1 if turn.has_assistant else 0,
        )
    _index, last_turn = live[-1]
    bridge.send(
        "ai_sessions",
        "ai-sessions:" + session,
        ai_sessions_body(
            session, runtime, surface, outgoing, len(turns), repo, branch,
            started_at or state["started"] or "", state["updated"] or started_at or state["started"] or "",
            last_turn.model or state["model"] or "unknown", config.machine, 0.0, "unpriced",
        ),
        session_id=session,
        turn_index=summary_index,
        stage=1,
        usage_revision=summary_revision,
    )
    for index, turn in live:
        if not turn.has_assistant:
            continue
        live_id = source_id_for(session, index)
        bridge.send(
            "ingest", live_id, ingest_body(
                session, index, runtime, turn.model or state["model"], turn, repo, branch,
                config.machine, surface,
            ),
            session_id=session, turn_index=index,
        )


def emit_hook_stdout(mode, payload):
    if mode == "claude":
        return
    if mode == "cursor":
        sys.stdout.write("{}\n")
        return
    if not isinstance(payload, dict):
        return
    name = str(payload.get("hook_event_name") or "")
    antigravity = (
        ("transcriptPath" in payload or "conversationId" in payload)
        and "transcript_path" not in payload
    )
    if name in CURSOR_EVENTS or antigravity:
        sys.stdout.write("{}\n")


def _read_payload():
    try:
        raw = sys.stdin.buffer.read()
    except (AttributeError, OSError, ValueError):
        try:
            raw = sys.stdin.read().encode("utf-8", "replace")
        except (OSError, ValueError):
            return None
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def cmd_hook(args):
    payload = _read_payload()
    try:
        config = load_config()
        if config.enabled and payload is not None:
            process_hook(payload, config, runtime=args.runtime)
    except Exception as exc:  # noqa: BLE001 — hook stdout contract still has to run
        _warn(f"activity-bridge: {type(exc).__name__}: {exc}")
    emit_hook_stdout(args.stdout, payload)
    _flush_stdout()
    return 0


def cmd_sync(args, config):
    dirs = list(args.sync_dir or [])
    if not dirs:
        dirs = list(config.sync_dirs)
    if not dirs:
        _warn("activity-bridge: pass --sync-dir or set WIZZO_ACTIVITY_SYNC_DIRS")
        return 2
    if args.interval <= 0:
        _warn("activity-bridge: --interval must be > 0")
        return 2
    if not config.enabled:
        _warn("activity-bridge: not configured (set WIZZO_COORD_URL or WIZZO_TWIN_API_URL)")
        return 0
    while True:
        delivered = 0
        spooled = 0
        for directory in dirs:
            got_d, got_s = sync_tree(
                directory, config, vendor=args.vendor, surface=args.surface,
                flush_limit=args.flush_limit,
            )
            delivered += got_d
            spooled += got_s
        _warn(f"activity-bridge: delivered={delivered} spooled={spooled}")
        if not args.watch:
            return 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


def cmd_flush(args, config):
    if not config.enabled:
        _warn("activity-bridge: not configured")
        return 0
    bridge = _open_bridge(config, post_json)
    try:
        sent = bridge.flush(args.flush_limit)
        left = bridge.store.pending_count()
    finally:
        bridge.close()
    _warn(f"activity-bridge: flushed={sent} pending={left}")
    return 0


def cmd_end(args, config):
    session_id = clean_id(args.session_id, "")
    if not session_id:
        _warn("activity-bridge: end requires --session-id")
        return 2
    if not config.enabled:
        _warn("activity-bridge: not configured")
        return 0
    runtime = args.vendor or args.runtime or "cursor"
    payload = {
        "hook_event_name": "sessionEnd",
        "session_id": session_id,
        "cwd": os.getcwd(),
    }
    process_hook(payload, config, runtime=runtime)
    bridge = _open_bridge(config, post_json)
    try:
        bridge.flush(args.flush_limit)
    finally:
        bridge.close()
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="activity-bridge")
    parser.add_argument("command", nargs="?", choices=("hook", "sync", "flush", "end"))
    parser.add_argument("--sync-dir", action="append", default=[])
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--vendor", default="")
    parser.add_argument("--surface", default="")
    parser.add_argument("--runtime", default="")
    parser.add_argument("--stdout", choices=("auto", "cursor", "claude"), default="auto")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--flush-limit", type=int, default=50)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command is None and args.sync_dir:
        command = "sync"
    if command is None:
        parser.error("choose hook, sync, flush, or end")
    if command == "hook":
        return cmd_hook(args)
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        _warn(f"activity-bridge: {type(exc).__name__}: {exc}")
        return 0
    if command == "sync":
        return cmd_sync(args, config)
    if command == "flush":
        return cmd_flush(args, config)
    if command == "end":
        return cmd_end(args, config)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
