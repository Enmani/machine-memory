"""Adapters: one per agent. Each knows where its logs live and how to read them into Threads.

An adapter has `source`, `discover()` -> paths, and `load(path)` -> [Thread]. Only the human-readable
conversation is kept: user prompts and the assistant's words. Reasoning, tool calls and tool output,
compaction snapshots and system or developer prompts are dropped here, because they are most of
the bytes on disk and none of the memory. To add an agent, write a class like these and add it to
ADAPTERS.
"""
import glob
import json
import os
import re
import sqlite3

from .model import Thread, Turn

HOME = os.path.expanduser("~")

# Text an agent injects into a user turn that the user never wrote.
INJECTED = re.compile(
    r"^\s*(<(environment_context|model_switch|app-context|skills_instructions|user_instructions|permissions[ >]|"
    r"collaboration_mode|turn_aborted|system-reminder|system-notice|ide_[a-z_]+|local-command-[a-z]+|command-(name|message|args)|"
    r"task-notification|user-prompt-submit-hook|attached_files)\b|# AGENTS\.md instructions|# Context from my IDE|\[Request interrupted)",
    re.I)


def clean_user_text(parts):
    kept = [p.strip() for p in parts if isinstance(p, str) and p.strip() and not INJECTED.match(p)]
    return "\n".join(kept)


def lines(path):
    with open(path, "rb") as handle:
        yield from handle


def load_json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


class CodexAdapter:
    """~/.codex/sessions/**/rollout-*.jsonl. The session_meta line names the thread, its working
    directory and, for subagents, the parent. Titles come from Codex's own state database."""
    source = "codex"
    MESSAGE = b'"type":"response_item","payload":{"type":"message"'

    def __init__(self, home=HOME):
        self.home = home
        self.titles = None

    def discover(self):
        found = []
        for folder in ("sessions", "archived_sessions"):
            found += glob.glob(os.path.join(self.home, ".codex", folder, "**", "rollout-*.jsonl"), recursive=True)
        return sorted(found, key=os.path.basename)

    def _titles(self):
        if self.titles is None:
            self.titles = {}
            db = os.path.join(self.home, ".codex", "state_5.sqlite")
            if os.path.exists(db):
                try:
                    with sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True) as conn:
                        self.titles = {row[0]: row[1] or "" for row in conn.execute("select id, title from threads")}
                except sqlite3.Error:
                    pass
        return self.titles

    def load(self, path):
        thread = None
        for raw in lines(path):
            head = raw[:200]
            if thread is None:
                meta = load_json(raw)
                if not meta or meta.get("type") != "session_meta":
                    return []
                payload = meta["payload"]
                source = payload.get("source")
                thread = Thread(source="codex", id=payload["id"], path=path, cwd=payload.get("cwd", ""),
                                started=payload.get("timestamp") or meta.get("timestamp", ""),
                                subagent=isinstance(source, dict) and "subagent" in source,
                                parent=payload.get("parent_thread_id"))
                thread.title = self._titles().get(thread.id, "")
                continue
            if self.MESSAGE not in head:
                continue  # compaction snapshots, tool output, reasoning: skipped without being parsed
            record = load_json(raw)
            payload = (record or {}).get("payload") or {}
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            items = payload.get("content") or []
            if role == "user":
                text = clean_user_text([i.get("text") for i in items if i.get("type") == "input_text"])
            else:
                text = "\n".join(i.get("text", "") for i in items if i.get("type") == "output_text").strip()
            if text:
                thread.turns.append(Turn(role, text, record.get("timestamp", ""), final=payload.get("phase") == "final_answer"))
        if thread is None or not thread.turns:
            return []
        thread.ended = thread.turns[-1].at
        thread.started = thread.started or thread.turns[0].at
        return [thread]


class ClaudeCodeAdapter:
    """~/.claude/projects/*/*.jsonl, which also holds the Claude desktop app's code sessions. A resumed
    session repeats earlier entries under the same uuid, so uuids are tracked across files."""
    source = "claude"

    def __init__(self, home=HOME):
        self.home = home
        self.seen = set()

    def discover(self):
        found = glob.glob(os.path.join(self.home, ".claude", "projects", "*", "*.jsonl"))
        return sorted(found, key=lambda p: (os.path.getmtime(p), p))

    @staticmethod
    def blocks(content):
        return [content] if isinstance(content, str) else [b for b in content or [] if isinstance(b, dict)]

    def load(self, path):
        thread = Thread(source="claude", id=os.path.splitext(os.path.basename(path))[0], path=path)
        for raw in lines(path):
            head = raw[:600]
            if b'"type":"user"' not in head and b'"type":"assistant"' not in head and b'"custom-title"' not in head:
                continue
            record = load_json(raw)
            if not record:
                continue
            kind = record.get("type")
            if kind == "custom-title":
                thread.title = record.get("customTitle") or thread.title
                continue
            if kind not in ("user", "assistant") or record.get("isSidechain") or record.get("isMeta"):
                continue
            if (record.get("origin") or {}).get("kind") not in (None, "human") and kind == "user":
                continue
            uuid = record.get("uuid")
            if uuid in self.seen:
                continue
            self.seen.add(uuid)
            thread.cwd = thread.cwd or record.get("cwd", "")
            blocks = self.blocks((record.get("message") or {}).get("content"))
            if kind == "user":
                text = clean_user_text([b if isinstance(b, str) else b.get("text") for b in blocks if isinstance(b, str) or b.get("type") == "text"])
            else:
                text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            if text:
                thread.turns.append(Turn(kind, text, record.get("timestamp", "")))
        if not thread.turns:
            return []
        thread.started, thread.ended = thread.turns[0].at, thread.turns[-1].at
        return [thread]


class OmpAdapter:
    """~/.omp/agent/sessions/**/*.jsonl (oh-my-pi), a tree of entries with message records."""
    source = "omp"

    def __init__(self, home=HOME):
        self.home = home

    def discover(self):
        return sorted(glob.glob(os.path.join(self.home, ".omp", "agent", "sessions", "**", "*.jsonl"), recursive=True))

    def load(self, path):
        thread = Thread(source="omp", id="", path=path)
        for raw in lines(path):
            record = load_json(raw)
            if not record:
                continue
            kind = record.get("type")
            if kind == "session":
                thread.id, thread.cwd, thread.started = record.get("id", ""), record.get("cwd", ""), record.get("timestamp", "")
                thread.title = record.get("title") or ""
            elif kind in ("title", "title_change"):
                thread.title = record.get("title") or thread.title
            elif kind == "message":
                message = record.get("message") or {}
                role = message.get("role")
                if role not in ("user", "assistant"):
                    continue
                parts = [b.get("text") for b in message.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
                text = clean_user_text(parts) if role == "user" else "\n".join(p for p in parts if p).strip()
                if text:
                    thread.turns.append(Turn(role, text, record.get("timestamp", "")))
        if not thread.id or not thread.turns:
            return []
        thread.ended = thread.turns[-1].at
        return [thread]


ADAPTERS = {"codex": CodexAdapter, "claude": ClaudeCodeAdapter, "omp": OmpAdapter}
