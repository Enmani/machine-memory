"""A project's history: the global memory's notes about it, as a memory of its own.

Each project gets an ordinary OptMem store (LOG.txt and TREE/) holding the notes tagged `[project]`. Agents never
write there: they note once, into the global memory, and the notes are routed here by `sync`. The store is a
derived view, so it is rebuilt whenever the global log is. Because it is an ordinary store, wake, zoom and recall
work on it unchanged, with the whole reading budget spent on this one project.
"""
import hashlib
import json
import os
import re

from memolib import store as S

from . import registry

TAG = re.compile(r"\[([^\]\n]{1,40})\]")
PROJECT_WAKE_LINES = 64


def memory_path(name):
    return os.path.join(registry.home(name), "memory")


def init(name):
    d = memory_path(name)
    os.makedirs(os.path.join(d, "TREE"), exist_ok=True)
    open(S.log_path(d), "a").close()
    if not os.path.exists(os.path.join(d, "config")):
        S.write_config(d, {"WAKE_LINES": PROJECT_WAKE_LINES})
    return d


def _state_file(name):
    return os.path.join(registry.home(name), "routing.json")


def _fingerprint(global_dir):
    return hashlib.sha1(S.log_get(global_dir, 0)[2].encode("utf-8")).hexdigest()[:12] if S.log_len(global_dir) else ""


def route(name, entry, global_dir):
    """Copy the global notes tagged for this project that have not been copied yet. Returns (added, rebuilt)."""
    d, tags = init(name), registry.names_of(name, entry)
    state = {"routed": 0, "fingerprint": ""}
    if os.path.exists(_state_file(name)):
        with open(_state_file(name), encoding="utf-8") as handle:
            state = json.load(handle)
    total, rebuilt = S.log_len(global_dir), False
    if state["routed"] > total or (state["routed"] and state["fingerprint"] != _fingerprint(global_dir)):
        # the global log was rebuilt under us: the copy is stale, and positions are identity, so start again
        for path in (S.log_path(d), *(os.path.join(d, "TREE", f) for f in os.listdir(os.path.join(d, "TREE")))):
            os.remove(path)
        open(S.log_path(d), "a").close()
        state, rebuilt = {"routed": 0, "fingerprint": ""}, True
    items = []
    for _, date, text in S.log_slice(global_dir, state["routed"], total) if total > state["routed"] else []:
        tag = TAG.match(text)
        if tag and tag.group(1).casefold() in tags:
            items.append((date, text))
    if items:
        S.log_append(d, items)
    os.makedirs(registry.home(name), exist_ok=True)
    with open(_state_file(name), "w", encoding="utf-8") as handle:
        json.dump({"routed": total, "fingerprint": _fingerprint(global_dir)}, handle)
    return len(items), rebuilt


def notes(name):
    """All of the project's notes as (id, date, text)."""
    d = memory_path(name)
    return list(S.log_scan(d)) if os.path.isdir(d) and S.log_len(d) else []
